"""Bounded model check of the budget race, unrolled step by step for Z3.

Two transactions (Agent A, Agent B) draw on one available budget. Each step is
one transition by one actor: check, reserve, check_reserve, pay, retry,
cancel, expire, revoke, or idle. The two variants differ in one place only:

- unsafe: ``check`` reads the available amount, ``reserve`` later trusts that
  stale read, so another agent can slip in between.
- atomic: ``check_reserve`` checks and reserves in a single transition.

In both, ``pay`` commits a reservation once, ``retry`` of a paid transaction
returns the same receipt (idempotent commit), and nothing pays after
revocation. We ask Z3 for any run of at most ``max_steps`` transitions that
breaks a property: sat = counterexample, unsat = none within the bound.
"""

from __future__ import annotations

import threading
import time
import uuid

import z3

# transaction phases
IDLE, CHECKED, RESERVED, PAID, RELEASED = range(5)
ACTIONS = ["idle", "check", "reserve", "check_reserve", "pay", "retry", "cancel", "expire", "revoke"]
A = {name: i for i, name in enumerate(ACTIONS)}
AGENTS = ["Agent A", "Agent B"]
USER = 2  # actor index for revoke

PROPERTIES = [
    ("budget", "1. Paid plus active reserved funds never exceed the available budget."),
    ("single_payment", "2. Each transaction produces at most one payment, including on retry."),
    ("no_pay_after_revoke", "3. No payment commits after the mandate is revoked."),
    ("single_release", "5. Cancellation or expiry releases a reservation at most once and leaves it unspendable."),
]

ASSUMPTIONS = [
    "Exactly two transactions, one per agent (Agent A, Agent B), share one available budget.",
    "Amounts are the concrete integer HKD cents in the request.",
    "Each step is one atomic transition by one actor; any interleaving of actors is allowed.",
    "In the unsafe variant, reserve trusts the amount read by an earlier check (no re-check).",
    "In the atomic variant, check-and-reserve is a single transition.",
    "Payment commits a reservation once; retry of a paid transaction returns the same receipt.",
    "Revocation is one mandate-wide flag set by the user; no payment network, clock or database is modelled.",
    "Invariant 4 (payment matches reserved amount and bound quote) is not modelled.",
]


def hkd(minor: int) -> str:
    return f"HK${minor // 100}" if minor % 100 == 0 else f"HK${minor // 100}.{minor % 100:02d}"


