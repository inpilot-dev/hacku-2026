"""Ed25519 (EdDSA) compact JWS for purchase authorizations.

Only ``EdDSA`` is accepted, the public key is pinned, and the audience is
fixed. Expiry is checked against the wallet's clock, not the host clock, so
the scenario lab's controlled clock applies. Ed25519 signatures are
deterministic: re-signing the same persisted claims reproduces the same
token, so the wallet never stores tokens and idempotent replays still return
the original one.

The private key lives in the wallet's key directory (MANDATE_WALLET_KEY_DIR),
outside anything the agent can read. It is separate from Seungbin's
checkpoint-signing key.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .clock import parse

AUDIENCE = "mandate-payment-sandbox"
# Approval decision tokens (approval_notify.py) use their own audience, so one can never pass as a
# purchase authorization, and an authorization can never decide an approval.
APPROVAL_AUDIENCE = "mandate-approval-decision"
ALGORITHM = "EdDSA"
ISSUER = "mandate-wallet"


class TokenInvalid(Exception):
    pass


class TokenExpired(Exception):
    pass


class Signer:
    def __init__(self, private_key: Ed25519PrivateKey, key_id: str = "wallet-1"):
        self._private_key = private_key
        self.public_key: Ed25519PublicKey = private_key.public_key()
        self.key_id = key_id

    @classmethod
    def generate(cls) -> "Signer":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_key_dir(cls, key_dir: str | Path) -> "Signer":
        path = Path(key_dir) / "authorization_ed25519.pem"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            pem = Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 key")
        return cls(key)

    def public_pem(self) -> str:
        return self.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def sign(self, claims: dict, *, audience: str = AUDIENCE) -> str:
        """Sign contract claims (AuthorizationClaims) plus registered JWT claims."""
        payload = dict(claims)
        payload.update(
            iss=ISSUER,
            aud=audience,
            jti=claims["token_id"],
            iat=int(parse(claims["issued_at"]).timestamp()),
            exp=int(parse(claims["expires_at"]).timestamp()),
        )
        return jwt.encode(payload, self._private_key, algorithm=ALGORITHM, headers={"kid": self.key_id})

    def verify(self, token: str, now: datetime, *, check_expiry: bool = True, audience: str = AUDIENCE) -> dict:
        """Return verified claims. Raises TokenInvalid or TokenExpired."""
        try:
            payload = jwt.decode(
                token,
                self.public_key,
                algorithms=[ALGORITHM],
                audience=audience,
                issuer=ISSUER,
                options={"verify_exp": False, "verify_iat": False, "verify_nbf": False, "require": ["aud", "exp", "iat", "jti", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenInvalid(type(exc).__name__) from None
        if check_expiry and now.timestamp() >= payload["exp"]:
            raise TokenExpired()
        return payload
