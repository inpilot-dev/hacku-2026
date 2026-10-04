"""Atomic group holds and compensating recovery for owner-approved split baskets."""
import json
import time
import uuid
from mandate.payments.auth import Actor
from mandate.payments.errors import conflict, not_found, invalid
from .recovery import Recovery, digest


class Groups:
    def __init__(self, wallet, recovery=None):
        self.wallet = wallet
        self.recovery = recovery or Recovery(wallet)

    def get(self, actor, group_id):
        with self.wallet.db.read() as conn:
            row = conn.execute('SELECT body_json FROM commerce_groups WHERE id=? AND owner_id=?', (group_id, actor.actor_id)).fetchone()
        if not row:
            raise not_found('Basket group')
        group = json.loads(row[0])
        group['operations'] = [self.recovery.get(actor, jid) for jid in group['job_ids']]
        return group

    def list(self, actor):
        with self.wallet.db.read() as conn:
            ids = [r[0] for r in conn.execute('SELECT id FROM commerce_groups WHERE owner_id=? ORDER BY rowid DESC LIMIT 30', (actor.actor_id,))]
        return [self.get(actor, gid) for gid in ids]

    def create(self, actor, key, body):
        request_hash = digest(body)
        with self.wallet.db.write_tx() as conn:
            previous = conn.execute('SELECT * FROM commerce_groups WHERE owner_id=? AND operation_key=?', (actor.actor_id, key)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash:
                    raise conflict('Group key belongs to different approved baskets.')
                group = json.loads(previous['body_json'])
            else:
                group = {'id': 'grp_' + uuid.uuid4().hex, 'owner_id': actor.actor_id, 'job_ids': [], 'status': 'queued', 'total_minor': 0, 'rollback_requested': False, 'boundary': 'Local sandbox split orders; all holds are atomic, recovery is compensating rather than instantaneous.'}
                merchants = set()
                for index, purchase in enumerate(body['purchases']):
                    job = self.recovery.create_in_transaction(conn, actor, group['id'] + ':' + str(index), purchase)
                    if job['quote']['merchant_id'] in merchants:
                        raise invalid('A split basket must use distinct stores.')
                    merchants.add(job['quote']['merchant_id'])
                    job['group_id'] = group['id']
                    conn.execute('UPDATE commerce_jobs SET body_json=? WHERE id=?', (json.dumps(job), job['id']))
                    group['job_ids'].append(job['id'])
                    group['total_minor'] += job['amount_minor']
                # Any refusal above rolls back EVERY hold and audit event.
                conn.execute('INSERT INTO commerce_groups(id,owner_id,operation_key,request_hash,body_json) VALUES(?,?,?,?,?)', (group['id'], actor.actor_id, key, request_hash, json.dumps(group)))
        return self.get(actor, group['id'])

    def save(self, group):
        value = {k: v for k, v in group.items() if k != 'operations'}
        with self.wallet.db.write_tx() as conn:
            conn.execute('UPDATE commerce_groups SET body_json=? WHERE id=?', (json.dumps(value), group['id']))

    def cancel(self, actor, group_id):
        group = self.get(actor, group_id)
        with self.wallet.db.write_tx() as conn:
            row = conn.execute('SELECT lease_until FROM commerce_groups WHERE id=?', (group_id,)).fetchone()
            if row[0] > time.time():
                raise conflict('Basket group is in flight; retrieve it before cancellation.')
            group.update(rollback_requested=True, status='recovering')
            value = {k: v for k, v in group.items() if k != 'operations'}
            conn.execute('UPDATE commerce_groups SET body_json=? WHERE id=?', (json.dumps(value), group_id))
        return self.advance(actor, group_id)

    def compensate(self, actor, group):
        # A worker must not overwrite a concurrent single-job transition.
        with self.wallet.db.write_tx() as conn:
            for jid in group['job_ids']:
                row = conn.execute('SELECT * FROM commerce_jobs WHERE id=?', (jid,)).fetchone()
                if row['lease_until'] > time.time():
                    raise conflict('A group payment is still in flight; retrieve the group again.')
                job = json.loads(row['body_json'])
                if job['status'] in ('refunded', 'canceled'):
                    continue
                job['force_refund'] = True
                job['cancel_requested'] = True
                conn.execute('UPDATE commerce_jobs SET body_json=? WHERE id=?', (json.dumps(job), jid))
        for jid in group['job_ids']:
            self.recovery.advance(actor, jid)
        jobs = [self.recovery.get(actor, jid) for jid in group['job_ids']]
        group['status'] = 'refunded' if all(j['budget_state'] in ('released', 'refunded') for j in jobs) else 'needs_operator' if any(j['status'] == 'needs_operator' for j in jobs) else 'recovering'

    def advance(self, actor, group_id):
        group = self.get(actor, group_id)
        with self.wallet.db.write_tx() as conn:
            claimed = conn.execute('UPDATE commerce_groups SET lease_until=? WHERE id=? AND lease_until<=?', (time.time() + 300, group_id, time.time()))
            if claimed.rowcount != 1:
                raise conflict('Basket group is in flight; retrieve the same group.')
        try:
            if group['status'] in ('confirmed', 'refunded'):
                return self.get(actor, group_id)
            if not group['rollback_requested']:
                for jid in group['job_ids']:
                    job = self.recovery.advance(actor, jid)
                    if job['order_state'] in ('failed', 'canceled_after_capture', 'group_rolled_back') or job['status'] in ('refunded', 'canceled', 'needs_operator'):
                        group['rollback_requested'] = True
                        self.save(group)  # persist compensation intent before requests
                        break
                    if job['status'] != 'confirmed':
                        group['status'] = 'unknown' if job['status'] == 'unknown' else 'recovering'
                        return self.get_after_save(actor, group)
            if group['rollback_requested']:
                self.compensate(actor, group)
            else:
                group['status'] = 'confirmed'
            return self.get_after_save(actor, group)
        finally:
            with self.wallet.db.write_tx() as conn:
                conn.execute('UPDATE commerce_groups SET lease_until=0 WHERE id=?', (group_id,))

    def get_after_save(self, actor, group):
        self.save(group)
        return self.get(actor, group['id'])

    def sweep(self):
        with self.wallet.db.read() as conn:
            rows = conn.execute("SELECT id,owner_id FROM commerce_groups WHERE lease_until<=? AND json_extract(body_json,'$.status') NOT IN ('confirmed','refunded','needs_operator') ORDER BY rowid LIMIT 20", (time.time(),)).fetchall()
        for row in rows:
            try:
                self.advance(Actor(row['owner_id'], 'user'), row['id'])
            except Exception as exc:
                from mandate.payments.errors import ApiError
                if not isinstance(exc, ApiError):
                    raise
        return len(rows)
