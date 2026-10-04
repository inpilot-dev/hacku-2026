"""Participant-entered study records. Server timing is not proof of human participation."""
import json
import uuid
from mandate.payments.clock import iso, parse
from mandate.payments.errors import invalid, not_found, conflict


class Studies:
    def __init__(self, wallet):
        self.wallet = wallet

    def list(self, actor):
        with self.wallet.db.read() as conn:
            return [json.loads(r[0]) for r in conn.execute(
                'SELECT body_json FROM commerce_studies WHERE owner_id=? ORDER BY rowid DESC LIMIT 100', (actor.actor_id,))]

    def start(self, actor, body):
        quote = self.wallet.get_quote(actor, body['quote_id'])
        run = {'id': 'study_' + uuid.uuid4().hex, **body, 'quote': quote, 'started_at': iso(self.wallet.clock.now()),
               'finished_at': None, 'elapsed_seconds': None, 'actions': None, 'errors': None, 'outcome': None,
               'participation': 'participant_entered_not_independently_verified',
               'boundary': 'Reviewed basket / checkout readiness; no real purchase', 'setup_seconds': body['setup_seconds']}
        with self.wallet.db.write_tx() as conn:
            if conn.execute("SELECT 1 FROM commerce_studies WHERE owner_id=? AND json_extract(body_json,'$.finished_at') IS NULL", (actor.actor_id,)).fetchone():
                raise conflict('Finish or abandon the active trial before starting another.')
            conn.execute('INSERT INTO commerce_studies VALUES (?,?,?)', (run['id'], actor.actor_id, json.dumps(run)))
        return run

    def finish(self, actor, run_id, body):
        with self.wallet.db.write_tx() as conn:
            row = conn.execute('SELECT * FROM commerce_studies WHERE id=? AND owner_id=?', (run_id, actor.actor_id)).fetchone()
            if not row:
                raise not_found('Study run')
            run = json.loads(row['body_json'])
            if run['finished_at']:
                if run['result'] != body:
                    raise conflict('This result is already sealed.')
                return run
            run.update(body)
            run['result'] = body
            run['finished_at'] = iso(self.wallet.clock.now())
            run['elapsed_seconds'] = max(0, (parse(run['finished_at']) - parse(run['started_at'])).total_seconds())
            conn.execute('UPDATE commerce_studies SET body_json=? WHERE id=?', (json.dumps(run), run_id))
        return run
