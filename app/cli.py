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
import sys
from app.config import system_config
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


def gen_espnow_secrets():
    """Generates random shared secret for ESP-NOW and Pi gateway."""
    raw_key = secrets.token_hex(16)
    print("\n" + "=" * 65)
    print(" ESP-NOW & SERIAL HMAC-SHA256 SHARED SECRET GENERATOR (v1.1)")
    print("=" * 65)
    print(f" Shared Key: {raw_key}\n")
    print(" [1] Add to /var/lib/smart-water/settings.yaml (or config.yaml):")
    print(" garden_gateway:")
    print(f'   shared_key: "{raw_key}"\n')
    print(" [2] Add to firmware/node/include/secrets.h & gateway/include/secrets.h:")
    print(f' #define ESPNOW_SHARED_KEY "{raw_key}"')
    print("=" * 65 + "\n")


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
        print("Radxa ROCK Pi S Smart Water Controller v1.1.0")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
