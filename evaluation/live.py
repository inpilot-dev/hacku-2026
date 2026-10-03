"""C1: the real wallet vs the unsafe baseline over real HTTP (uvicorn subprocesses on ports 8100-8199).

    .venv/bin/python -m evaluation.live            (from the repo root)

Setup per server: weekly HK$800, HK$400 already paid, then two HK$300 authorizations
fired concurrently (asyncio.gather over httpx.AsyncClient). Then, on the wallet only:
payment retry and revoke-after-approval.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from mandate.payments.drafts import InMemoryDrafts

from .common import AGENT, PRESPEND, RACE_BASKET, Api, hkd, key, txn

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable  # the venv running the evaluation, wherever it lives


class AnyEvalDraft(InMemoryDrafts):
    """Any ``draft_eval_*`` ID is a draft from user_demo to agent_student (drafts are Abdullah's module)."""

    def __call__(self, draft_id: str) -> dict | None:
        if draft_id.startswith("draft_eval_"):
            self.register(draft_id, owner_id="user_demo", delegatee_id="agent_student",
                          expires_at="2026-10-31T23:59:59+08:00")
        return super().__call__(draft_id)


def wallet_app():
    """uvicorn factory for the real wallet with a fresh data dir from EVAL_WALLET_DATA_DIR."""
    from mandate.payments.dev_app import create_app
    app, _ = create_app(os.environ["EVAL_WALLET_DATA_DIR"], draft_lookup=AnyEvalDraft())
    return app


def unsafe_app():
    from .unsafe_wallet.app import create_app
    return create_app(float(os.environ.get("EVAL_RACE_DELAY_S", "0.05")))


def free_port(lo=8100, hi=8199) -> int:
    for port in range(lo, hi + 1):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:  # nothing listening
                return port
    raise RuntimeError("no free port in 8100-8199")


@contextmanager
def server(factory: str, env: dict | None = None):
    """Start uvicorn on a free C-range port; stop only that PID on exit."""
    port = free_port()
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "--factory", factory, "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'services' / 'api'}", **(env or {})},
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                httpx.get(url + "/openapi.json", timeout=0.5)
                break
            except httpx.TransportError:
                if proc.poll() is not None:
                    raise RuntimeError(f"{factory} exited with {proc.returncode}")
                time.sleep(0.1)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def _race(url: str, mandate_id: str, quote_ids: list[str]) -> list[dict]:
    async with httpx.AsyncClient(base_url=url, timeout=30) as c:
        async def one(qid):
            res = await c.post("/api/v1/authorizations", headers={**AGENT, **key()},
                               json={"transaction_id": txn(), "mandate_id": mandate_id, "quote_id": qid})
            return res.json()
        return await asyncio.gather(*(one(q) for q in quote_ids))


def race(url: str, label: str) -> dict:
    """Weekly HK$800 with HK$400 paid, then two concurrent HK$300 authorizations; pay whatever was approved."""
    with httpx.Client(base_url=url, timeout=30) as c:
        api = Api(c)
        m = api.confirm(f"draft_eval_{txn()}")
        for b in PRESPEND:
            api.buy(m["id"], b)
        before = api.week(m["id"])
        quotes = [api.quote(RACE_BASKET) for _ in range(2)]
        results = asyncio.run(_race(url, m["id"], [q["id"] for q in quotes]))
        paid = [api.pay(r) for r in results if r["status"] == "approved"]
        after = api.week(m["id"])
    over = after["paid_minor"] + after["reserved_minor"] - after["limit_minor"]
    return {
        "server": label,
        "available_before_minor": before["available_minor"],
        "request_amount_minor": quotes[0]["total_minor"],
        "concurrent_requests": len(results),
        "approved": sum(r["status"] == "approved" for r in results),
        "refused": sum(r["status"] == "refused" for r in results),
        "refusals": [{"code": v["code"], "rule_id": v["rule_id"]}
                     for r in results if r["status"] == "refused" for v in r["violations"]],
        "completed_payments": sum(p["status"] == "completed" for p in paid),
        "week_after": {k: after[k] for k in ("limit_minor", "paid_minor", "reserved_minor")},
        "overspend_minor": max(over, 0),
    }


