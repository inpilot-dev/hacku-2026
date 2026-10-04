"""Durable coordinator. Network calls are outside SQLite transactions.

Durable sandbox holds use the same period rows as wallet reservations. A provider
unknown or failed refund NEVER releases them. Worker leases + provider stable
operation keys prevent duplicate calls from becoming duplicate charges.
"""
import hashlib
import json
import time
import uuid
from datetime import timedelta
from mandate.payments import policy as rules
from mandate.payments.clock import iso, parse
from mandate.payments.errors import ApiError, conflict, invalid, not_found
from .provider import Sandbox, Unknown


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class Recovery:
    def __init__(self, wallet, provider_factory=None):
        self.wallet, self.provider_factory = wallet, provider_factory or (lambda: Sandbox(wallet))

    def get(self, actor, job_id):
        with self.wallet.db.read() as conn:
            r = conn.execute('SELECT body_json FROM commerce_jobs WHERE id=? AND owner_id=?', (job_id, actor.actor_id)).fetchone()
        if not r:
            raise not_found('Provider operation')
        return json.loads(r[0])

    def list(self, actor):
        with self.wallet.db.read() as conn:
            return [json.loads(r[0]) for r in conn.execute('SELECT body_json FROM commerce_jobs WHERE owner_id=? ORDER BY rowid DESC LIMIT 50', (actor.actor_id,))]

    def create(self, actor, key, body):
        # Configuration failure is detected before funds are reserved.
        configured = self.provider_factory()
        if hasattr(configured, 'close'):
            configured.close()
        with self.wallet.db.write_tx() as conn:
            return self.create_in_transaction(conn, actor, key, body)

    def create_in_transaction(self, conn, actor, key, body):
        request_hash = digest(body)
        now = self.wallet.clock.now()
        prior = conn.execute('SELECT * FROM commerce_jobs WHERE owner_id=? AND operation_key=?', (actor.actor_id, key)).fetchone()
        if prior:
            if prior['request_hash'] != request_hash:
                raise conflict('Operation key already belongs to a different purchase.')
            return json.loads(prior['body_json'])
        if conn.execute("SELECT 1 FROM commerce_jobs WHERE owner_id=? AND json_extract(body_json,'$.status') IN ('unknown','needs_operator') LIMIT 1", (actor.actor_id,)).fetchone():
            raise conflict('Resolve the existing provider operation before another sandbox purchase.')
        mandate = self.wallet._load_mandate(conn, body['mandate_id'])
        if not mandate or mandate['owner_id'] != actor.actor_id:
            raise not_found('Mandate')
        row = self.wallet._load_quote(conn, body['quote_id'], actor)
        if not row:
            raise not_found('Quote')
        quote = json.loads(row['body_json'])
        if digest(quote) != body['approved_quote_hash']:
            raise conflict('Approve the exact current quote before starting sandbox checkout.')
        chain = self.wallet._chain(conn, mandate)
        budgets = self.wallet._ensure_periods(conn, chain, now)
        assessment = self.wallet._risk(conn, chain, quote, budgets, now)
        ev = rules.evaluate(chain, quote, budgets, now, recent_purchases=self.wallet._recent_purchases(conn, chain, now), risk=assessment.reasons)
        if ev.hard:
            raise invalid('The purchase is outside its mandate.', violations=ev.hard)
        frozen = self.wallet._frozen_card_violation(conn, chain)
        if frozen:
            raise invalid('A card in the mandate chain is frozen.', violations=[frozen])
        # This user-only API is explicit owner approval for THIS exact quote,
        # not a grant of greater authority to an agent.
        job = {'id': 'ext_' + uuid.uuid4().hex, 'owner_id': actor.actor_id, 'mandate_id': mandate['id'],
               'mandate_version': mandate['version'], 'quote': quote, 'quote_hash': digest(quote),
               'amount_minor': quote['total_minor'], 'currency': 'HKD', 'provider': 'local_sandbox',
               'status': 'queued', 'payment_state': 'not_started', 'order_state': 'not_created',
               'recovery_state': 'none', 'budget_state': 'held', 'period_ids': [b['id'] for b in budgets],
               'created_at': iso(now), 'approved_at': iso(now), 'attempts': 0,
               'owner_approved_review_reasons': ev.review, 'risk_assessment': assessment.summary(),
               'scenario': body['scenario'], 'session_id': None, 'payment_id': None, 'refund_id': None,
               'checkout_url': None, 'cancel_requested': False, 'events': [],
               'boundary': 'Local simulated payment and synthetic order. No real funds or retailer integration.'}
        for b in budgets:
            conn.execute('UPDATE budget_periods SET reserved_minor=reserved_minor+?, version=version+1 WHERE id=?', (job['amount_minor'], b['id']))
        self.event(job, 'Exact quote approved and budget reserved; provider has not been called.')
        conn.execute('INSERT INTO commerce_jobs(id,owner_id,mandate_id,operation_key,request_hash,body_json) VALUES(?,?,?,?,?,?)',
                     (job['id'], actor.actor_id, mandate['id'], key, request_hash, json.dumps(job)))
        self.audit(conn, job, 'sandbox.reserved')
        return job

    def audit(self, conn, job, kind):
        self.wallet._event(conn, job['owner_id'], kind,
                           {k: job[k] for k in ('id', 'quote_hash', 'amount_minor', 'currency', 'provider', 'status', 'payment_state', 'order_state', 'recovery_state', 'budget_state')},
                           actor='sandbox_coordinator', now=self.wallet.clock.now(), mandate_id=job['mandate_id'], transaction_id=job['id'])

    def event(self, job, message):
        job['events'].append({'at': iso(self.wallet.clock.now()), 'message': message})
        job['events'] = job['events'][-80:]

    def save(self, job):
        with self.wallet.db.write_tx() as conn:
            previous = json.loads(conn.execute('SELECT body_json FROM commerce_jobs WHERE id=?', (job['id'],)).fetchone()[0])
            conn.execute('UPDATE commerce_jobs SET body_json=? WHERE id=?', (json.dumps(job), job['id']))
            if any(previous[k] != job[k] for k in ('status', 'payment_state', 'order_state', 'recovery_state')):
                self.audit(conn, job, 'sandbox.reconciled')

    def money(self, job, target):
        if job['budget_state'] == target:
            return
        with self.wallet.db.write_tx() as conn:
            current = json.loads(conn.execute('SELECT body_json FROM commerce_jobs WHERE id=?', (job['id'],)).fetchone()[0])
            prior = current['budget_state']
            amount = job['amount_minor']
            if prior == target:
                job['budget_state'] = target
                return
            if (prior, target) not in {('held', 'spent'), ('held', 'released'), ('spent', 'refunded')}:
                raise conflict('Invalid sandbox budget transition.')
            for pid in job['period_ids']:
                if prior == 'held':
                    conn.execute('UPDATE budget_periods SET reserved_minor=reserved_minor-?, paid_minor=paid_minor+?, version=version+1 WHERE id=?',
                                 (amount, amount if target == 'spent' else 0, pid))
                else:
                    conn.execute('UPDATE budget_periods SET paid_minor=paid_minor-?, version=version+1 WHERE id=?', (amount, pid))
            job['budget_state'] = target
            conn.execute('UPDATE commerce_jobs SET body_json=? WHERE id=?', (json.dumps(job), job['id']))
            self.audit(conn, job, 'sandbox.' + target)

    def cancellation(self, actor, job_id):
        with self.wallet.db.write_tx() as conn:
            row = conn.execute('SELECT * FROM commerce_jobs WHERE id=? AND owner_id=?', (job_id, actor.actor_id)).fetchone()
            if not row:
                raise not_found('Provider operation')
            if row['lease_until'] > time.time():
                raise conflict('An operation is in flight. Retrieve its result before cancellation.')
            job = json.loads(row['body_json'])
            if job['payment_state'] == 'captured':
                raise conflict('Captured payments require recovery, not cancellation.')
            job['cancel_requested'] = True
            conn.execute('UPDATE commerce_jobs SET body_json=? WHERE id=?', (json.dumps(job), job_id))
        return self.advance(actor, job_id)

    def still_allowed(self, job):
        with self.wallet.db.read() as conn:
            leaf = self.wallet._load_mandate(conn, job['mandate_id'])
            if not leaf or leaf['version'] != job['mandate_version']:
                return False
            chain = self.wallet._chain(conn, leaf)
            budgets = [dict(conn.execute('SELECT * FROM budget_periods WHERE id=?', (p,)).fetchone()) for p in job['period_ids']]
            # This job already holds its money; do not count that hold twice.
            for b in budgets:
                b['reserved_minor'] -= job['amount_minor']
            now = self.wallet.clock.now()
            recent = self.wallet._recent_purchases(conn, chain, now)
            for m in chain:
                velocity = m['policy'].get('velocity_limit')
                if velocity and (now - parse(job['created_at'])).total_seconds() < velocity['window_minutes'] * 60:
                    recent[m['id']] = max(0, recent.get(m['id'], 0) - 1)
            result = rules.evaluate(chain, job['quote'], budgets, now, recent_purchases=recent)
            try:
                q = job['quote']
                current = self.wallet.catalog.price(q['merchant_id'], [{'product_id': i['product_id'], 'quantity': i['quantity']} for i in q['items']], q['delivery_context_id'])
                unchanged = all(current[k] == q[k] for k in ('revision', 'total_minor', 'items', 'charges'))
            except Exception:
                unchanged = False
            return not result.hard and unchanged and not self.wallet._frozen_card_violation(conn, chain)

    def advance(self, actor, job_id):
        self.get(actor, job_id)  # ownership before worker claim
        with self.wallet.db.write_tx() as conn:
            claimed = conn.execute('UPDATE commerce_jobs SET lease_until=? WHERE id=? AND lease_until<=?', (time.time() + 300, job_id, time.time()))
            if claimed.rowcount != 1:
                raise conflict('Provider operation is already in flight. Refresh, do not start another purchase.')
        job = self.get(actor, job_id)
        provider = None
        try:
            if job['status'] in ('refunded', 'canceled') or (job['status'] == 'confirmed' and not job.get('force_refund')):
                return job
            provider = self.provider_factory()
            job['attempts'] += 1
            if getattr(provider, 'name', '') != 'local_sandbox' and (self.wallet.clock.now() - parse(job['created_at'])).total_seconds() >= 23 * 3600 and (not job['session_id'] or (job['recovery_state'] == 'refund_requested' and not job['refund_id'])):
                raise ApiError(409, 'OPERATION_TOO_OLD', 'Provider idempotency recovery window exceeded; operator verification required.')
            if not job['session_id']:
                # Persist the creation intent before the call. Reuse the same
                # provider key after a crash, even if the response id was lost.
                job['payment_state'] = 'authorizing'
                self.save(job)
                session = provider.create(job)
                job.update(session_id=session['id'], checkout_url=session['url'], status='awaiting_payment')
                self.save(job)
            payment = provider.retrieve(job)
            if payment.get('id'):
                job['payment_id'] = payment['id']
            status = payment['status']
            if status == 'canceled':
                job.update(status='canceled', payment_state='canceled', order_state='not_created')
                self.money(job, 'released')
            elif status == 'requires_capture':
                job['payment_state'] = 'authorized'
                self.save(job)
                if job['cancel_requested'] or not self.still_allowed(job):
                    provider.cancel(job)
                    # Next retrieval, not the request, proves cancellation.
                    job['status'] = 'recovering'
                    self.event(job, 'Cancel requested; budget remains held until provider retrieval.')
                else:
                    job['payment_state'] = 'capturing'
                    self.save(job)
                    provider.capture(job)
                    # Retrieve rather than trusting the action response.
                    payment = provider.retrieve(job)
                    status = payment['status']
            elif job['cancel_requested'] and status not in ('succeeded', 'canceled'):
                provider.cancel(job)
                self.event(job, 'Checkout expiry requested; awaiting provider retrieval.')
            if status == 'succeeded':
                job['payment_state'] = 'captured'
                self.money(job, 'spent')
                if job.get('force_refund') or job['cancel_requested'] or job['scenario'] in ('order_failure', 'pending_refund', 'failed_refund'):
                    job.update(order_state='group_rolled_back' if job.get('force_refund') else 'canceled_after_capture' if job['cancel_requested'] else 'failed', recovery_state='refund_requested', status='recovering')
                    self.save(job)
                    if not job['refund_id']:
                        refund = provider.refund(job)
                        job['refund_id'] = refund['id']
                        self.save(job)
                    refund = provider.retrieve_refund(job)
                    if refund['status'] == 'succeeded':
                        job.update(status='refunded', recovery_state='refunded')
                        self.money(job, 'refunded')
                    elif refund['status'] in ('failed', 'canceled', 'requires_action'):
                        job.update(status='needs_operator', recovery_state='failed')
                    else:
                        job.update(status='recovering', recovery_state='pending')
                else:
                    job.update(status='confirmed', order_state='confirmed_synthetic', recovery_state='reconciled' if job['recovery_state'] == 'pending' else 'none')
            elif status not in ('canceled', 'requires_capture'):
                job.update(status='awaiting_payment', payment_state='requires_action')
            self.event(job, 'Provider retrieved: ' + status + '; order: ' + job['order_state'] + '; recovery: ' + job['recovery_state'])
        except Unknown:
            job.update(status='unknown', recovery_state='pending')
            self.event(job, 'Response unavailable. Retrieve the same operation; no new charge and no released budget.')
        except ApiError as exc:
            job.update(status='needs_operator', recovery_state='needs_operator')
            self.event(job, exc.message)
        finally:
            if provider is not None and hasattr(provider, 'close'):
                provider.close()
            self.save(job)
            with self.wallet.db.write_tx() as conn:
                conn.execute('UPDATE commerce_jobs SET lease_until=0 WHERE id=?', (job_id,))
        return job
