"""
API Security & Bearer Token Authentication for Radxa ROCK Pi S Controller.
Stores only cryptographic SHA-256 hashes of tokens.
Prints raw tokens exactly ONCE on initial generation or rotation.
"""

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple
from app.config import system_config

logger = logging.getLogger("smart_water.security")


class SecurityManager:
    """Manages API token hashing, verification, and rotation."""

    def __init__(self, auth_file_path: Optional[str] = None):
        self.auth_file_path = Path(auth_file_path or system_config.security.auth_token_file)

    def _hash_token(self, raw_token: str) -> str:
        """Computes SHA-256 hash of a raw token."""
        return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()

    def get_or_create_token(self) -> Tuple[Optional[str], str]:
        """
        Loads the stored token hash. If none exists, generates a new token,
        persists its hash, and returns (raw_token, token_hash).
        If already exists, returns (None, token_hash).
        """
        if self.auth_file_path.exists():
            try:
                with open(self.auth_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return None, data["token_hash"]
            except Exception as e:
                logger.error("Error reading auth file %s: %s", self.auth_file_path, e)

        # Generate fresh secure random token
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw_token)
        self._save_token_hash(token_hash)

        print("\n" + "=" * 72)
        print(" [SECURITY NOTICE] A NEW SMART-WATER API BEARER TOKEN HAS BEEN GENERATED:")
        print(f" Token: {raw_token}")
        print(" Save this token now. It is NEVER stored in plaintext and will not be shown again.")
        print("=" * 72 + "\n")

        return raw_token, token_hash

    def rotate_token(self) -> str:
        """Rotates the API token, writes new hash to disk, and returns the new raw token."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw_token)
        self._save_token_hash(token_hash)
        logger.warning("API Bearer token was rotated.")
        return raw_token

    def _save_token_hash(self, token_hash: str) -> None:
        """Atomically saves the token hash to disk."""
        self.auth_file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.auth_file_path.with_suffix(".tmp")
        payload = {
            "token_hash": token_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        temp_path.replace(self.auth_file_path)
        try:
            os.chmod(self.auth_file_path, 0o600)
        except OSError:
            pass

    def verify_token(self, provided_token: Optional[str]) -> bool:
        """Verifies provided Bearer token against stored hash in constant time."""
        if not provided_token:
            return False
        if not self.auth_file_path.exists():
            return False

        try:
            with open(self.auth_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                expected_hash = data.get("token_hash", "")
            provided_hash = self._hash_token(provided_token)
            return hmac.compare_digest(provided_hash, expected_hash)
        except Exception as e:
            logger.error("Token verification error: %s", e)
            return False


security_manager = SecurityManager()
