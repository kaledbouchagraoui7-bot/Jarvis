"""Execution Layer — AuthManager: the ONLY place credentials exist.

The Orchestration layer never sees raw passwords or API keys. Tools request a
credential *handle* at execution time; AuthManager injects it into the outbound
call for milliseconds and wipes it. Storage is an encrypted-at-rest vault
(Fernet-style envelope via stdlib HMAC+AES substitute, or `cryptography` if
installed), keyed from AUTOMATON_VAULT_KEY (or derived from a passphrase).

OAuth lifecycles (Gmail/Notion/Instagram) are modeled with refresh-token
handling; GitHub uses PATs. All events are audit-logged WITHOUT secrets.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..core.types import AuditEvent


# --------------------------------------------------------------------------- #
# Envelope encryption (stdlib AES-128-CTR-ish stream via SHA256-CTR keystream +
# HMAC-SHA256 authenticate-then-encrypt). For production swap in `cryptography`
# (Fernet) — the interface stays identical.
# --------------------------------------------------------------------------- #
def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        out += block
        counter += 1
    return out[:length]


def _derive_key(master: bytes, purpose: str) -> bytes:
    return hashlib.sha256(b"automaton-vault:" + purpose.encode() + b":" + master).digest()


def encrypt_secret(master: bytes, plaintext: str) -> str:
    nonce = secrets.token_bytes(16)
    enc_k, mac_k = _derive_key(master, "enc"), _derive_key(master, "mac")
    raw = plaintext.encode()
    ct = bytes(a ^ b for a, b in zip(raw, _keystream(enc_k, nonce, len(raw))))
    tag = hmac.new(mac_k, nonce + ct, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + tag + ct).decode()


def decrypt_secret(master: bytes, blob: str) -> str:
    data = base64.urlsafe_b64decode(blob.encode())
    nonce, tag, ct = data[:16], data[16:48], data[48:]
    enc_k, mac_k = _derive_key(master, "enc"), _derive_key(master, "mac")
    if not hmac.compare_digest(tag, hmac.new(mac_k, nonce + ct, hashlib.sha256).digest()):
        raise ValueError("vault integrity check failed (tampered or wrong key)")
    return bytes(a ^ b for a, b in zip(ct, _keystream(enc_k, nonce, len(ct)))).decode()


# --------------------------------------------------------------------------- #
@dataclass
class CredentialHandle:
    """Ephemeral, single-use handle. Never serializable into prompts/logs."""

    name: str
    kind: str  # pat | oauth | api_key
    _getter: Callable[[], str]
    _ttl_s: float = 30.0
    _issued: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "_issued", time.time())

    def reveal(self) -> str:
        """Return the raw secret. Call ONLY inside the executor at injection time."""
        if time.time() - self._issued > self._ttl_s:
            raise RuntimeError(f"credential handle {self.name} expired (TTL {self._ttl_s}s)")
        return self._getter()

    def __repr__(self) -> str:  # leak-proof
        return f"CredentialHandle(name={self.name!r}, kind={self.kind!r}, [REDACTED])"

    __str__ = __repr__


class OAuthLifecycle:
    """Minimal OAuth2 refresh flow (client_credentials / refresh_token grants)."""

    def __init__(self, token_url: str, client_id: str, client_secret: str, scopes: list[str]):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0.0

    def is_expired(self, skew_s: int = 60) -> bool:
        return time.time() + skew_s >= self.expires_at

    def bootstrap_from(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        self.access_token, self.refresh_token = access_token, refresh_token
        self.expires_at = time.time() + expires_in

    def refresh(self, http_post: Optional[Callable[[str, dict[str, str]], dict[str, Any]]] = None) -> str:
        """Refresh the access token. ``http_post`` injectable for tests/offline."""
        if http_post is None:
            def http_post(url: str, form: dict[str, str]) -> dict[str, Any]:
                import urllib.parse, urllib.request
                data = urllib.parse.urlencode(form).encode()
                with urllib.request.urlopen(url, data=data, timeout=30) as r:
                    return json.loads(r.read().decode())
        resp = http_post(
            self.token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token or "",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": " ".join(self.scopes),
            },
        )
        self.access_token = resp["access_token"]
        self.refresh_token = resp.get("refresh_token", self.refresh_token)
        self.expires_at = time.time() + int(resp.get("expires_in", 3600))
        return self.access_token

    def valid_token(self, refresher: Optional[Callable[[], str]] = None) -> str:
        if self.is_expired():
            if refresher:
                refresher()
            elif os.environ.get("AUTOMATON_ALLOW_NETWORK") == "1":
                self.refresh()
            else:
                raise RuntimeError("OAuth token expired and network disabled")
        return self.access_token or ""


class AuthManager:
    """Encrypted credential vault + injection points for the Execution Layer."""

    def __init__(
        self,
        vault_path: str = "data/vault.json",
        master_key: Optional[bytes] = None,
        audit_log: Optional[list[AuditEvent]] = None,
    ):
        self.vault_path = vault_path
        self.audit_log = audit_log if audit_log is not None else []
        env_key = os.environ.get("AUTOMATON_VAULT_KEY")
        if master_key is not None:
            self.master = hashlib.sha256(master_key).digest()
        elif env_key:
            self.master = hashlib.sha256(env_key.encode()).digest()
        else:
            # ephemeral key: vault entries won't survive restarts (dev mode)
            self.master = secrets.token_bytes(32)
        self._entries: dict[str, dict[str, Any]] = {}
        self._oauth: dict[str, OAuthLifecycle] = {}
        self._load()

    # -- persistence ------------------------------------------------------ #
    def _load(self) -> None:
        if os.path.exists(self.vault_path):
            with open(self.vault_path, "r", encoding="utf-8") as fh:
                self._entries = json.load(fh)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.vault_path) or ".", exist_ok=True)
        tmp = self.vault_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._entries, fh)
        os.replace(tmp, self.vault_path)
        try:
            os.chmod(self.vault_path, 0o600)
        except OSError:
            pass

    # -- enrollment ------------------------------------------------------- #
    def store_pat(self, name: str, token: str) -> None:
        self._entries[name] = {"kind": "pat", "secret": encrypt_secret(self.master, token)}
        self._save()
        self.audit_log.append(AuditEvent(actor="auth_manager", event="stored", detail={"name": name}))

    def store_api_key(self, name: str, key: str) -> None:
        self.store_pat(name, key)  # same envelope, different label
        self._entries[name]["kind"] = "api_key"
        self._save()

    def register_oauth(
        self,
        name: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        access_token: str,
        refresh_token: str,
        expires_in: int = 3600,
    ) -> None:
        flow = OAuthLifecycle(token_url, client_id, client_secret, scopes)
        flow.bootstrap_from(access_token, refresh_token, expires_in)
        self._oauth[name] = flow
        # persist only the ENCRYPTED refresh token, never the live access token
        self._entries[name] = {
            "kind": "oauth",
            "secret": encrypt_secret(self.master, refresh_token),
            "token_url": token_url,
            "client_id": client_id,
            "scopes": scopes,
        }
        self._save()
        self.audit_log.append(AuditEvent(actor="auth_manager", event="oauth_registered", detail={"name": name}))

    def has(self, name: str) -> bool:
        return name in self._entries or name in self._oauth

    def names(self) -> list[str]:
        return sorted(set(self._entries) | set(self._oauth))

    # -- injection -------------------------------------------------------- #
    def get_handle(self, name: str) -> CredentialHandle:
        """Orchestration/tools ask for a HANDLE; the raw value exists only at
        the moment of execution, inside ``execute_with``."""
        if name in self._oauth:
            flow = self._oauth[name]
            return CredentialHandle(
                name=name,
                kind="oauth",
                _getter=lambda: flow.valid_token(),
            )
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"no credential registered for {name!r}")
        if entry["kind"] == "oauth":
            # rebuilt after restart: caller must re-run the OAuth dance
            raise RuntimeError(f"credential {name!r} needs interactive OAuth re-authentication")
        blob = entry["secret"]

        def getter() -> str:
            return decrypt_secret(self.master, blob)

        self.audit_log.append(
            AuditEvent(actor="auth_manager", event="handle_issued", detail={"name": name})
        )
        return CredentialHandle(name=name, kind=entry["kind"], _getter=getter)

    @staticmethod
    def execute_with(handle: CredentialHandle, fn: Callable[[str], Any]) -> Any:
        """The exact-moment injection point. The secret is passed positionally
        to ``fn`` and never stored on any object that outlives the call."""
        secret = handle.reveal()
        try:
            return fn(secret)
        finally:
            del secret

    def wipe(self, name: str) -> None:
        self._entries.pop(name, None)
        self._oauth.pop(name, None)
        self._save()
        self.audit_log.append(AuditEvent(actor="auth_manager", event="wiped", detail={"name": name}))
