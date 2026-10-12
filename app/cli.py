"""
Command-Line Utility for Radxa ROCK Pi S Smart Water Controller.
Usage:
    python -m app.cli rotate-token
    python -m app.cli restore <backup.db>
    python -m app.cli backup [--keep-days 7]
    python -m app.cli update --from <tarball> [--sha256 <hash>] [--service <name>] [--health-url <url>]
    python -m app.cli gen-espnow-secrets
    python -m app.cli version
"""

import argparse
import asyncio
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from typing import Optional
import urllib.error
import urllib.request

from app import __version__
from app.core.security import security_manager
from app.core.state_manager import state_manager


def restore_backup(backup_path_str: str) -> bool:
    """Safely restores SQLite database from backup file with header validation."""
    src = Path(backup_path_str)
    if not src.exists():
        print(f"Error: Backup file {src} does not exist.")
        return False

    # Validate SQLite magic header
    try:
        with open(src, "rb") as f:
            header = f.read(16)
            if header != b"SQLite format 3\x00":
                print("Error: Target file does not have a valid SQLite database header.")
                return False
    except Exception as exc:
        print(f"Error reading backup file: {exc}")
        return False

    dest = state_manager.db_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest.with_suffix(".restore.tmp")

    try:
        shutil.copyfile(src, temp_dest)
        try:
            os.chmod(temp_dest, 0o600)
        except OSError:
            pass
        temp_dest.replace(dest)
        print(f"Database successfully restored from {src} to {dest}.")
        return True
    except Exception as exc:
        print(f"Failed to restore database: {exc}")
        if temp_dest.exists():
            try:
                temp_dest.unlink()
            except OSError:
                pass
        return False


def backup_database(keep_days: Optional[int] = None) -> bool:
    """Runs on-demand atomic VACUUM INTO backup with rotation."""
    print("Initiating atomic SQLite database backup...")
    try:
        bkp = asyncio.run(state_manager.run_nightly_backup(keep_days=keep_days))
        if bkp and bkp.exists():
            print(f"Database backup created successfully: {bkp}")
            return True
        else:
            print("Database backup failed: No backup path returned.")
            return False
    except Exception as exc:
        print(f"Database backup encountered error: {exc}")
        return False


def gen_espnow_secrets() -> None:
    """
    Generates cryptographically random PMK (16 bytes hex), LMK (16 bytes hex),
    and shared key (32 bytes hex), writes /etc/smart-water/espnow.key (mode 0600)
    and prints #define lines to paste into firmware secrets.h.
    """
    pmk = secrets.token_hex(16)
    lmk = secrets.token_hex(16)
    shared_key = secrets.token_hex(32)

    # 1. Attempt writing to /etc/smart-water/espnow.key (0600)
    target_paths = [
        Path("/etc/smart-water/espnow.key"),
        Path(os.getenv("SMART_WATER_DATA_DIR", "/var/lib/smart-water")) / "espnow.key",
    ]
    written_path = None
    for p in target_paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"ESPNOW_PMK={pmk}\n")
                f.write(f"ESPNOW_LMK={lmk}\n")
                f.write(f"ESPNOW_SHARED_KEY={shared_key}\n")
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            written_path = p
            break
        except OSError:
            continue

    print("\n" + "=" * 70)
    print(" ESP-NOW & SERIAL CRYPTOGRAPHIC SECRETS GENERATOR (v1.1)")
    print("=" * 70)
    if written_path:
        print(f" Secrets written to: {written_path} (mode 0600)")
    else:
        print(" Note: Could not write to /etc/smart-water/espnow.key (permission denied).")

    print("\n Paste the following into /var/lib/smart-water/settings.yaml:")
    print(" garden_gateway:")
    print("   enabled: true")
    print(f'   shared_key: "{shared_key}"')

    print("\n Paste the following into firmware/node/include/secrets.h")
    print(" and firmware/gateway/include/secrets.h:")
    print("-" * 70)
    print("#pragma once")
    print(f'#define ESPNOW_PMK "{pmk}"')
    print(f'#define ESPNOW_LMK "{lmk}"')
    print(f'#define ESPNOW_SHARED_KEY "{shared_key}"')
    print("-" * 70)
    print(" Keep secrets.h private and NEVER commit to source control.")
    print("=" * 70 + "\n")


