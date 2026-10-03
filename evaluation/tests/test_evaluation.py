"""services/api/.venv/bin/python -m pytest evaluation -q      (from the repo root)"""

import evaluation  # noqa: F401  (puts services/api on sys.path)
from evaluation import extra, live, scenarios


def test_live_http_race_retry_and_revoke():
    r = live.run()
    unsafe, wallet = r["race"]
    assert (unsafe["approved"], unsafe["overspend_minor"]) == (2, 20000)
    assert (wallet["approved"], wallet["refused"], wallet["overspend_minor"]) == (1, 1, 0)
    assert wallet["refusals"][0]["code"] == "PERIOD_BUDGET_EXCEEDED"
    assert wallet["refusals"][0]["rule_id"].endswith("/period:calendar_week")
    assert r["retry"]["same_receipt"] and r["retry"]["extra_debit_minor"] == 0
    assert r["revoke"]["refusal"]["code"] == "MANDATE_REVOKED"
    assert r["revoke"]["earlier_payment_still_paid"]


def test_deterministic_scenarios_all_pass():
    results, _, _ = scenarios.run_all()
    assert len(results) == 20
    assert [r["id"] for r in results if not r["passed"]] == []


def test_extra_items():
    assert [e["after_decision"] for e in extra.escalation()] == \
        ["completed", "refused:APPROVAL_DENIED", "refused:APPROVAL_EXPIRED"]
    assert [f["status"] for f in extra.formal()] == ["counterexample_found", "no_counterexample_within_bound"]
    a = extra.audit()
    assert a["detected"] == a["of"] == 5 and a["cases"][0]["status"] == "valid_through_checkpoint"
    assert extra.route_costs()["routes"]
