import copy
import pytest
from mandate.commerce.groups import Groups
from mandate.commerce.recovery import digest
from mandate.payments.auth import Actor
from mandate.payments.errors import ApiError
from tests.payments.conftest import h, POLICY

OWNER = Actor('user_demo', 'user')


def basket(h, scenarios=('happy', 'happy'), budget=None):
    q1 = h.quote()
    q2 = h.quote({'merchant_id': 'demo_store_b', 'items': [{'product_id': 'p_b_detergent', 'quantity': 1}], 'delivery_context_id': h.wallet.catalog.delivery_context_ids('demo_store_b')[0]})
    policy = copy.deepcopy(POLICY)
    if budget is not None:
        policy['period_limits'][0]['limit_minor'] = q1['total_minor'] + q2['total_minor'] - 1
    m = h.confirm(policy)
    body = {'purchases': [{'mandate_id': m['id'], 'quote_id': q['id'], 'approved_quote_hash': digest(q), 'scenario': scenario} for q, scenario in zip((q1, q2), scenarios)]}
    return m, body, q1['total_minor'] + q2['total_minor']


def test_group_holds_are_atomic_and_replay_bound(h):
    m, body, total = basket(h)
    groups = Groups(h.wallet)
    group = groups.create(OWNER, 'group1', body)
    assert h.budget(m['id'])[0]['reserved_minor'] == total
    assert groups.create(OWNER, 'group1', body)['id'] == group['id']
    assert groups.advance(OWNER, group['id'])['status'] == 'confirmed'
    assert Groups(h.wallet).advance(OWNER, group['id'])['status'] == 'confirmed'
    assert h.budget(m['id'])[0]['paid_minor'] == total
    with pytest.raises(ApiError):
        groups.get(Actor('other', 'user'), group['id'])
    with pytest.raises(ApiError):
        groups.create(OWNER, 'group1', {'purchases': list(reversed(body['purchases']))})


def test_second_refusal_rolls_back_every_hold_and_group(h):
    m, body, total = basket(h, budget='short')
    with pytest.raises(ApiError):
        Groups(h.wallet).create(OWNER, 'group1', body)
    assert h.budget(m['id'])[0]['reserved_minor'] == 0
    assert not Groups(h.wallet).list(OWNER)
    with h.wallet.db.read() as conn:
        assert conn.execute('SELECT COUNT(*) FROM commerce_jobs').fetchone()[0] == 0


def test_partial_order_failure_compensates_first_capture(h):
    m, body, total = basket(h, ('happy', 'order_failure'))
    groups = Groups(h.wallet)
    group = groups.create(OWNER, 'group1', body)
    out = groups.advance(OWNER, group['id'])
    assert out['status'] == 'refunded'
    assert all(j['budget_state'] == 'refunded' for j in out['operations'])
    assert out['operations'][0]['order_state'] == 'group_rolled_back'
    assert h.budget(m['id'])[0]['paid_minor'] == 0
    assert h.budget(m['id'])[0]['reserved_minor'] == 0


def test_unknown_first_capture_does_not_start_second_store(h):
    m, body, total = basket(h, ('lost_capture_response', 'happy'))
    group = Groups(h.wallet).create(OWNER, 'group1', body)
    out = Groups(h.wallet).advance(OWNER, group['id'])
    assert out['status'] == 'unknown' and out['operations'][1]['payment_state'] == 'not_started'
    assert h.budget(m['id'])[0]['reserved_minor'] == total
    assert Groups(h.wallet).advance(OWNER, group['id'])['status'] == 'confirmed'
    assert h.budget(m['id'])[0]['paid_minor'] == total


def test_failed_group_refund_retains_only_failed_store_accounting(h):
    m, body, total = basket(h, ('happy', 'failed_refund'))
    groups = Groups(h.wallet)
    group = groups.create(OWNER, 'group1', body)
    out = groups.advance(OWNER, group['id'])
    assert out['status'] == 'needs_operator'
    first, second = out['operations']
    assert first['budget_state'] == 'refunded' and second['budget_state'] == 'spent'
    assert h.budget(m['id'])[0]['paid_minor'] == second['amount_minor']


def test_group_cancel_before_capture_releases_only_after_retrieval(h):
    m, body, total = basket(h)
    groups = Groups(h.wallet)
    group = groups.create(OWNER, 'group1', body)
    assert groups.cancel(OWNER, group['id'])['status'] == 'recovering'
    assert h.budget(m['id'])[0]['reserved_minor'] == total
    assert groups.advance(OWNER, group['id'])['status'] == 'refunded'
    assert h.budget(m['id'])[0]['reserved_minor'] == 0