class _Unrolled:
    """Symbolic state for steps 0..n and the transition constraints between them."""

    def __init__(self, variant: str, available: int, amounts: list[int], n: int):
        self.n, self.amounts, self.available = n, amounts, available
        ints = lambda name: [z3.Int(f"{name}_{k}") for k in range(n + 1)]  # noqa: E731
        self.phase = [ints(f"phase{i}") for i in range(2)]
        self.seen = [ints(f"seen{i}") for i in range(2)]  # amount read by the last check
        self.payments = [ints(f"payments{i}") for i in range(2)]
        self.releases = [ints(f"releases{i}") for i in range(2)]
        self.revoked = [z3.Bool(f"revoked_{k}") for k in range(n + 1)]
        self.paid_after_revoke = [z3.Bool(f"par_{k}") for k in range(n + 1)]
        self.action = [z3.Int(f"action_{k}") for k in range(n)]
        self.actor = [z3.Int(f"actor_{k}") for k in range(n)]
        allowed = ["idle", "pay", "retry", "cancel", "expire", "revoke"]
        allowed += ["check", "reserve"] if variant == "unsafe" else ["check_reserve"]

        c = [z3.Not(self.revoked[0]), z3.Not(self.paid_after_revoke[0])]
        for i in range(2):
            c += [self.phase[i][0] == IDLE, self.seen[i][0] == 0,
                  self.payments[i][0] == 0, self.releases[i][0] == 0]
        for k in range(n):
            c.append(z3.Or([self.action[k] == A[a] for a in allowed]))
            c.append(z3.And(self.actor[k] >= 0, self.actor[k] <= USER))
            c.append((self.action[k] == A["revoke"]) == (self.actor[k] == USER))
            c.append(z3.Implies(self.action[k] == A["idle"], self.actor[k] == 0))
            c.append(self._step(k))
        self.constraints = c

    def paid(self, k):
        return z3.Sum([z3.If(self.phase[i][k] == PAID, self.amounts[i], 0) for i in range(2)])

    def reserved(self, k):
        return z3.Sum([z3.If(self.phase[i][k] == RESERVED, self.amounts[i], 0) for i in range(2)])

    def _step(self, k):
        nxt = k + 1
        act, who = self.action[k], self.actor[k]
        free = self.available - self.paid(k) - self.reserved(k)
        per_tx = []
        for i in range(2):
            ph, ph2, amt = self.phase[i][k], self.phase[i][nxt], self.amounts[i]
            seen, seen2 = self.seen[i][k], self.seen[i][nxt]
            counts_same = z3.And(self.payments[i][nxt] == self.payments[i][k],
                                 self.releases[i][nxt] == self.releases[i][k])
            same = z3.And(ph2 == ph, seen2 == seen, counts_same)

            def move(guard, new_phase, pays=0, rel=0):
                return z3.And(guard, ph2 == new_phase, seen2 == seen,
                              self.payments[i][nxt] == self.payments[i][k] + pays,
                              self.releases[i][nxt] == self.releases[i][k] + rel)

            per_tx.append(z3.If(z3.And(who == i, act != A["idle"]), z3.Or(
                # unsafe: read now, reserve later on the possibly stale read
                z3.And(act == A["check"], ph == IDLE, ph2 == CHECKED, seen2 == free, counts_same),
                z3.And(act == A["reserve"], move(z3.And(ph == CHECKED, seen >= amt), RESERVED)),
                z3.And(act == A["check_reserve"], move(z3.And(ph == IDLE, free >= amt), RESERVED)),
                z3.And(act == A["pay"], move(z3.And(ph == RESERVED, z3.Not(self.revoked[k])), PAID, pays=1)),
                z3.And(act == A["retry"], ph == PAID, same),  # idempotent: same receipt, no new payment
                z3.And(act == A["cancel"], move(ph == RESERVED, RELEASED, rel=1)),
                z3.And(act == A["expire"], move(ph == RESERVED, RELEASED, rel=1)),
            ), same))
        payments_before = self.payments[0][k] + self.payments[1][k]
        payments_after = self.payments[0][nxt] + self.payments[1][nxt]
        return z3.And(
            *per_tx,
            self.revoked[nxt] == z3.Or(self.revoked[k], act == A["revoke"]),
            self.paid_after_revoke[nxt] == z3.Or(
                self.paid_after_revoke[k], z3.And(self.revoked[k], payments_after > payments_before)),
        )

    def holds(self, prop: str, k: int):
        if prop == "budget":
            return self.paid(k) + self.reserved(k) <= self.available
        if prop == "single_payment":
            return z3.And([self.payments[i][k] <= 1 for i in range(2)])
        if prop == "no_pay_after_revoke":
            return z3.Not(self.paid_after_revoke[k])
        if prop == "single_release":
            return z3.And([z3.And(self.releases[i][k] <= 1,
                                  z3.Implies(self.releases[i][k] > 0, self.payments[i][k] == 0))
                           for i in range(2)])
        raise ValueError(prop)

    def violated_somewhere(self, prop: str):
        return z3.Or([z3.Not(self.holds(prop, k)) for k in range(self.n + 1)])

    def timeline(self, m: z3.ModelRef, stop) -> list[dict]:
        """Readable steps up to the first state where ``stop(k)`` is true."""
        val = lambda e: m.eval(e, model_completion=True).as_long()  # noqa: E731
        steps = []
        for k in range(self.n):
            act, who = ACTIONS[val(self.action[k])], val(self.actor[k])
            if act == "idle":
                continue
            paid, reserved = val(self.paid(k + 1)), val(self.reserved(k + 1))
            steps.append({
                "step": len(steps) + 1,
                "actor": "User" if who == USER else AGENTS[who],
                "action": act,
                "paid_minor": paid,
                "reserved_minor": reserved,
                "remaining_minor": self.available - paid - reserved,
                "explanation": self._explain(act, who, k, val),
            })
            if z3.is_true(m.eval(stop(k + 1), model_completion=True)):
                break
        return steps

    def _explain(self, act, who, k, val) -> str:
        if act == "revoke":
            return "User revoked the mandate."
        name, amt = AGENTS[who], hkd(self.amounts[who])
        free_before = self.available - val(self.paid(k)) - val(self.reserved(k))
        return {
            "check": f"{name} checked {hkd(val(self.seen[who][k + 1]))} available.",
            "reserve": f"{name} reserved {amt}, trusting its earlier check of {hkd(val(self.seen[who][k]))}.",
            "check_reserve": f"{name} checked {hkd(free_before)} available and reserved {amt} in one step.",
            "pay": f"{name} spent {amt}.",
            "retry": f"{name} retried its payment and got the same receipt; nothing more was charged.",
            "cancel": f"{name} cancelled its {amt} reservation.",
            "expire": f"{name}'s {amt} reservation expired.",
        }[act]


