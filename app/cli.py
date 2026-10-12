"""
Command-Line Utility for Radxa ROCK Pi S Controller.
Usage:
    python -m app.cli rotate-token
    python -m app.cli version
"""

import sys
import os
from pathlib import Path
import secrets
import shutil
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


def gen_espnow_secrets() -> None:
    """
    Generates cryptographically random PMK (16 bytes hex), LMK (16 bytes hex),
    and shared key (32 bytes hex), writes /etc/smart-water/espnow.key (mode 0600)
    and prints #define lines to paste into firmware secrets.h (Part 2.2).
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


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m app.cli [rotate-token|restore <backup.db>|gen-espnow-secrets|version]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "rotate-token":
        new_token = security_manager.rotate_token()
        print("\n" + "=" * 60)
        print(" TOKEN ROTATED SUCCESSFULLY:")
        print(f" New Token: {new_token}")
        print(" Update your API clients with this new Bearer token.")
        print("=" * 60 + "\n")
    elif cmd == "restore":
        if len(sys.argv) < 3:
            print("Error: Missing backup file path. Usage: python -m app.cli restore <backup.db>")
            sys.exit(1)
        success = restore_backup(sys.argv[2])
        sys.exit(0 if success else 1)
    elif cmd == "gen-espnow-secrets":
        gen_espnow_secrets()
    elif cmd == "version":
        print(f"Radxa ROCK Pi S Smart Water Controller v{__version__}")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
