"""
API Security & Bearer Token Authentication for Radxa ROCK Pi S Controller.
Stores only cryptographic SHA-256 hashes of tokens.
Prints raw tokens exactly ONCE on initial generation or rotation.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import time
import urllib.parse
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from app.config import system_config

logger = logging.getLogger("smart_water.security")


class SecurityManager:
    """Manages API token hashing, verification, rotation, and short-lived SSE tickets."""

    def __init__(self, auth_file_path: Optional[str] = None):
        self.auth_file_path = Path(auth_file_path or system_config.security.auth_token_file)
        self._sse_tickets: dict[str, float] = {}

    def create_sse_ticket(self, ttl_seconds: int = 60) -> str:
        """Generates a short-lived (60s), single-use ticket for SSE streaming."""
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        # Prune expired tickets
        self._sse_tickets = {t: exp for t, exp in self._sse_tickets.items() if exp > now}
        self._sse_tickets[ticket] = now + ttl_seconds
        return ticket

    def consume_sse_ticket(self, ticket: Optional[str]) -> bool:
        """Validates and immediately consumes a single-use SSE ticket (Part 1.3)."""
        if not ticket:
            return False
        now = time.time()
        expiry = self._sse_tickets.pop(ticket, None)
        if expiry is None:
            return False
        return now <= expiry

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


def validate_webhook_url(url: str, allowlist: List[str]) -> Tuple[bool, Optional[str]]:
    """
    Validates webhook URL against Server-Side Request Forgery (SSRF) (Part 3.4):
      - Must be http or https
      - Host must be in allowlist (if allowlist configured)
      - Resolves hostname and rejects loopback, link-local, multicast, and private non-routable IPs
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return False, f"Malformed URL: {exc}"

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme '{parsed.scheme}' (only http and https allowed)"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing valid hostname"

    # Allowlist check: if allowlist is specified, hostname must match an allowed host
    if allowlist and hostname not in allowlist:
        return False, f"Host '{hostname}' is not in webhook allowlist {allowlist}"

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
        if not addr_infos:
            return False, f"Could not resolve host '{hostname}'"

        for _, _, _, _, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_loopback:
                return False, f"Resolved IP {ip_str} is loopback (127.0.0.0/8 or ::1)"
            if ip.is_link_local:
                return False, f"Resolved IP {ip_str} is link-local (169.254.0.0/16 or fe80::/10)"
            if ip.is_reserved or ip.is_multicast:
                return False, f"Resolved IP {ip_str} is reserved or multicast"
            if ip.is_unspecified:
                return False, f"Resolved IP {ip_str} is unspecified (0.0.0.0 or ::)"
    except socket.gaierror as err:
        return False, f"DNS resolution failed for '{hostname}': {err}"
    except Exception as exc:
        return False, f"Security validation error for '{hostname}': {exc}"

    return True, None


security_manager = SecurityManager()