# ponytail: z3py's default context is process-global and not thread-safe; two concurrent
# /verification/runs (the Safety Lab sends unsafe+atomic together) segfaulted the API.
# One lock serialises runs; per-call z3.Context() if parallel solving ever matters.
_Z3_LOCK = threading.Lock()


def run(variant: str, initial_available_minor: int, purchase_amounts_minor: list[int],
        max_steps: int = 8, timeout_ms: int = 3000) -> dict:
    with _Z3_LOCK:  # timeout starts once this run holds the solver
        return _run(variant, initial_available_minor, purchase_amounts_minor, max_steps, timeout_ms)


def _run(variant: str, initial_available_minor: int, purchase_amounts_minor: list[int],
         max_steps: int, timeout_ms: int) -> dict:
    started = time.monotonic()
    deadline = started + timeout_ms / 1000
    model = _Unrolled(variant, initial_available_minor, list(purchase_amounts_minor), max_steps)
    solver_result, counterexample, broken = "unsat", [], None

    def check(*extra):
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            return "timeout", None
        s = z3.Optimize()  # soft "idle" goals give the shortest readable counterexample
        s.set("timeout", left_ms)
        s.add(*model.constraints, *extra)
        for act in model.action:
            s.add_soft(act == A["idle"])
        r = s.check()
        if r == z3.sat:
            return "sat", s.model()
        if r == z3.unsat:
            return "unsat", None
        reason = s.reason_unknown()
        return ("timeout" if "timeout" in reason or "canceled" in reason else "unknown"), None

    for prop, _ in PROPERTIES:
        bad = model.violated_somewhere(prop)
        res, stop = "unsat", lambda k, p=prop: z3.Not(model.holds(p, k))
        if prop == "budget":
            # prefer a witness where the money is actually spent, not just reserved
            res, m = check(bad, model.paid(max_steps) > initial_available_minor)
            if res == "sat":
                stop = lambda k: model.paid(k) > initial_available_minor  # noqa: E731
        if res != "sat":
            res, m = check(bad)
        if res == "sat":
            solver_result, broken = "sat", prop
            counterexample = model.timeline(m, stop)
            break
        if res != "unsat":
            solver_result = res
            break

    status = {"sat": "counterexample_found", "unsat": "no_counterexample_within_bound"}.get(
        solver_result, "inconclusive")
    bound = f"{max_steps} steps, 2 transactions"
    if status == "counterexample_found":
        message = f"Counterexample found within {bound}. Violated property {dict(PROPERTIES)[broken]}"
    elif status == "no_counterexample_within_bound":
        message = (f"No counterexample found within the stated model and bound ({bound}). "
                   "This does not prove the deployed code, settings or payment networks correct.")
    else:
        message = f"Inconclusive: solver returned {solver_result} within {timeout_ms} ms; no claim is made."
    return {
        "id": f"ver_{uuid.uuid4().hex}",
        "variant": variant,
        "status": status,
        "solver_result": solver_result,
        "max_steps": max_steps,
        "transaction_count": 2,
        "runtime_ms": int((time.monotonic() - started) * 1000),
        "assumptions": ASSUMPTIONS,
        "checked_properties": [desc for _, desc in PROPERTIES],
        "counterexample": counterexample,
        "message": message,
    }
