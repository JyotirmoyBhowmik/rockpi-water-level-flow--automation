"""
Command-Line Utility for Radxa ROCK Pi S Controller.
Usage:
    python -m app.cli rotate-token
    python -m app.cli version
"""

import sys
from app.config import system_config
from app.core.security import security_manager


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m app.cli [rotate-token|version]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "rotate-token":
        new_token = security_manager.rotate_token()
        print("\n" + "=" * 60)
        print(" TOKEN ROTATED SUCCESSFULLY:")
        print(f" New Token: {new_token}")
        print(" Update your API clients with this new Bearer token.")
        print("=" * 60 + "\n")
    elif cmd == "version":
        print("Radxa ROCK Pi S Smart Water Controller v1.0.0")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
