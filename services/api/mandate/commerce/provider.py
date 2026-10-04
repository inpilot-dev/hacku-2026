"""Persistent local payment simulator. No external service or real funds.

Provider objects commit independently of the wallet's budget transaction so
lost-response/restart recovery can be demonstrated without a payment API key.
Stable operation IDs make creation, capture, cancellation and refund replayable.
"""
import json
from mandate.payments.errors import ApiError


class Unknown(Exception):
    pass


class Sandbox:
    name = 'local_sandbox'

    def __init__(self, wallet):
        self.wallet = wallet

    def _read(self, job):
        with self.wallet.db.read() as conn:
            row = conn.execute('SELECT body_json FROM commerce_sandbox_objects WHERE id=?', (job['id'],)).fetchone()
        if not row:
            raise Unknown('Sandbox provider object is missing; retain the same operation.')
        obj = json.loads(row[0])
        if any(obj[k] != job[k] for k in ('owner_id', 'quote_hash', 'amount_minor', 'currency')):
            raise ApiError(409, 'PROVIDER_MISMATCH', 'Sandbox object does not match the exact approved purchase.')
        return obj

    def _save(self, conn, obj):
        conn.execute('UPDATE commerce_sandbox_objects SET body_json=? WHERE id=?', (json.dumps(obj), obj['id']))

    def create(self, job):
        with self.wallet.db.write_tx() as conn:
            obj = {k: job[k] for k in ('id', 'owner_id', 'quote_hash', 'amount_minor', 'currency')}
            obj.update(session_id='sb_session_' + job['id'], payment_id='sb_payment_' + job['id'], status='requires_capture', refund_id=None, refund_status=None, capture_response_lost=False, refund_queries=0)
            conn.execute('INSERT OR IGNORE INTO commerce_sandbox_objects VALUES (?,?)', (job['id'], json.dumps(obj)))
        obj = self._read(job)
        return {'id': obj['session_id'], 'url': None}

    def retrieve(self, job):
        obj = self._read(job)
        return {'id': obj['payment_id'], 'status': obj['status'], 'amount_received': obj['amount_minor'] if obj['status'] == 'succeeded' else 0, 'simulated': True}

    def capture(self, job):
        self._read(job)
        lost = False
        with self.wallet.db.write_tx() as conn:
            obj = json.loads(conn.execute('SELECT body_json FROM commerce_sandbox_objects WHERE id=?', (job['id'],)).fetchone()[0])
            if obj['status'] == 'canceled':
                raise ApiError(409, 'PROVIDER_CANCELED', 'The sandbox payment is already canceled.')
            if obj['status'] != 'succeeded':
                obj['status'] = 'succeeded'
                if job['scenario'] == 'lost_capture_response' and not obj['capture_response_lost']:
                    obj['capture_response_lost'] = True
                    lost = True
                self._save(conn, obj)
        if lost:
            # Commit first, then lose the response: next retrieval proves the
            # existing capture. This never submits a second payment.
            raise Unknown('Injected sandbox response loss after capture.')
        return self.retrieve(job)

    def cancel(self, job):
        self._read(job)
        with self.wallet.db.write_tx() as conn:
            obj = json.loads(conn.execute('SELECT body_json FROM commerce_sandbox_objects WHERE id=?', (job['id'],)).fetchone()[0])
            if obj['status'] == 'succeeded':
                raise ApiError(409, 'PROVIDER_ALREADY_CAPTURED', 'Captured sandbox payments require refund recovery.')
            obj['status'] = 'canceled'
            self._save(conn, obj)

    def refund(self, job):
        self._read(job)
        with self.wallet.db.write_tx() as conn:
            obj = json.loads(conn.execute('SELECT body_json FROM commerce_sandbox_objects WHERE id=?', (job['id'],)).fetchone()[0])
            if obj['status'] != 'succeeded':
                raise ApiError(409, 'PROVIDER_NOT_CAPTURED', 'Only a captured sandbox payment can be refunded.')
            if not obj['refund_id']:
                obj['refund_id'] = 'sb_refund_' + job['id']
                obj['refund_status'] = 'pending' if job['scenario'] == 'pending_refund' else 'failed' if job['scenario'] == 'failed_refund' else 'succeeded'
                self._save(conn, obj)
        return self.retrieve_refund(job, advance=False)

    def retrieve_refund(self, job, advance=True):
        obj = self._read(job)
        if not obj['refund_id'] or (job.get('refund_id') and job['refund_id'] != obj['refund_id']):
            raise ApiError(409, 'PROVIDER_MISMATCH', 'Sandbox refund is not bound to this purchase.')
        if advance and obj['refund_status'] == 'pending':
            with self.wallet.db.write_tx() as conn:
                obj = json.loads(conn.execute('SELECT body_json FROM commerce_sandbox_objects WHERE id=?', (job['id'],)).fetchone()[0])
                obj['refund_queries'] += 1
                if obj['refund_queries'] >= 2:
                    obj['refund_status'] = 'succeeded'
                self._save(conn, obj)
        return {'id': obj['refund_id'], 'status': obj['refund_status'], 'payment_intent': obj['payment_id'], 'amount': obj['amount_minor'], 'simulated': True}