def switch_symlink(link_path: Path, new_target: Path) -> bool:
    """Atomically or safely updates symlink to point to new_target."""
    try:
        # If link_path is a directory but not a symlink/junction, back it up
        if link_path.exists() and not link_path.is_symlink() and link_path.is_dir() and link_path.resolve() == link_path:
            legacy_bak = link_path.with_name(f"{link_path.name}.bak.{int(time.time())}")
            link_path.rename(legacy_bak)

        tmp_link = link_path.with_name(f"{link_path.name}.tmp.{secrets.token_hex(4)}")
        if tmp_link.is_symlink() or tmp_link.exists():
            tmp_link.unlink()

        try:
            tmp_link.symlink_to(new_target)
            os.replace(tmp_link, link_path)
            return True
        except OSError:
            # Fallback for systems where symlinks need elevated privileges (e.g. Windows non-admin)
            if os.name == "nt":
                import _winapi
                if link_path.exists():
                    link_path.unlink()
                _winapi.CreateJunction(str(new_target.resolve()), str(link_path.resolve()))
                return True
            else:
                if link_path.is_symlink() or link_path.exists():
                    if link_path.is_dir() and not link_path.is_symlink():
                        shutil.rmtree(link_path)
                    else:
                        link_path.unlink()
                link_path.symlink_to(new_target)
                return True
    except Exception as exc:
        print(f"Symlink creation failed ({link_path} -> {new_target}): {exc}")
        return False


