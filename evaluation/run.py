"""Run C1 (live HTTP), C2 (20 deterministic scenarios) and the extra §11 items; write results/latest.json; print a summary.

    services/api/.venv/bin/python -m evaluation.run          (from the repo root)

Every number is measured in this run. Nothing is estimated or carried over.
"""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

from mandate.payments.clock import HKT, iso

from . import extra, live, scenarios
from .common import POLICY, hkd

OUT = Path(__file__).with_name("results") / "latest.json"


def pct(xs: list[float], p: int) -> float | None:
    if len(xs) < 2:
        return round(xs[0], 2) if xs else None
    return round(statistics.quantiles(xs, n=100, method="inclusive")[p - 1], 2)


def rate(n: int, d: int) -> dict:
    return {"count": n, "denominator": d, "rate": round(n / d, 4) if d else None}


def metrics(results: list[dict], auth_ms: list[float], pay_ms: list[float]) -> dict:
    attempts = [a for r in results for a in r["attempts"]]
    legit = [a for a in attempts if a["legit"]]
    blocked = [a for a in attempts if not a["legit"]]
    cap = POLICY["per_order_limit_minor"]
    order_over = [a["amount_minor"] - cap for a in attempts if a["completed"] and a["amount_minor"] > cap]
    retry = [a for r in results if r["category"] == "retry" for a in r["attempts"]]
    return {
        "unauthorized_completed_payments": rate(sum(a["completed"] for a in blocked), len(blocked)),
        "order_cap_overspend": {**rate(len(order_over), sum(a["completed"] for a in attempts)),
                                "amount_minor": sum(order_over)},
        "weekly_cap_overspend": {
            "note": "concurrency scenarios: paid + reserved minus the HK$800 weekly limit, summed",
            **_weekly(results)},
        "duplicate_completed_payments_under_retry": sum(not a["legit"] and a["completed"] for a in retry),
        "legitimate_purchase_completion": rate(sum(a["completed"] for a in legit), len(legit)),
        "false_refusal": rate(sum(not a["completed"] for a in legit), len(legit)),
        "escalation": rate(sum(a["outcome"].startswith("requires_review") for a in attempts), len(attempts)),
        "latency_ms": {
            "where": "in-process FastAPI TestClient + SQLite on this machine, no network, no model",
            "authorize": {"n": len(auth_ms), "p50": pct(auth_ms, 50), "p95": pct(auth_ms, 95)},
            "pay": {"n": len(pay_ms), "p50": pct(pay_ms, 50), "p95": pct(pay_ms, 95)},
        },
    }


def _weekly(results: list[dict]) -> dict:
    # Each concurrency scenario starts with HK$400 paid of HK$800; anything completed above HK$400 overspends.
    over = []
    for r in results:
        if r["category"] == "concurrency":
            spent = 40000 + sum(a["amount_minor"] for a in r["attempts"] if a["completed"])
            over.append(max(spent - 80000, 0))
    return {**rate(sum(o > 0 for o in over), len(over)), "amount_minor": sum(over)}


def main() -> dict:
    c1 = live.run()
    results, auth_ms, pay_ms = scenarios.run_all()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=Path(__file__).parent).stdout.strip()
    out = {
        "schema_version": 1,
        "generated_at": iso(datetime.now(HKT)),
        "git_commit": commit,
        "python": platform.python_version(),
        "catalog": "placeholder (services/api/mandate/payments/fixtures/placeholder_catalog.json), NOT observed data",
        "live_http": c1,
        "deterministic": {
            "scenario_count": len(results),
            "passed": sum(r["passed"] for r in results),
            "failed": sum(not r["passed"] for r in results),
            "metrics": metrics(results, auth_ms, pay_ms),
            "scenarios": results,
        },
        "escalation_outcomes": extra.escalation(),
        "route_costs": extra.route_costs(),
        "formal": extra.formal(),
        "audit": extra.audit(),
        "not_run": "10 model-dependent scenarios (incl. prompt injection) are not run yet",
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return out


def summary(out: dict) -> str:
    d, m = out["deterministic"], out["deterministic"]["metrics"]
    lines = [live.report(out["live_http"]), "",
             f"[C2] deterministic scenarios: {d['scenario_count']} run, {d['passed']} passed, {d['failed']} failed"]
    for r in d["scenarios"]:
        lines.append(f"  {'PASS' if r['passed'] else 'FAIL'} {r['id']} {r['category']:<19} {r['observed']}"
                     + (f"  expected {r['expected']}" if not r["passed"] else "") + (f"  {r['error']}" if r["error"] else ""))
    u, lc, fr = m["unauthorized_completed_payments"], m["legitimate_purchase_completion"], m["false_refusal"]
    lines += [
        f"  unauthorized completed payments {u['count']}/{u['denominator']}; "
        f"order-cap overspend {hkd(m['order_cap_overspend']['amount_minor'])}; "
        f"weekly overspend {hkd(m['weekly_cap_overspend']['amount_minor'])}; "
        f"duplicate payments under retry {m['duplicate_completed_payments_under_retry']}",
        f"  legitimate completion {lc['count']}/{lc['denominator']}; false refusals {fr['count']}/{fr['denominator']}",
        f"  latency authorize p50 {m['latency_ms']['authorize']['p50']}ms p95 {m['latency_ms']['authorize']['p95']}ms; "
        f"pay p50 {m['latency_ms']['pay']['p50']}ms p95 {m['latency_ms']['pay']['p95']}ms ({m['latency_ms']['where']})",
        f"  escalated {m['escalation']['count']}/{m['escalation']['denominator']} attempts; owner decision -> "
        + ", ".join(f"{e['owner_decision']}: {e['after_decision']}" for e in out["escalation_outcomes"]),
        "", "[routes] " + ", ".join(f"{r['route_id']} {hkd(r['gross_minor'] + r['fee_minor'])}"
                                     for r in out["route_costs"]["routes"])
        + f" (recommended {out['route_costs']['recommended_route_id']})",
        "[Z3] " + "; ".join(f"{f['variant']} {f['status']} ({f['solver_result']}, bound {f['max_steps']}, "
                            f"{f['runtime_ms']} ms)" for f in out["formal"]),
        f"[audit] {out['audit']['detected']}/{out['audit']['of']} edits caught: "
        + ", ".join(f"{c['case']} {c['status']}" for c in out["audit"]["cases"]),
        f"  wrote {OUT.relative_to(OUT.parents[2])}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary(main()))
