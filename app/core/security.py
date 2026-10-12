"""
API Security & Bearer Token Authentication for Radxa ROCK Pi S Controller.
Stores only cryptographic SHA-256 hashes of tokens.
Prints raw tokens exactly ONCE on initial generation or rotation.
"""

import asyncio
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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
import httpx
from app.config import system_config

logger = logging.getLogger("smart_water.security")

TokenRole = Literal["operator", "viewer"]


@dataclass
class TokenRecord:
    """Record storing hashed token metadata, role, and expiration."""
    name: str
    role: TokenRole
    token_hash: str
    created_at: str
    expires_at: Optional[str] = None
    revoked: bool = False

    def is_valid(self) -> bool:
        """Checks whether token is active and unexpired."""
        if self.revoked:
            return False
        if self.expires_at:
            try:
                exp_dt = datetime.fromisoformat(self.expires_at)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= exp_dt:
                    return False
            except Exception:
                return False
        return True


class SecurityManager:
    """Manages API token hashing, verification, RBAC roles, and short-lived SSE tickets."""

    def __init__(self, auth_file_path: Optional[str] = None):
        self.auth_file_path = Path(auth_file_path or system_config.security.auth_token_file)
        self._sse_tickets: dict[str, float] = {}
        self._cached_token_hash: Optional[str] = None
        self._cached_tokens: Dict[str, TokenRecord] = {}
        self._cached_mtime: Optional[float] = None

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

    def _load_stored_auth(self) -> None:
        """Loads and caches tokens and hashes in memory; reloads when auth.json mtime changes."""
        if not self.auth_file_path.exists():
            self._cached_token_hash = None
            self._cached_tokens = {}
            self._cached_mtime = None
            return

        try:
            current_mtime = self.auth_file_path.stat().st_mtime
            if self._cached_mtime == current_mtime:
                return

            with open(self.auth_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._cached_token_hash = data.get("token_hash")
            self._cached_tokens = {}

            # Parse named tokens
            raw_tokens = data.get("tokens", {})
            for t_name, t_data in raw_tokens.items():
                self._cached_tokens[t_name] = TokenRecord(
                    name=t_data.get("name", t_name),
                    role=t_data.get("role", "operator"),
                    token_hash=t_data.get("token_hash", ""),
                    created_at=t_data.get("created_at", ""),
                    expires_at=t_data.get("expires_at"),
                    revoked=bool(t_data.get("revoked", False)),
                )

            # If legacy token_hash exists and not in tokens dict, synthesize legacy operator token
            if self._cached_token_hash and "default_admin" not in self._cached_tokens:
                self._cached_tokens["default_admin"] = TokenRecord(
                    name="default_admin",
                    role="operator",
                    token_hash=self._cached_token_hash,
                    created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
                    expires_at=None,
                    revoked=False,
                )

            self._cached_mtime = current_mtime
        except Exception as e:
            logger.error("Error reading auth file %s: %s", self.auth_file_path, e)

    def _get_stored_token_hash(self) -> Optional[str]:
        """Backward-compatible helper returning legacy token hash or default admin hash."""
        self._load_stored_auth()
        if self._cached_token_hash:
            return self._cached_token_hash
        for t in self._cached_tokens.values():
            if t.role == "operator" and t.is_valid():
                return t.token_hash
        return None

    def _save_auth_file(self, data: Dict[str, Any]) -> None:
        """Atomically saves auth payload to disk (mode 0600)."""
        self.auth_file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.auth_file_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_path.replace(self.auth_file_path)
        try:
            os.chmod(self.auth_file_path, 0o600)
        except OSError:
            pass
        self._cached_mtime = self.auth_file_path.stat().st_mtime

    def get_or_create_token(self) -> Tuple[Optional[str], str]:
        """
        Loads the stored token hash. If none exists, generates a new default operator token,
        persists its hash, and returns (raw_token, token_hash).
        """
        self._load_stored_auth()
        if self._cached_tokens or self._cached_token_hash:
            return None, (self._cached_token_hash or next(iter(self._cached_tokens.values())).token_hash)

        # Generate fresh secure random token
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw_token)
        now_iso = datetime.now(timezone.utc).isoformat()

        record = TokenRecord(
            name="default_admin",
            role="operator",
            token_hash=token_hash,
            created_at=now_iso,
            expires_at=None,
            revoked=False,
        )
        self._cached_tokens = {"default_admin": record}
        self._cached_token_hash = token_hash

        payload: Dict[str, Any] = {
            "token_hash": token_hash,
            "created_at": now_iso,
            "tokens": {
                "default_admin": asdict(record),
            },
        }
        self._save_auth_file(payload)

        print("\n" + "=" * 72)
        print(" [SECURITY NOTICE] A NEW SMART-WATER API BEARER TOKEN HAS BEEN GENERATED:")
        print(f" Token: {raw_token}")
        print(" Save this token now. It is NEVER stored in plaintext and will not be shown again.")
        print("=" * 72 + "\n")

        return raw_token, token_hash

    def rotate_token(self) -> str:
        """Rotates the default operator token, writes new hash to disk, and returns the new raw token."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw_token)
        now_iso = datetime.now(timezone.utc).isoformat()

        self._load_stored_auth()
        record = TokenRecord(
            name="default_admin",
            role="operator",
            token_hash=token_hash,
            created_at=now_iso,
            expires_at=None,
            revoked=False,
        )
        self._cached_tokens["default_admin"] = record
        self._cached_token_hash = token_hash

        # Read existing file to preserve other tokens
        data: Dict[str, Any] = {}
        if self.auth_file_path.exists():
            try:
                with open(self.auth_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        data["token_hash"] = token_hash
        data["created_at"] = now_iso
        tokens_dict = data.setdefault("tokens", {})
        tokens_dict["default_admin"] = asdict(record)

        self._save_auth_file(data)
        logger.warning("Default API Bearer token was rotated.")
        return raw_token

    def _save_token_hash(self, token_hash: str) -> None:
        """Backward-compatible helper to save legacy token hash."""
        now_iso = datetime.now(timezone.utc).isoformat()
        record = TokenRecord(
            name="default_admin",
            role="operator",
            token_hash=token_hash,
            created_at=now_iso,
            expires_at=None,
            revoked=False,
        )
        self._cached_tokens = {"default_admin": record}
        self._cached_token_hash = token_hash
        payload: Dict[str, Any] = {
            "token_hash": token_hash,
            "created_at": now_iso,
            "tokens": {"default_admin": asdict(record)},
        }
        self._save_auth_file(payload)

    def issue_token(
        self,
        name: str,
        role: TokenRole = "operator",
        days: Optional[int] = None,
    ) -> Tuple[str, TokenRecord]:
        """
        Issues a new named API Bearer token with specific role ('operator' or 'viewer')
        and optional expiration.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Token name cannot be empty.")
        if role not in ("operator", "viewer"):
            raise ValueError(f"Invalid role '{role}'. Allowed roles: 'operator', 'viewer'.")

        self._load_stored_auth()
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw_token)
        now_dt = datetime.now(timezone.utc)
        expires_at = (now_dt + timedelta(days=days)).isoformat() if days else None

        record = TokenRecord(
            name=clean_name,
            role=role,
            token_hash=token_hash,
            created_at=now_dt.isoformat(),
            expires_at=expires_at,
            revoked=False,
        )

        data: Dict[str, Any] = {}
        if self.auth_file_path.exists():
            try:
                with open(self.auth_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        tokens_dict = data.setdefault("tokens", {})
        tokens_dict[clean_name] = asdict(record)
        self._save_auth_file(data)
        self._cached_tokens[clean_name] = record

        logger.info("Issued new API token '%s' (role: %s, expires: %s)", clean_name, role, expires_at)
        return raw_token, record

    def revoke_token(self, name: str) -> bool:
        """Revokes an existing named token by setting revoked=True."""
        clean_name = name.strip()
        self._load_stored_auth()

        data: Dict[str, Any] = {}
        if not self.auth_file_path.exists():
            return False
        try:
            with open(self.auth_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False

        tokens_dict = data.get("tokens", {})
        if clean_name not in tokens_dict:
            return False

        tokens_dict[clean_name]["revoked"] = True
        self._save_auth_file(data)
        if clean_name in self._cached_tokens:
            self._cached_tokens[clean_name].revoked = True

        logger.warning("Revoked API token '%s'", clean_name)
        return True

    def list_tokens(self) -> List[TokenRecord]:
        """Returns all registered tokens."""
        self._load_stored_auth()
        return list(self._cached_tokens.values())

    def authenticate(self, provided_token: Optional[str]) -> Optional[TokenRecord]:
        """
        Authenticates provided token against stored tokens in constant time.
        Returns TokenRecord if valid and unexpired, else None.
        """
        if not provided_token:
            return None

        self._load_stored_auth()
        provided_hash = self._hash_token(provided_token)

        # Check in named tokens
        matched_token: Optional[TokenRecord] = None
        for record in self._cached_tokens.values():
            if hmac.compare_digest(provided_hash, record.token_hash):
                matched_token = record
                break

        # Check in legacy token_hash if no named match
        if not matched_token and self._cached_token_hash:
            if hmac.compare_digest(provided_hash, self._cached_token_hash):
                matched_token = TokenRecord(
                    name="default_admin",
                    role="operator",
                    token_hash=self._cached_token_hash,
                    created_at="",
                    expires_at=None,
                    revoked=False,
                )

        if matched_token and matched_token.is_valid():
            return matched_token

        return None

    def verify_token(self, provided_token: Optional[str], required_role: TokenRole = "viewer") -> bool:
        """
        Verifies provided Bearer token against stored hashes.
        Enforces required role: if required_role is 'operator', viewer tokens are rejected.
        """
        token_record = self.authenticate(provided_token)
        if not token_record:
            return False
        if required_role == "operator" and token_record.role != "operator":
            return False
        return True


BLOCKED_NETWORKS = [
    ipaddress.ip_network("100.64.0.0/10"),     # Carrier-Grade NAT (RFC 6598)
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / Cloud metadata (RFC 3927)
    ipaddress.ip_network("fc00::/7"),          # IPv6 Unique Local Address (ULA, RFC 4193)
    ipaddress.ip_network("127.0.0.0/8"),       # IPv4 Loopback
    ipaddress.ip_network("::1/128"),           # IPv6 Loopback
    ipaddress.ip_network("0.0.0.0/8"),         # "This host" network
    ipaddress.ip_network("::/128"),            # IPv6 Unspecified
]
METADATA_IP = ipaddress.ip_address("169.254.169.254")


def is_ip_allowed_for_webhook(
    ip: Union[str, ipaddress.IPv4Address, ipaddress.IPv6Address]
) -> Tuple[bool, Optional[str]]:
    """Checks an IP address against global routing and blocked ranges."""
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip)
        except ValueError as exc:
            return False, f"Invalid IP address '{ip}': {exc}"

    # 1. Must be globally routable
    if not ip.is_global:
        return False, f"Resolved IP {ip} is not globally routable (blocked)"

    # 2. Explicit metadata IP check
    if ip == METADATA_IP:
        return False, f"Resolved IP {ip} is prohibited cloud metadata (169.254.169.254)"

    # 3. Explicit blocked networks: 100.64.0.0/10, 169.254.0.0/16, fc00::/7, loopback
    for net in BLOCKED_NETWORKS:
        if ip in net:
            return False, f"Resolved IP {ip} belongs to prohibited network {net}"

    # 4. Strict defense against private, loopback, multicast, reserved
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False, f"Resolved IP {ip} is private, link-local, or reserved (blocked)"

    return True, None


def resolve_and_validate_webhook_url(
    url: str,
    allowlist: Optional[List[str]] = None,
    allow_http: bool = False,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates webhook URL against Server-Side Request Forgery (SSRF) and DNS rebinding:
      - Validates scheme: https only by default (rejects http unless allow_http=True)
      - Host must be in allowlist (if allowlist configured)
      - Resolves hostname via DNS
      - Rejects unless ip.is_global; explicitly blocks 100.64.0.0/10, metadata 169.254.169.254, IPv6 ULA
      - Returns (is_safe, error_message, pinned_ip)
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return False, f"Malformed URL: {exc}", None

    # Check scheme first: only https/http allowed
    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme '{parsed.scheme}': only https and http allowed", None
    if not allow_http and parsed.scheme != "https":
        return False, f"Unsupported scheme '{parsed.scheme}': only https allowed by default", None

    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing valid hostname", None

    # Allowlist check: if allowlist is specified, hostname must match an allowed host
    if allowlist and hostname not in allowlist:
        return False, f"Host '{hostname}' is not in webhook allowlist {allowlist}", None

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addr_infos = socket.getaddrinfo(hostname, port)
        if not addr_infos:
            return False, f"Could not resolve host '{hostname}'", None

        resolved_ips = []
        for _, _, _, _, sockaddr in addr_infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            allowed, reason = is_ip_allowed_for_webhook(ip)
            if not allowed:
                return False, reason, None
            resolved_ips.append(ip_str)

        # Pin the first resolved valid IP
        pinned_ip = resolved_ips[0]
        return True, None, pinned_ip
    except socket.gaierror as err:
        return False, f"DNS resolution failed for '{hostname}': {err}", None
    except Exception as exc:
        return False, f"Security validation error for '{hostname}': {exc}", None


def validate_webhook_url(
    url: str,
    allowlist: Optional[List[str]] = None,
    allow_http: bool = False,
    require_https: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
    """Synchronous validation helper."""
    if require_https is not None:
        allow_http = not require_https
    is_safe, err_msg, _ = resolve_and_validate_webhook_url(url, allowlist, allow_http=allow_http)
    return is_safe, err_msg


async def async_validate_webhook_url(
    url: str,
    allowlist: Optional[List[str]] = None,
    allow_http: bool = False,
    require_https: Optional[bool] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Executes DNS resolution and IP validation off-thread to avoid blocking event loop."""
    if require_https is not None:
        allow_http = not require_https
    return await asyncio.to_thread(resolve_and_validate_webhook_url, url, allowlist, allow_http)


class PinnedIPTransport(httpx.AsyncBaseTransport):
    """
    An httpx async transport that pins the TCP connection to a pre-validated IP address
    while preserving the original Host header and TLS SNI extension to prevent DNS rebinding attacks.
    """

    def __init__(self, pinned_ip: str, original_host: str, verify: bool = True):
        self.pinned_ip = pinned_ip
        self.original_host = original_host
        self._underlying = httpx.AsyncHTTPTransport(verify=verify)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        orig_url = request.url
        port = orig_url.port or (443 if orig_url.scheme == "https" else 80)
        ip_target = (
            f"[{self.pinned_ip}]"
            if ":" in self.pinned_ip and not self.pinned_ip.startswith("[")
            else self.pinned_ip
        )
        pinned_url = orig_url.copy_with(host=ip_target, port=port)

        headers = dict(request.headers)
        headers["host"] = self.original_host

        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self.original_host

        pinned_request = httpx.Request(
            method=request.method,
            url=pinned_url,
            headers=headers,
            content=request.content,
            extensions=extensions,
        )
        return await self._underlying.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._underlying.aclose()


security_manager = SecurityManager()
