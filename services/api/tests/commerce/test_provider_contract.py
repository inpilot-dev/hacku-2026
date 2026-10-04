from mandate.commerce.provider import Sandbox
from mandate.commerce.recovery import Recovery, digest
from mandate.payments.auth import Actor
from tests.payments.conftest import h

OWNER = Actor('user_demo', 'user')


def create(h, scenario):
    m, q = h.confirm(), h.quote()
    job = Recovery(h.wallet).create(OWNER, scenario, {'mandate_id': m['id'], 'quote_id': q['id'], 'approved_quote_hash': digest(q), 'scenario': scenario})
    return job, m, q


def test_local_provider_requires_no_keys_and_replays_capture(h, monkeypatch):
    monkeypatch.delenv('STRIPE_SECRET_KEY', raising=False)
    job, m, q = create(h, 'happy')
    r = Recovery(h.wallet)
    assert r.advance(OWNER, job['id'])['status'] == 'confirmed'
    assert Recovery(h.wallet).advance(OWNER, job['id'])['budget_state'] == 'spent'
    assert h.budget(m['id'])[0]['paid_minor'] == q['total_minor']


def test_local_lost_capture_response_recovers_after_restart(h):
    job, m, q = create(h, 'lost_capture_response')
    assert Recovery(h.wallet).advance(OWNER, job['id'])['status'] == 'unknown'
    assert h.budget(m['id'])[0]['reserved_minor'] == q['total_minor']
    assert Sandbox(h.wallet).retrieve(job)['status'] == 'succeeded'
    assert Recovery(h.wallet).advance(OWNER, job['id'])['status'] == 'confirmed'
    assert h.budget(m['id'])[0]['paid_minor'] == q['total_minor']


def test_local_pending_refund_keeps_money_until_next_retrieval(h):
    job, m, q = create(h, 'pending_refund')
    out = Recovery(h.wallet).advance(OWNER, job['id'])
    assert out['recovery_state'] == 'pending' and out['budget_state'] == 'spent'
    assert h.budget(m['id'])[0]['paid_minor'] == q['total_minor']
    assert Recovery(h.wallet).advance(OWNER, job['id'])['status'] == 'refunded'
    assert h.budget(m['id'])[0]['paid_minor'] == 0


def test_local_failed_refund_keeps_spent_budget(h):
    job, m, q = create(h, 'failed_refund')
    out = Recovery(h.wallet).advance(OWNER, job['id'])
    assert out['status'] == 'needs_operator' and out['budget_state'] == 'spent'
    assert Recovery(h.wallet).advance(OWNER, job['id'])['status'] == 'needs_operator'
    assert h.budget(m['id'])[0]['paid_minor'] == q['total_minor']


def test_local_order_failure_refunds_exact_capture(h):
    job, m, q = create(h, 'order_failure')
    out = Recovery(h.wallet).advance(OWNER, job['id'])
    assert out['status'] == 'refunded' and out['amount_minor'] == q['total_minor']
    assert h.budget(m['id'])[0]['paid_minor'] == 0


def test_cancel_unknown_capture_retrieves_then_refunds(h):
    job, m, q = create(h, 'lost_capture_response')
    r = Recovery(h.wallet)
    assert r.advance(OWNER, job['id'])['status'] == 'unknown'
    out = r.cancellation(OWNER, job['id'])
    assert out['status'] == 'refunded' and out['order_state'] == 'canceled_after_capture'
    assert h.budget(m['id'])[0]['paid_minor'] == 0
    assert h.budget(m['id'])[0]['reserved_minor'] == 0


def test_retrieved_capture_clears_pending_recovery_label(h):
    job, m, q = create(h, 'lost_capture_response')
    r = Recovery(h.wallet)
    r.advance(OWNER, job['id'])
    out = r.advance(OWNER, job['id'])
    assert out['status'] == 'confirmed' and out['recovery_state'] == 'reconciled'
