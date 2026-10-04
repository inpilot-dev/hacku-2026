"""Restart-safe bounded polling worker for already-owner-approved test operations.

python -m mandate.commerce.worker [--once]
Uses MANDATE_WALLET_DATA_DIR and the same catalogue and keys as the API.
Never creates a purchase; only reconciles persisted approved jobs.
"""
import argparse
import json
import time
from mandate.payments.auth import Actor
from mandate.payments.dev_app import create_app
from mandate.payments.errors import ApiError
from .recovery import Recovery


def sweep(recovery, limit=20):
    with recovery.wallet.db.read() as conn:
        rows = conn.execute("SELECT id,owner_id FROM commerce_jobs WHERE lease_until<=? AND json_extract(body_json,'$.status') NOT IN ('confirmed','refunded','canceled','needs_operator') ORDER BY rowid LIMIT ?", (time.time(), limit)).fetchall()
    for row in rows:
        try:
            recovery.advance(Actor(row['owner_id'], 'user'), row['id'])
        except ApiError:
            pass  # another worker owns the lease, or configuration is unavailable
    return len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--once', action='store_true'); args = parser.parse_args()
    _, wallet = create_app()
    recovery = Recovery(wallet)
    while True:
        count = sweep(recovery)
        print(json.dumps({'reconciled_candidates': count}), flush=True)
        if args.once:
            break
        time.sleep(5)
