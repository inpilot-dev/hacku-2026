import base64
import copy
import hashlib
import hmac
import json
import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from mandate.payments.auth import Actor
from mandate.payments.errors import ApiError
from mandate.commerce.planning import preview, optimize
from mandate.commerce.recovery import Recovery, digest
from mandate.commerce.routes import build_commerce_router
from mandate.commerce.credentials import Credentials, did
from mandate.commerce.provider import Unknown, Sandbox
from tests.payments.conftest import h, POLICY, USER, AGENT

OWNER = Actor('user_demo', 'user')


class Provider:
    def __init__(self):
        self.sessions = {}
        self.payments = {}
        self.refunds = {}
        self.fail_create_once = False
        self.fail_capture_once = False
        self.refund_status = 'succeeded'
        self.capture_calls = 0

    def create(self, job):
        self.sessions.setdefault(job['id'], {'id': 'cs_test_' + job['id'], 'url': 'https://checkout.stripe.com/test'})
        self.payments.setdefault(job['id'], {'id': 'pi_' + job['id'], 'status': 'requires_capture'})
        if self.fail_create_once:
            self.fail_create_once = False
            raise Unknown()
        return self.sessions[job['id']]

    def retrieve(self, job):
        return self.payments[job['id']].copy()

    def capture(self, job):
        self.capture_calls += 1
        self.payments[job['id']]['status'] = 'succeeded'
        if self.fail_capture_once:
            self.fail_capture_once = False
            raise Unknown()
        return self.retrieve(job)

    def cancel(self, job):
        self.payments[job['id']]['status'] = 'canceled'

    def refund(self, job):
        self.refunds.setdefault(job['id'], {'id': 're_' + job['id'], 'status': self.refund_status})
        return self.refunds[job['id']]

    def retrieve_refund(self, job):
        self.refunds[job['id']]['status'] = self.refund_status
        return self.refunds[job['id']]


def operation(h, provider, scenario='happy', key='purchase1'):
    m = h.confirm()
    q = h.quote()
    recovery = Recovery(h.wallet, lambda: provider)
    body = {'mandate_id': m['id'], 'quote_id': q['id'], 'approved_quote_hash': digest(q), 'scenario': scenario}
    job = recovery.create(OWNER, key, body)
    return recovery, job, m, q, body


def test_preview_is_readonly_and_changes_with_limits(h):
    # Connection snapshot closed explicitly below for portable SQLite locks.
    with h.wallet.db.read() as conn:
        before = list(conn.iterdump())
    p = copy.deepcopy(POLICY); p['approval_above_minor'] = 10000
    result = preview(h.wallet, OWNER, p)
    assert [r['status'] for r in result['examples']] == ['approved', 'requires_review', 'refused', 'refused']
    with h.wallet.db.read() as conn:
        assert list(conn.iterdump()) == before


def test_preview_cannot_expand_parent(h):
    m = h.confirm()
    p = copy.deepcopy(POLICY); p['per_order_limit_minor'] += 1
    with pytest.raises(ApiError):
        preview(h.wallet, OWNER, p, m['id'])


def test_plan_preserves_quantities_and_requires_observed_rules(h):
    m = h.confirm()
    result = optimize(h.wallet, OWNER, m['id'], [{'product_id': 'p_a_rice', 'quantity': 3}])
    assert result['planning_only'] and result['plans']
    assert all(sum(i['quantity'] for q in p['orders'] for i in q['items']) == 3 for p in result['plans'])
    assert h.budget(m['id'])[0]['reserved_minor'] == 0


def test_capture_unknown_restart_does_not_charge_twice(h):
    p = Provider(); p.fail_capture_once = True
    recovery, job, m, q, body = operation(h, p)
    assert recovery.advance(OWNER, job['id'])['status'] == 'unknown'
    assert h.budget(m['id'])[0]['reserved_minor'] == q['total_minor']
    retry = Recovery(h.wallet, lambda: p)
    result = retry.advance(OWNER, job['id'])
    assert result['status'] == 'confirmed' and result['budget_state'] == 'spent'
    assert p.capture_calls == 1
    assert retry.advance(OWNER, job['id'])['status'] == 'confirmed'
    assert h.budget(m['id'])[0]['paid_minor'] == q['total_minor']


def test_lost_session_id_recovers_same_creation(h):
    p = Provider(); p.fail_create_once = True
    r, job, m, q, body = operation(h, p)
    assert r.advance(OWNER, job['id'])['status'] == 'unknown'
    assert r.create(OWNER, 'purchase1', body)['id'] == job['id']
    assert r.advance(OWNER, job['id'])['status'] == 'confirmed'
    assert len(p.sessions) == 1


@pytest.mark.parametrize('refund_status', ['pending', 'failed', 'requires_action'])
def test_unresolved_refund_does_not_release_money(h, refund_status):
    p = Provider(); p.refund_status = refund_status
    r, job, m, q, _ = operation(h, p, 'order_failure')
    out = r.advance(OWNER, job['id'])
    assert out['order_state'] == 'failed'
    assert h.budget(m['id'])[0]['paid_minor'] == q['total_minor']
    p.refund_status = 'succeeded'
    assert r.advance(OWNER, job['id'])['status'] == 'refunded'
    assert h.budget(m['id'])[0]['paid_minor'] == 0
    r.advance(OWNER, job['id'])
    assert h.budget(m['id'])[0]['paid_minor'] == 0


def test_revoke_before_capture_cancels_and_release_requires_retrieval(h):
    p = Provider()
    r, job, m, q, _ = operation(h, p)
    h.wallet.revoke_mandate(OWNER, 'revoke', m['id'], {})
    out = r.advance(OWNER, job['id'])
    assert p.capture_calls == 0 and out['budget_state'] == 'held'
    h.clock.advance(minutes=30)
    assert r.advance(OWNER, job['id'])['budget_state'] == 'released'


