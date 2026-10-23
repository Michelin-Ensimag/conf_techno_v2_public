"""Ed25519 signed envelopes, compatible with admin-panel's app/signing.py.

Wire format: {"username": str, "seq": int, "payload": {...}, "signature": base64(...)}.
The signature covers the canonical JSON of {"seq": seq, "payload": payload}.
"""

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from student_client.config import Settings


class StartupError(Exception):
    """The identity is not usable; the message lists every problem found."""


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_private_key(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    return key


def load_public_key(pem: str) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not an Ed25519 public key")
    return key


def sign_payload(private_key: Ed25519PrivateKey, payload: dict[str, Any]) -> str:
    return base64.b64encode(private_key.sign(canonical_json(payload))).decode("utf-8")


def verify_signature(public_key: Ed25519PublicKey, payload: dict[str, Any], signature_b64: str) -> bool:
    try:
        public_key.verify(base64.b64decode(signature_b64), canonical_json(payload))
        return True
    except (InvalidSignature, ValueError):
        return False


def is_signed_envelope(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("username"), str)
        and isinstance(value.get("seq"), int)
        and not isinstance(value.get("seq"), bool)
        and isinstance(value.get("payload"), dict)
        and isinstance(value.get("signature"), str)
    )


@dataclass
class Identity:
    """The team's signing key pair and the admin key used to verify incoming messages."""

    username: str
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    admin_username: str
    admin_public_key: Ed25519PublicKey
    _last_seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def next_sequence(self) -> int:
        """Strictly increasing nonce (wall-clock ns), also across restarts."""
        with self._lock:
            self._last_seq = max(time.time_ns(), self._last_seq + 1)
            return self._last_seq

    def build_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        seq = self.next_sequence()
        return {
            "username": self.username,
            "seq": seq,
            "payload": payload,
            "signature": sign_payload(self.private_key, {"seq": seq, "payload": payload}),
        }

    def verify_admin_envelope(self, envelope: dict[str, Any]) -> bool:
        """True if the envelope is signed by the admin identity with the admin public key."""
        return envelope["username"] == self.admin_username and verify_signature(
            self.admin_public_key,
            {"seq": envelope["seq"], "payload": envelope["payload"]},
            envelope["signature"],
        )


def load_identity(settings: Settings) -> Identity:
    """Check the environment provides a usable identity, or raise StartupError listing what is wrong."""
    problems: list[str] = []
    for name, value in (
        ("TEAM_USERNAME", settings.team_username.strip()),
        ("TEAM_PRIVATE_KEY", settings.team_private_key),
        ("TEAM_PUBLIC_KEY", settings.team_public_key),
        ("ADMIN_PUBLIC_KEY", settings.admin_public_key),
    ):
        if not value:
            problems.append(f"{name} is not set")

    private_key = public_key = admin_public_key = None
    if settings.team_private_key:
        try:
            private_key = load_private_key(settings.team_private_key)
        except (ValueError, TypeError) as exc:
            problems.append(f"TEAM_PRIVATE_KEY is not a valid Ed25519 PEM private key ({exc})")
    if settings.team_public_key:
        try:
            public_key = load_public_key(settings.team_public_key)
        except (ValueError, TypeError) as exc:
            problems.append(f"TEAM_PUBLIC_KEY is not a valid Ed25519 PEM public key ({exc})")
    if settings.admin_public_key:
        try:
            admin_public_key = load_public_key(settings.admin_public_key)
        except (ValueError, TypeError) as exc:
            problems.append(f"ADMIN_PUBLIC_KEY is not a valid Ed25519 PEM public key ({exc})")

    if private_key is not None and public_key is not None:
        probe = {"seq": 1, "payload": {"purpose": "startup-key-check"}}
        if not verify_signature(public_key, probe, sign_payload(private_key, probe)):
            problems.append("TEAM_PRIVATE_KEY does not match TEAM_PUBLIC_KEY")
    if public_key is not None and admin_public_key is not None and (
        public_key.public_bytes_raw() == admin_public_key.public_bytes_raw()
    ):
        problems.append("ADMIN_PUBLIC_KEY is the team's own public key, not the admin's")

    if problems or private_key is None or public_key is None or admin_public_key is None:
        raise StartupError("; ".join(problems))
    return Identity(
        username=settings.team_username.strip(),
        private_key=private_key,
        public_key=public_key,
        admin_username=settings.admin_username,
        admin_public_key=admin_public_key,
    )
