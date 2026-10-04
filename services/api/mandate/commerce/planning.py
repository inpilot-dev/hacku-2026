"""Read-only checks and bounded basket planning. No invented shipping rules."""
from datetime import timedelta
from itertools import product
import re
import unicodedata

from mandate.payments import policy as rules
from mandate.payments.clock import iso, period_bounds
from mandate.payments.errors import invalid, ApiError


def preview(wallet, actor, policy, parent_id=None):
    now = wallet.clock.now()
    problems = rules.validate_policy(policy, now)
    if problems:
        raise invalid('; '.join(problems))
    leaf = {'id': 'preview', 'version': 1, 'status': 'active', 'expires_at': policy['expires_at'], 'policy': policy}
    chain = [leaf]
    with wallet.db.read() as conn:
        if parent_id:
            parent = wallet._load_mandate(conn, parent_id)
            if not parent or parent['owner_id'] != actor.actor_id:
                raise invalid('Parent mandate is not owned by this user.')
            problems = rules.narrowing_problems(policy, parent['policy'])
            if problems:
                raise invalid('; '.join(problems))
            chain += wallet._chain(conn, parent)
        budgets = []
        for m in chain:
            for limit in m['policy']['period_limits']:
                start, _ = period_bounds(limit['period'], now)
                row = conn.execute('SELECT * FROM budget_periods WHERE mandate_id=? AND period=? AND starts_at=?',
                                   (m['id'], limit['period'], iso(start))).fetchone()
                budgets.append(dict(row) if row else {**limit, 'mandate_id': m['id'], 'paid_minor': 0, 'reserved_minor': 0})
    cap = policy['per_order_limit_minor']
    threshold = policy['approval_above_minor']
    safe = max(1, min(cap, threshold if threshold else cap, *(b['limit_minor'] for b in budgets)))
    specs = [('within_limit', safe, 'pantry'), ('near_limit', cap, 'pantry'), ('over_limit', cap + 1, 'pantry')]
    if policy['blocked_categories']:
        specs.append(('blocked_category', safe, policy['blocked_categories'][0]))
    cards = []
    for name, amount, category in specs:
        quote = {'id': name, 'currency': 'HKD', 'merchant_id': policy['allowed_merchant_ids'][0],
                 'expires_at': iso(now + timedelta(minutes=10)), 'total_minor': amount, 'charges': [],
                 'items': [{'title': 'Illustrative item', 'category': category, 'category_status': 'curated'}]}
        result = rules.evaluate(chain, quote, budgets, now)
        cards.append({'id': name, 'total_minor': amount, 'category': category, 'status': result.status,
                      'violations': result.hard + result.review, 'rule_ids': result.rule_ids})
    return {'examples': cards, 'illustrative': True, 'mutates_wallet': False, 'evaluated_at': iso(now)}


def normalized(row):
    title = unicodedata.normalize('NFKC', row['title']).casefold()
    return re.sub(r'\s+', ' ', title).strip(), row['unit_label'].casefold().strip()


def optimize(wallet, actor, mandate_id, lines):
    """Fixed quantities, exact title and pack only. Limited enumeration, no auto-purchase."""
    mandate = wallet.get_mandate(actor, mandate_id)
    if mandate['status'] != 'active':
        raise invalid('Planning requires an active mandate.')
    catalog = wallet.catalog.listing()
    by_id = {p['id']: p for p in catalog['products']}
    pools = []
    with wallet.db.read() as conn:
        leaf = wallet._load_mandate(conn, mandate_id)
        chain = wallet._chain(conn, leaf)
        available = []
        for m in chain:
            for limit in m['policy']['period_limits']:
                start, _ = period_bounds(limit['period'], wallet.clock.now())
                b = conn.execute('SELECT * FROM budget_periods WHERE mandate_id=? AND period=? AND starts_at=?', (m['id'], limit['period'], iso(start))).fetchone()
                available.append(b['limit_minor'] - b['paid_minor'] - b['reserved_minor'] if b else limit['limit_minor'])
    for line in lines:
        base = by_id.get(line['product_id'])
        if not base:
            raise invalid('Unknown product.')
        equivalents = [p for p in catalog['products'] if p['available'] and normalized(p) == normalized(base)
                       and p['merchant_id'] in mandate['policy']['allowed_merchant_ids']
                       and p['category'] not in mandate['policy']['blocked_categories']
                       and p['category_status'] in ('curated', 'verified')]
        if not equivalents:
            return {'plans': [], 'evaluated': 0, 'truncated': False, 'planning_only': True,
                    'reason': 'No allowed, available exact-pack equivalent. Your authority was not changed.'}
        pools.append(sorted(equivalents, key=lambda p: (p['unit_price_minor'], p['id']))[:6])
    plans, seen, evaluated, truncated = [], set(), 0, False
    for selection in product(*pools):
        if evaluated >= 512:
            truncated = True
            break
        evaluated += 1
        groups = {}
        for row, line in zip(selection, lines):
            group = groups.setdefault(row['merchant_id'], {})
            group[row['id']] = group.get(row['id'], 0) + line['quantity']
        if len(groups) > 3:
            continue
        key = tuple((m, tuple(sorted(g.items()))) for m, g in sorted(groups.items()))
        if key in seen:
            continue
        seen.add(key)
        priced = []
        for merchant, group in sorted(groups.items()):
            contexts = wallet.catalog.delivery_context_ids(merchant)
            options = []
            for context in contexts:
                try:
                    q = wallet.catalog.price(merchant, [{'product_id': p, 'quantity': qty} for p, qty in group.items()], context)
                    if q['total_minor'] <= mandate['policy']['per_order_limit_minor']:
                        options.append({**q, 'delivery_context_id': context})
                except Exception as exc:
                    # Catalog failures are infeasible candidates, never substituted zero fees.
                    from mandate.payments.catalog import CatalogError
                    if not isinstance(exc, (CatalogError, ApiError)):
                        raise
            if not options:
                break
            priced.append(min(options, key=lambda q: q['total_minor']))
        if len(priced) != len(groups):
            continue
        for q in priced:
            q['id'] = 'planning'
            q['expires_at'] = iso(wallet.clock.now() + timedelta(minutes=10))
        if any(rules.evaluate(chain, q, [], wallet.clock.now()).hard for q in priced):
            continue
        total = sum(q['total_minor'] for q in priced)
        if any(total > amount for amount in available):
            continue
        plans.append({'total_minor': total, 'store_count': len(priced), 'orders': priced,
                      'selection': [{'product_id': p['id'], 'quantity': l['quantity']} for p, l in zip(selection, lines)]})
    plans.sort(key=lambda p: (p['total_minor'], p['store_count']))
    return {'plans': plans[:5], 'evaluated': evaluated, 'truncated': truncated, 'planning_only': True,
            'reason': 'Exact title and pack, fixed quantities, observed catalogue delivery rules. Review each store order; no payment was submitted.'}
