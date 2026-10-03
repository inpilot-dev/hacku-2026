"""Audit log, checkpoints and independent verifier (owner: Seungbin).

``audit_shim`` in the wallet imports ``append_event`` and ``canonical_json``
from here. Keep this import light: it runs while the wallet package loads.
"""

from .log import GENESIS_HASH, append_event, canonical_json, event_hash, read_events, sha256_hex

__all__ = ["GENESIS_HASH", "append_event", "canonical_json", "event_hash", "read_events", "sha256_hex"]
