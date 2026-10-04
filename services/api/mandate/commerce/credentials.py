"""User-signed VC-JOSE credentials; export/verification never grants spending.

Authenticated owner pins an Ed25519 key. The browser holds the private key;
server verifies its JWS and checks current wallet revocation/version.
This is prototype key binding, not legal identity or bank acceptance.
"""
import base64
import json
from datetime import timedelta
from urllib.parse import quote
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mandate.payments.clock import iso, parse
from mandate.payments.errors import conflict, invalid


def decode_key(key):
    try:
        raw = base64.urlsafe_b64decode(key + '=' * (-len(key) % 4))
        if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip('=') != key:
            raise ValueError()
        return raw
    except (ValueError, TypeError):
        raise invalid('Canonical Ed25519 public key required.') from None


def did(key):
    data = b'\xed\x01' + decode_key(key)
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    number, encoded = int.from_bytes(data, 'big'), ''
    while number:
        number, r = divmod(number, 58)
        encoded = alphabet[r] + encoded
    return 'did:key:z' + encoded


class Credentials:
    def __init__(self, wallet):
        self.wallet = wallet
        with wallet.db.write_tx() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS commerce_owner_keys (owner_id TEXT PRIMARY KEY, public_x TEXT NOT NULL)')

    def template(self, actor, mandate_id, public_x):
        issuer = did(public_x)
        mandate = self.wallet.get_mandate(actor, mandate_id)
        if mandate['status'] != 'active':
            raise conflict('Only an active owned mandate can be exported.')
        with self.wallet.db.write_tx() as conn:
            chain = self.wallet._chain(conn, self.wallet._load_mandate(conn, mandate_id))
            if any(m['status'] != 'active' or parse(m['policy']['expires_at']) <= self.wallet.clock.now() for m in chain):
                raise conflict('Every ancestor must be active for credential export or verification.')
            row = conn.execute('SELECT public_x FROM commerce_owner_keys WHERE owner_id=?', (actor.actor_id,)).fetchone()
            if row and row[0] != public_x:
                raise conflict('Owner key is pinned. Key rotation requires a separate authorized migration.')
            conn.execute('INSERT OR IGNORE INTO commerce_owner_keys VALUES (?,?)', (actor.actor_id, public_x))
        return {'@context': ['https://www.w3.org/ns/credentials/v2', {'AgentDelegationCredential': 'urn:mandate:AgentDelegationCredential', 'mandate_id': 'urn:mandate:mandateId', 'version': 'urn:mandate:version', 'authorityChain': {'@id': 'urn:mandate:authorityChain', '@type': '@json'}, 'policy': {'@id': 'urn:mandate:policy', '@type': '@json'}}], 'type': ['VerifiableCredential', 'AgentDelegationCredential'],
                'id': 'urn:mandate:' + mandate_id + ':v' + str(mandate['version']), 'issuer': issuer,
                'validFrom': mandate['created_at'], 'validUntil': mandate['policy']['expires_at'],
                'credentialSubject': {'id': 'urn:mandate:agent:' + quote(mandate['delegatee_id'], safe=''),
                                      'mandate_id': mandate_id, 'version': mandate['version'], 'policy': mandate['policy'],
                                      'authorityChain': [{'mandate_id': m['id'], 'version': m['version'], 'policy': m['policy']} for m in chain]}}

    def verify(self, actor, token):
        with self.wallet.db.read() as conn:
            row = conn.execute('SELECT public_x FROM commerce_owner_keys WHERE owner_id=?', (actor.actor_id,)).fetchone()
        if not row:
            raise invalid('No owner key is pinned.')
        issuer = did(row[0])
        try:
            header = jwt.get_unverified_header(token)
            if header.get('alg') != 'EdDSA' or header.get('typ') != 'vc+jwt' or header.get('kid') != issuer + '#' + issuer.removeprefix('did:key:'):
                raise ValueError()
            payload = jwt.decode(token, Ed25519PublicKey.from_public_bytes(decode_key(row[0])), algorithms=['EdDSA'],
                                 options={'verify_aud': False, 'verify_exp': False})
            subject = payload['credentialSubject']
            expected = self.template(actor, subject['mandate_id'], row[0])
            if payload != expected or parse(payload['validUntil']) <= self.wallet.clock.now():
                raise ValueError()
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise invalid('Credential signature, terms or validity is invalid.') from None
        return {'valid': True, 'issuer': issuer, 'mandate_id': subject['mandate_id'],
                'scope': 'Owner-key binding and current wallet state; no legal-identity or bank assertion.'}
