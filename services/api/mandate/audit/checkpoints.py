"""Ed25519 checkpoint signing, kept apart from the wallet's authorization-token key.

A checkpoint commits to a stream head: (stream_id, sequence, event_hash). The
signature is base64url (unpadded) Ed25519 over the canonical JSON of every
Checkpoint field except ``signature``. Its stable ID is ``<stream_id>:<sequence>``.

The private key lives in the API's own key directory (MANDATE_AUDIT_KEY_DIR,
default ``.data/audit/keys``) next to a public PEM that the verifier process
pins at startup.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .log import HKT, canonical_json

PRIVATE_NAME = "checkpoint_ed25519.pem"
PUBLIC_NAME = "checkpoint_ed25519.pub.pem"
SIGNED_FIELDS = ("stream_id", "sequence", "event_hash", "key_id", "created_at")


def checkpoint_id(checkpoint: dict) -> str:
    return f"{checkpoint['stream_id']}:{checkpoint['sequence']}"


def key_id_for(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f"ckpt_{hashlib.sha256(raw).hexdigest()[:16]}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_public_key(pem_path: str | Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(Path(pem_path).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError(f"{pem_path} is not an Ed25519 public key")
    return key


def signature_valid(checkpoint: dict, public_key: Ed25519PublicKey) -> bool:
    """True only if the checkpoint names this key and its signature verifies."""
    try:
        if checkpoint["key_id"] != key_id_for(public_key):
            return False
        public_key.verify(_unb64url(checkpoint["signature"]),
                          canonical_json({k: checkpoint[k] for k in SIGNED_FIELDS}))
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


class CheckpointSigner:
    def __init__(self, private_key: Ed25519PrivateKey):
        self._private_key = private_key
        self.public_key = private_key.public_key()
        self.key_id = key_id_for(self.public_key)

    @classmethod
    def generate(cls) -> "CheckpointSigner":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_key_dir(cls, key_dir: str | Path) -> "CheckpointSigner":
        """Load the key, creating it (0600) and its public PEM on first use."""
        key_dir = Path(key_dir)
        path = key_dir / PRIVATE_NAME
        if not path.exists():
            key_dir.mkdir(parents=True, exist_ok=True)
            pem = Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 key")
        signer = cls(key)
        (key_dir / PUBLIC_NAME).write_text(signer.public_pem())
        return signer

    def public_pem(self) -> str:
        return self.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def sign(self, stream_id: str, sequence: int, event_hash: str, created_at: datetime | None = None) -> dict:
        body = {
            "stream_id": stream_id,
            "sequence": sequence,
            "event_hash": event_hash,
            "key_id": self.key_id,
            "created_at": (created_at or datetime.now(HKT)).astimezone(HKT).replace(microsecond=0).isoformat(),
        }
        return {**body, "signature": _b64url(self._private_key.sign(canonical_json(body)))}