def test_exact_quote_and_owner_binding(h):
    p = Provider()
    r, job, m, q, body = operation(h, p)
    with pytest.raises(ApiError):
        r.get(Actor('other', 'user'), job['id'])
    with pytest.raises(ApiError):
        r.create(OWNER, 'purchase1', {**body, 'scenario': 'order_failure'})
    with pytest.raises(ApiError):
        r.create(OWNER, 'different', {**body, 'approved_quote_hash': '0' * 64})


def test_external_hold_competes_with_normal_wallet(h):
    p = Provider()
    r, job, m, q, _ = operation(h, p)
    for n in range(2):
        h.buy(m['id']) if n == 0 else None
    decision = h.authorize(m['id'], h.quote()['id']).json()
    assert decision['status'] == 'refused'
    assert any(v['code'] == 'PERIOD_BUDGET_EXCEEDED' for v in decision['violations'])


def test_unknown_operation_blocks_normal_authorization(h):
    p = Provider(); p.fail_create_once = True
    r, job, m, q, _ = operation(h, p)
    r.advance(OWNER, job['id'])
    assert h.authorize(m['id'], q['id']).status_code == 409


def test_owner_signed_credential_tamper_key_swap_and_revocation(h):
    m = h.confirm(); c = Credentials(h.wallet)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(public).decode().rstrip('=')
    document = c.template(OWNER, m['id'], x)
    issuer = did(x)
    token = jwt.encode(document, private, algorithm='EdDSA', headers={'typ': 'vc+jwt', 'kid': issuer + '#' + issuer.removeprefix('did:key:')})
    assert c.verify(OWNER, token)['valid']
    with pytest.raises(ApiError):
        c.template(OWNER, m['id'], base64.urlsafe_b64encode(b'1' * 32).decode().rstrip('='))
    document['credentialSubject']['policy']['per_order_limit_minor'] += 1
    tampered = jwt.encode(document, private, algorithm='EdDSA', headers={'typ': 'vc+jwt', 'kid': issuer + '#' + issuer.removeprefix('did:key:')})
    with pytest.raises(ApiError):
        c.verify(OWNER, tampered)
    h.wallet.revoke_mandate(OWNER, 'revoke', m['id'], {})
    with pytest.raises(ApiError):
        c.verify(OWNER, token)


def test_study_timing_failure_sealing_and_role_scope(h):
    h.client.app.include_router(build_commerce_router(h.wallet), prefix='/api/v1')
    q = h.quote()
    body = {'participant': 'P01', 'task_id': q['basket_hash'], 'mode': 'manual', 'quote_id': q['id'], 'setup_seconds': 12, 'execution_mode': 'human'}
    assert h.client.post('/api/v1/commerce/studies', headers=AGENT, json=body).status_code == 403
    run = h.client.post('/api/v1/commerce/studies', headers=USER, json=body).json()
    h.clock.advance(seconds=34)
    result = {'actions': 7, 'errors': 1, 'outcome': 'failed', 'source_url': 'https://shop.example.com', 'observed_at': '2026-10-07T10:00:00+08:00', 'note': ''}
    out = h.client.post('/api/v1/commerce/studies/' + run['id'] + '/finish', headers=USER, json=result)
    assert out.json()['elapsed_seconds'] == 34 and out.json()['outcome'] == 'failed'
    assert h.client.post('/api/v1/commerce/studies/' + run['id'] + '/finish', headers=USER, json={**result, 'actions': 6}).status_code == 409


def test_worker_reconciles_persisted_unknown(h):
    from mandate.commerce.worker import sweep
    p = Provider(); p.fail_capture_once = True
    r, job, m, q, _ = operation(h, p)
    r.advance(OWNER, job['id'])
    assert sweep(Recovery(h.wallet, lambda: p)) == 1
    assert r.get(OWNER, job['id'])['status'] == 'confirmed'
    assert sweep(r) == 0 and p.capture_calls == 1


def test_expired_unknown_creation_keeps_budget(h):
    p = Provider(); p.fail_create_once = True
    r, job, m, q, _ = operation(h, p)
    r.advance(OWNER, job['id'])
    h.clock.advance(hours=24)
    out = r.advance(OWNER, job['id'])
    assert out['status'] == 'needs_operator'
    assert h.budget(m['id'])[0]['reserved_minor'] == q['total_minor']




def test_planner_finds_two_store_exact_pack_combination_without_payment(h):
    from mandate.payments.catalog import Catalog
    data = copy.deepcopy(h.wallet.catalog._data)
    products = {p['id']: p for p in data['products']}
    for original, pid, price in [('p_a_milk', 'p_b_milk', 1000), ('p_a_rice', 'p_b_rice', 10000)]:
        data['products'].append({**products[original], 'id': pid, 'merchant_id': 'demo_store_b', 'unit_price_minor': price})
    # Explicit synthetic fee fixture: both stores free, never production evidence.
    for context in data['delivery_contexts']:
        for fee in context['fee_rules']:
            fee.update(min_subtotal_minor=0, max_subtotal_minor=None, amount_minor=0)
    h.wallet.catalog = Catalog(data)
    m = h.confirm()
    result = optimize(h.wallet, OWNER, m['id'], [{'product_id': 'p_a_milk', 'quantity': 1}, {'product_id': 'p_a_rice', 'quantity': 1}])
    best = result['plans'][0]
    assert best['store_count'] == 2 and best['total_minor'] == 9900
    assert sorted((line['product_id'], line['quantity']) for line in best['selection']) == [('p_a_rice', 1), ('p_b_milk', 1)]
    assert h.budget(m['id'])[0]['reserved_minor'] == 0
