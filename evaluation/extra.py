"""Build plan §11 items beyond C1/C2: escalation outcomes, per-route cost, the bounded Z3 run, audit tamper detection.

Each runs against a fresh in-process wallet (same ``H`` as C2) and reports what the code returned in this run.
"""

from __future__ import annotations

import copy
import tempfile

from mandate.audit import GENESIS_HASH, event_hash, read_events
from mandate.audit.checkpoints import CheckpointSigner
from mandate.audit.verifier import verify
from mandate.payments.audit_shim import stream_for_owner
from mandate.verification import model

from .common import NORMAL_BASKET, POLICY, USER, key
from .scenarios import H


def escalation() -> list[dict]:
    """HK$297 order over a HK$200 approval threshold, then the owner approves, denies, or never answers."""
    out = []
    for decision in ("approve", "deny", "no_answer"):
        with tempfile.TemporaryDirectory() as tmp:
            h = H(tmp)
            m = h.confirm({**POLICY, "approval_above_minor": 20000})
            q = h.api.quote(NORMAL_BASKET)["id"]
            first = h.api.authorize(m, q, "txn_escalation")
            approval = first["approval_request"]
            if decision == "no_answer":
                h.clock.advance(minutes=11)  # past the quote-bound approval window
            else:
                h.api.c.post(f"/api/v1/approvals/{approval['id']}/{decision}", headers={**USER, **key()}, json={})
            retry = h.api.authorize(m, q, "txn_escalation")
            final = h.api.pay(retry) if retry["status"] == "approved" else retry
            out.append({"owner_decision": decision, "first_authorization": first["status"],
                        "reasons": [v["code"] for v in approval["reasons"]],
                        "after_decision": final["status"] if final["status"] == "completed"
                        else f"{final['status']}:{final['violations'][0]['code']}",
                        "paid_minor": final["receipt"]["amount_minor"] if final["status"] == "completed" else 0})
    return out


def route_costs() -> dict:
    """The wallet's route comparison for one basket. Placeholder catalog; sandbox rails, nothing is charged."""
    with tempfile.TemporaryDirectory() as tmp:
        h = H(tmp)
        h.confirm()
        q = h.api.quote(NORMAL_BASKET)["id"]
        res = h.api.c.get(f"/api/v1/quotes/{q}/payment-options", headers=USER).json()
    keep = ("route_id", "label", "rail", "eligible", "ineligible_reason", "gross_minor", "fee_minor",
            "reward_minor", "reward_counted", "net_minor", "rank", "evidence_ids")
    return {"note": "charged = gross_minor + fee_minor; reward_minor is the wallet's sourced rebate estimate, "
                    "not money moved",
            "basket": NORMAL_BASKET, "quote_total_minor": res["total_minor"], "rule": res["rule"],
            "recommended_route_id": res["recommended_route_id"],
            "routes": [{k: o[k] for k in keep} for o in res["options"]]}


def formal() -> list[dict]:
    """Same inputs as the Safety Lab button: HK$400 available, two HK$300 purchases, 8 steps, 3 s."""
    keep = ("variant", "status", "solver_result", "max_steps", "transaction_count", "runtime_ms",
            "assumptions", "checked_properties")
    out = []
    for variant in ("unsafe", "atomic"):
        r = model.run(variant, 40000, [30000, 30000], max_steps=8, timeout_ms=3000)
        out.append({**{k: r[k] for k in keep}, "initial_available_minor": 40000,
                    "purchase_amounts_minor": [30000, 30000], "counterexample_steps": len(r["counterexample"])})
    return out


def _rechain(events: list[dict]) -> None:
    """What a careful forger does: renumber and recompute every hash."""
    previous = GENESIS_HASH
    for i, e in enumerate(events, 1):
        e["sequence"], e["previous_hash"] = i, previous
        e["event_hash"] = previous = event_hash(e)


def audit() -> dict:
    """One real purchase, a checkpoint over it, then each kind of edit checked by the verifier logic."""
    with tempfile.TemporaryDirectory() as tmp:
        h = H(tmp)
        h.api.buy(h.confirm(), NORMAL_BASKET)
        stream = stream_for_owner("user_demo")
        with h.wallet.db.read() as conn:
            events = read_events(conn, stream)
    signer = CheckpointSigner.generate()
    cp = signer.sign(stream, events[-1]["sequence"], events[-1]["event_hash"])
    original = {"stream_id": stream, "events": events}

    def edited(fn):
        exp = copy.deepcopy(original)
        fn(exp["events"])
        return exp

    def bump_amount(ev):
        next(e for e in ev if "amount_minor" in e["payload"])["payload"]["amount_minor"] += 1

    cases = {
        "original": (original, cp, "valid_through_checkpoint"),
        "amount_edited": (edited(bump_amount), cp, "invalid"),
        "amount_edited_and_rechained": (edited(lambda ev: (bump_amount(ev), _rechain(ev))), cp, "invalid"),
        "event_deleted_and_rechained": (edited(lambda ev: (ev.pop(1), _rechain(ev))), cp, "invalid"),
        "truncated_before_checkpoint": (edited(lambda ev: ev.__delitem__(slice(2, None))), cp, "invalid"),
        "no_retained_checkpoint": (original, None, "no_trusted_checkpoint"),
    }
    results = []
    for name, (exp, checkpoint, expected) in cases.items():
        r = verify(exp, checkpoint, signer.public_key)
        results.append({"case": name, "expected": expected, "status": r["status"], "valid": r["valid"],
                        "failure_codes": sorted({f["code"] for f in r["failures"]}),
                        "as_expected": r["status"] == expected})
    return {"where": "in-process verify(); the same cases against the separate verifier process run in "
                     "services/api/tests/audit/test_verifier_process.py",
            "events_in_export": len(events), "checkpoint_sequence": cp["sequence"],
            "detected": sum(r["as_expected"] for r in results[1:]), "of": len(results) - 1, "cases": results}