def update_app(
    tarball_path_str: str,
    expected_sha256: Optional[str] = None,
    service_name: str = "smart-water.service",
    health_url: str = "http://127.0.0.1:8000/healthz",
    timeout_s: float = 20.0,
    install_dir_str: Optional[str] = None,
    slots_dir_str: Optional[str] = None,
    skip_health_check: bool = False,
) -> bool:
    """
    Executes a safe A/B update with SHA-256 verification and automatic rollback.
    1. Validates tarball presence and verifies SHA-256 integrity.
    2. Determines inactive A/B slot (/opt/smart-water-a or /opt/smart-water-b).
    3. Safely unpacks tarball to target slot with path traversal protection.
    4. Preserves Python virtual environment if available.
    5. Atomically flips /opt/smart-water symlink to target slot.
    6. Restarts systemd service.
    7. Polls health check endpoint (/healthz); automatically reverts if failed.
    """
    tarball = Path(tarball_path_str)
    if not tarball.is_file():
        print(f"ERROR: Update tarball '{tarball}' does not exist or is not a file.")
        return False

    # Check for adjacent .sha256 file if expected_sha256 was not explicitly passed
    if not expected_sha256:
        checksum_cand = tarball.with_suffix(tarball.suffix + ".sha256")
        if not checksum_cand.exists():
            checksum_cand = tarball.with_suffix(".sha256")
        if checksum_cand.exists():
            try:
                with open(checksum_cand, "r", encoding="utf-8") as cf:
                    first_tok = cf.readline().strip().split()
                    if first_tok:
                        expected_sha256 = first_tok[0]
                        print(f"Read expected SHA-256 from {checksum_cand}: {expected_sha256}")
            except Exception as read_err:
                print(f"Warning: Could not read adjacent checksum file: {read_err}")

    # Compute actual SHA-256 hash
    hasher = hashlib.sha256()
    try:
        with open(tarball, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
    except Exception as exc:
        print(f"ERROR reading tarball: {exc}")
        return False

    actual_sha256 = hasher.hexdigest().lower()
    print(f"Calculated Tarball SHA-256: {actual_sha256}")

    if expected_sha256:
        expected_clean = expected_sha256.strip().lower()
        if actual_sha256 != expected_clean:
            print("=" * 65)
            print("CRITICAL SECURITY ERROR: SHA-256 Checksum Mismatch!")
            print(f"  Expected: {expected_clean}")
            print(f"  Actual:   {actual_sha256}")
            print("Update aborted to prevent executing corrupted or untrusted code.")
            print("=" * 65)
            return False
        print("[OK] SHA-256 checksum verified successfully.")

    # Slot Directories Setup
    install_dir = Path(install_dir_str or os.getenv("SMART_WATER_INSTALL_DIR", "/opt/smart-water"))
    slots_dir = Path(slots_dir_str or os.getenv("SMART_WATER_SLOTS_DIR", str(install_dir.parent)))
    slot_a = slots_dir / (install_dir.name + "-a")
    slot_b = slots_dir / (install_dir.name + "-b")

    # Detect currently active slot
    active_slot: Optional[Path] = None
    if install_dir.is_symlink() or (install_dir.exists() and install_dir.resolve() != install_dir):
        active_target = install_dir.resolve()
        if active_target == slot_a.resolve() or active_target.name.endswith("-a"):
            active_slot = slot_a
            target_slot = slot_b
        elif active_target == slot_b.resolve() or active_target.name.endswith("-b"):
            active_slot = slot_b
            target_slot = slot_a
        else:
            active_slot = active_target
            target_slot = slot_a
    elif install_dir.exists() and install_dir.is_dir():
        # Legacy direct directory installation
        if not slot_a.exists():
            print(f"Migrating current active directory {install_dir} to slot {slot_a}...")
            shutil.copytree(install_dir, slot_a, symlinks=True)
        active_slot = slot_a
        target_slot = slot_b
    else:
        active_slot = None
        target_slot = slot_a

    print(f"Active Slot: {active_slot if active_slot else '(Initial deployment)'}")
    print(f"Target Slot: {target_slot}")

    # Prepare target slot
    if target_slot.exists():
        shutil.rmtree(target_slot, ignore_errors=True)
    target_slot.mkdir(parents=True, exist_ok=True)

    # Extract tarball safely with directory traversal check
    print(f"Extracting tarball into {target_slot}...")
    try:
        with tarfile.open(tarball, "r:*") as tar:
            target_resolved = target_slot.resolve()
            for member in tar.getmembers():
                dest_path = (target_slot / member.name).resolve()
                try:
                    dest_path.relative_to(target_resolved)
                except ValueError:
                    print(f"SECURITY ERROR: Tarball member '{member.name}' resolves outside target directory!")
                    shutil.rmtree(target_slot, ignore_errors=True)
                    return False
                if member.issym() or member.islnk():
                    if member.linkname.startswith("/") or ".." in member.linkname:
                        link_target = (target_slot / member.linkname).resolve()
                        try:
                            link_target.relative_to(target_resolved)
                        except ValueError:
                            print(f"SECURITY ERROR: Tarball symlink '{member.name}' points outside target directory!")
                            shutil.rmtree(target_slot, ignore_errors=True)
                            return False
            tar.extractall(path=target_slot)
    except Exception as exc:
        print(f"ERROR: Failed to extract tarball: {exc}")
        shutil.rmtree(target_slot, ignore_errors=True)
        return False

    # Preserve virtualenv from previous active slot if target slot does not contain one
    if not (target_slot / "venv").exists() and active_slot and (active_slot / "venv").exists():
        print("Preserving existing Python virtual environment from active slot...")
        try:
            shutil.copytree(active_slot / "venv", target_slot / "venv", symlinks=True)
        except Exception as v_err:
            print(f"Warning: Could not copy virtual environment: {v_err}")

    # Switch symlink to target slot
    print(f"Switching active symlink: {install_dir} -> {target_slot}...")
    if not switch_symlink(install_dir, target_slot):
        print("ERROR: Failed to switch active symlink.")
        return False

    # Restart systemd service if available
    has_systemctl = bool(shutil.which("systemctl"))
    if has_systemctl:
        print(f"Restarting service {service_name}...")
        res = subprocess.run(["systemctl", "restart", service_name], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"ERROR: Service restart failed (code {res.returncode}): {res.stderr}")
            print("Initiating immediate rollback...")
            if active_slot and active_slot.exists():
                switch_symlink(install_dir, active_slot)
                subprocess.run(["systemctl", "restart", service_name], capture_output=True)
                print(f"Rollback complete: Reverted to {active_slot}.")
            return False
    else:
        print("[INFO] systemctl not detected; skipping service restart.")

    # Post-update health check
    if not skip_health_check:
        print(f"Performing health check against {health_url} (timeout: {timeout_s}s)...")
        start_poll = time.time()
        healthy = False
        while time.time() - start_poll < timeout_s:
            try:
                req = urllib.request.Request(
                    health_url,
                    headers={"User-Agent": "smart-water-updater"},
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                pass
            time.sleep(1.0)

        if not healthy:
            print("=" * 65)
            print(f"ERROR: Health check against {health_url} timed out or failed!")
            print("AUTOMATIC ROLLBACK INITIATED")
            print("=" * 65)
            if active_slot and active_slot.exists():
                print(f"Reverting symlink {install_dir} -> {active_slot}...")
                switch_symlink(install_dir, active_slot)
                if has_systemctl:
                    print(f"Restarting service {service_name} on previous slot...")
                    subprocess.run(["systemctl", "restart", service_name], capture_output=True)
                print(f"Rollback complete: Restored operational version in {active_slot}.")
            else:
                print("No previous slot available for rollback.")
            return False

    print("=" * 65)
    print(" UPDATE SUCCESSFUL!")
    print(f" Active slot is now: {target_slot}")
    print("=" * 65)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Command-Line Management Utility for Radxa ROCK Pi S Smart Water Controller",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # rotate-token
    subparsers.add_parser("rotate-token", help="Rotate API authentication Bearer token")

    # restore
    restore_parser = subparsers.add_parser("restore", help="Safely restore database from backup file")
    restore_parser.add_argument("backup_file", help="Path to SQLite backup .db file")

    # backup
    backup_parser = subparsers.add_parser("backup", help="Run atomic SQLite VACUUM INTO backup and prune rotation")
    backup_parser.add_argument("--keep-days", type=int, default=None, help="Retention window in days (default: 7)")

    # gen-espnow-secrets
    subparsers.add_parser("gen-espnow-secrets", help="Generate ESP-NOW & serial cryptographic secrets")

    # version
    subparsers.add_parser("version", help="Print controller version")

    # update
    update_parser = subparsers.add_parser("update", help="Safe A/B update with checksum verification & rollback")
    update_parser.add_argument("--from", dest="from_tarball", required=True, help="Path to update tarball (.tar.gz)")
    update_parser.add_argument("--sha256", dest="sha256", default=None, help="Expected SHA-256 checksum hex string")
    update_parser.add_argument("--service", dest="service_name", default="smart-water.service", help="Systemd service name")
    update_parser.add_argument("--health-url", dest="health_url", default="http://127.0.0.1:8000/healthz", help="Health check URL")
    update_parser.add_argument("--timeout", dest="timeout", type=float, default=20.0, help="Health check timeout in seconds")
    update_parser.add_argument("--install-dir", dest="install_dir", default=None, help="Base installation symlink path")
    update_parser.add_argument("--slots-dir", dest="slots_dir", default=None, help="Slots directory parent path")
    update_parser.add_argument("--skip-health-check", action="store_true", help="Skip post-update HTTP health check")

    # token management
    token_parser = subparsers.add_parser("token", help="Issue, revoke, and list named API tokens")
    token_sub = token_parser.add_subparsers(dest="token_action", help="Token actions")

    # token issue
    issue_parser = token_sub.add_parser("issue", help="Issue a new named API Bearer token")
    issue_parser.add_argument("--name", required=True, help="Descriptive name for the token (e.g. tablet-wall, monitoring)")
    issue_parser.add_argument("--role", choices=["operator", "viewer"], default="operator", help="Access role: operator (control) or viewer (read-only)")
    issue_parser.add_argument("--days", type=int, default=None, help="Token validity window in days (default: permanent)")

    # token revoke
    revoke_parser = token_sub.add_parser("revoke", help="Revoke an existing token by name")
    revoke_parser.add_argument("--name", required=True, help="Name of token to revoke")

    # token list
    token_sub.add_parser("list", help="List all issued tokens and their status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmd = args.command.lower()
    if cmd == "rotate-token":
        new_token = security_manager.rotate_token()
        print("\n" + "=" * 60)
        print(" TOKEN ROTATED SUCCESSFULLY:")
        print(f" New Token: {new_token}")
        print(" Update your API clients with this new Bearer token.")
        print("=" * 60 + "\n")
    elif cmd == "token":
        action = getattr(args, "token_action", None)
        if action == "issue":
            try:
                raw_token, rec = security_manager.issue_token(name=args.name, role=args.role, days=args.days)
                print("\n" + "=" * 68)
                print(f" TOKEN ISSUED SUCCESSFULLY: [{rec.name.upper()}]")
                print("=" * 68)
                print(f" Token Name: {rec.name}")
                print(f" Role:       {rec.role.upper()} ({'Full Control' if rec.role == 'operator' else 'Read-Only'})")
                print(f" Created:    {rec.created_at}")
                print(f" Expires:    {rec.expires_at if rec.expires_at else 'Never'}")
                print("-" * 68)
                print(f" Secret Bearer Token: {raw_token}")
                print("-" * 68)
                print(" IMPORTANT: Save this secret token now. It is hashed with SHA-256")
                print(" and will NEVER be displayed again.")
                print("=" * 68 + "\n")
                sys.exit(0)
            except Exception as e:
                print(f"Error issuing token: {e}")
                sys.exit(1)
        elif action == "revoke":
            success = security_manager.revoke_token(args.name)
            if success:
                print(f"Token '{args.name}' has been successfully revoked.")
                sys.exit(0)
            else:
                print(f"Error: Token '{args.name}' not found.")
                sys.exit(1)
        elif action == "list":
            tokens = security_manager.list_tokens()
            print("\n" + "=" * 80)
            print(" REGISTERED API AUTHENTICATION TOKENS")
            print("=" * 80)
            print(f"{'NAME':<22} {'ROLE':<10} {'STATUS':<10} {'EXPIRES':<25} {'CREATED':<20}")
            print("-" * 80)
            if not tokens:
                print(" (No tokens registered)")
            for t in tokens:
                status_str = "REVOKED" if t.revoked else ("EXPIRED" if not t.is_valid() else "ACTIVE")
                exp_str = t.expires_at if t.expires_at else "Never"
                print(f"{t.name:<22} {t.role:<10} {status_str:<10} {exp_str:<25} {t.created_at[:19]:<20}")
            print("=" * 80 + "\n")
            sys.exit(0)
        else:
            token_parser.print_help()
            sys.exit(1)
    elif cmd == "restore":
        success = restore_backup(args.backup_file)
        sys.exit(0 if success else 1)
    elif cmd == "backup":
        success = backup_database(keep_days=args.keep_days)
        sys.exit(0 if success else 1)
    elif cmd == "gen-espnow-secrets":
        gen_espnow_secrets()
    elif cmd == "version":
        print(f"Radxa ROCK Pi S Smart Water Controller v{__version__}")
    elif cmd == "update":
        success = update_app(
            tarball_path_str=args.from_tarball,
            expected_sha256=args.sha256,
            service_name=args.service_name,
            health_url=args.health_url,
            timeout_s=args.timeout,
            install_dir_str=args.install_dir,
            slots_dir_str=args.slots_dir,
            skip_health_check=args.skip_health_check,
        )
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