def retry_and_revoke(url: str) -> dict:
    with httpx.Client(base_url=url, timeout=30) as c:
        api = Api(c)
        # Retry: the same payment three times (same key twice, then a fresh key).
        m = api.confirm(f"draft_eval_{txn()}")
        auth = api.authorize(m["id"], api.quote(RACE_BASKET)["id"])
        k = {**AGENT, **key()}
        first, same_key, new_key = api.pay(auth, headers=k), api.pay(auth, headers=k), api.pay(auth)
        paid_after = api.week(m["id"])["paid_minor"]
        retry = {
            "receipt_ids": [p["receipt"]["id"] for p in (first, same_key, new_key)],
            "same_receipt": first["receipt"] == same_key["receipt"] == new_key["receipt"],
            "replayed_flags": [p["replayed"] for p in (first, same_key, new_key)],
            "paid_minor_after_3_attempts": paid_after,
            "extra_debit_minor": paid_after - first["receipt"]["amount_minor"],
        }
        # Revoke: one payment completes, a second is approved, then the user revokes before it is paid.
        done = api.buy(m["id"], PRESPEND[0])
        pending = api.authorize(m["id"], api.quote(PRESPEND[0])["id"])
        rev = api.revoke(m["id"])
        blocked = api.pay(pending)
        earlier = api.receipt(done["receipt"]["transaction_id"])
        wk = api.week(m["id"])
        revoke = {
            "approved_before_revoke": pending["status"],
            "cancelled_reservation_ids": rev["cancelled_reservation_ids"],
            "payment_after_revoke": blocked["status"],
            "refusal": {"code": blocked["violations"][0]["code"], "rule_id": blocked["violations"][0]["rule_id"]},
            "earlier_payment_still_paid": earlier["http_status"] == 200 and earlier["status"] == "paid",
            "week_after": {k: wk[k] for k in ("paid_minor", "reserved_minor")},
        }
    return {"retry": retry, "revoke": revoke}


def run(race_delay_s: float = 0.05) -> dict:
    data = Path(tempfile.mkdtemp(prefix="wallet_", dir=_data_dir()))
    try:
        with server("evaluation.live:unsafe_app", {"EVAL_RACE_DELAY_S": str(race_delay_s)}) as url:
            unsafe = race(url, "unsafe_baseline")
        with server("evaluation.live:wallet_app", {"EVAL_WALLET_DATA_DIR": str(data)}) as url:
            wallet = race(url, "wallet")
            rr = retry_and_revoke(url)
    finally:
        shutil.rmtree(data, ignore_errors=True)
    return {
        "transport": "real HTTP to uvicorn subprocesses on 127.0.0.1:8100-8199; httpx.AsyncClient + asyncio.gather",
        "catalog": "placeholder (fixtures/placeholder_catalog.json), not observed data",
        "unsafe_artificial_delay_s": race_delay_s,
        "race": [unsafe, wallet],
        **rr,
    }


def _data_dir() -> Path:
    d = Path(__file__).with_name(".data")
    d.mkdir(exist_ok=True)
    return d


def report(r: dict) -> str:
    lines = [f"[C1] {r['transport']}; catalog {r['catalog']}",
             f"     unsafe baseline has an ARTIFICIAL {r['unsafe_artificial_delay_s']}s delay between read and reserve"]
    for x in r["race"]:
        lines.append(f"  race {x['server']:<16} available {hkd(x['available_before_minor'])}, "
                     f"{x['concurrent_requests']}x {hkd(x['request_amount_minor'])} concurrent -> "
                     f"approved {x['approved']}, refused {x['refused']} {x['refusals']}; "
                     f"week paid {hkd(x['week_after']['paid_minor'])} / limit {hkd(x['week_after']['limit_minor'])}, "
                     f"overspend {hkd(x['overspend_minor'])}")
    t, v = r["retry"], r["revoke"]
    lines.append(f"  retry  receipts {t['receipt_ids']} same={t['same_receipt']} replayed={t['replayed_flags']} "
                 f"paid {hkd(t['paid_minor_after_3_attempts'])}, extra debit {hkd(t['extra_debit_minor'])}")
    lines.append(f"  revoke approved-then-revoked -> payment {v['payment_after_revoke']} {v['refusal']}; "
                 f"cancelled {v['cancelled_reservation_ids']}; earlier payment still paid={v['earlier_payment_still_paid']}; "
                 f"week {v['week_after']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(run()))
    sys.exit(0)
