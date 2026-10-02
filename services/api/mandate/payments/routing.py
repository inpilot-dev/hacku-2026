"""Payment route picker: which way to pay for a quote, ranked by net cost after rewards.

The rule, stated so a user can check any decision against it:

    net cost = basket total + route fee - reward value (HKD cents).
    Rank eligible routes by net cost, lowest first. Ties go to a route that
    holds funds at the rail, then to the route id. Fees always count. A reward
    counts only when its rate and its cash value were both observed; inferred
    or unpublished rewards are shown and counted as zero.

Route figures live in ``fixtures/payment_routes.json`` (or MANDATE_ROUTES_PATH),
each with a source URL and the time it was read. Pure functions only: the
ledger passes in how much the owner already spent on each route this month,
so a tiered reward ("4% on the first HK$10,000 a month") is priced correctly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("fixtures") / "payment_routes.json"

RULE = ("Net cost = basket total + route fee - reward value. Lowest net cost wins; ties go to a route that "
        "holds funds at the rail, then route id. Rewards count only when observed with a source.")


def _rate(amount_minor: int, rate_bp: int) -> int:
    """Basis points of an amount, rounded half up to the cent."""
    return (amount_minor * rate_bp + 5000) // 10000


def tiered_reward(amount_minor: int, spent_before_minor: int, tiers: list[dict]) -> int:
    """Reward on ``amount_minor`` when ``spent_before_minor`` already counted towards the tiers this period.

    Each tier covers spend from the previous tier's ceiling up to its own
    ``up_to_spend_minor`` (None means no ceiling).
    """
    reward, low = 0, 0
    start_all, end_all = spent_before_minor, spent_before_minor + amount_minor
    for tier in tiers:
        high = tier["up_to_spend_minor"]
        start, end = max(start_all, low), end_all if high is None else min(end_all, high)
        if end > start:
            reward += _rate(end - start, tier["rate_bp"])
        if high is None:
            break
        low = high
    return reward


@dataclass(frozen=True)
class RouteBook:
    routes: list[dict]
    sources: dict[str, dict]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "RouteBook":
        path = Path(path or os.environ.get("MANDATE_ROUTES_PATH") or DEFAULT_PATH)
        raw = json.loads(path.read_text())
        book = cls(routes=raw["routes"], sources=raw["sources"])
        for r in book.routes:
            for ref in (r["fee"]["source"], r["reward"]["source"], r.get("max_amount_source"),
                        r["reward"].get("value_source")):
                if ref is not None and ref not in book.sources:
                    raise ValueError(f"Route {r['id']} cites unknown source {ref}.")
        return book

    def get(self, route_id: str) -> dict | None:
        return next((r for r in self.routes if r["id"] == route_id), None)

    @property
    def rail_names(self) -> list[str]:
        return list(dict.fromkeys(r["rail"] for r in self.routes))

    def option(self, route: dict, amount_minor: int, spent_this_period_minor: int) -> dict:
        fee = route["fee"]
        fee_minor = _rate(amount_minor, fee["rate_bp"]) + fee["fixed_minor"]
        reward = route["reward"]
        counted = reward["status"] == "observed"
        units_minor = tiered_reward(amount_minor, spent_this_period_minor, reward["tiers"]) if reward["tiers"] else 0
        reward_minor = units_minor * reward.get("value_minor_per_unit", 100) // 100 if counted else 0
        limit = route["max_amount_minor"]
        eligible = limit is None or amount_minor <= limit
        evidence = [s for s in (fee["source"], reward["source"], route.get("max_amount_source"),
                                reward.get("value_source")) if s]
        caveats = []
        if fee["status"] != "observed":
            caveats.append(f"Fee is {fee['status']}: {fee['note']}")
        if reward["status"] != "observed":
            caveats.append(f"Reward not counted: {reward['note']}")
        if route.get("network_note"):
            caveats.append(route["network_note"])
        return {
            "route_id": route["id"],
            "label": route["label"],
            "provider": route["provider"],
            "network": route["network"],
            "rail": route["rail"],
            "holds_funds_at_rail": route["holds_funds_at_rail"],
            "settlement": route["settlement"],
            "eligible": eligible,
            "ineligible_reason": None if eligible else
            f"Over this route's HK${limit // 100:,} per-payment maximum.",
            "gross_minor": amount_minor,
            "fee_minor": fee_minor,
            "reward_minor": reward_minor,
            "net_minor": amount_minor + fee_minor - reward_minor,
            "reward_counted": counted,
            "rank": None,
            "evidence_ids": list(dict.fromkeys(evidence)),
            "caveats": caveats,
        }

    def rank(self, amount_minor: int, spent_by_route: dict[str, int]) -> list[dict]:
        """Every route with its economics; eligible ones carry rank 1..n by the stated rule."""
        options = [self.option(r, amount_minor, spent_by_route.get(r["id"], 0)) for r in self.routes]
        eligible = sorted((o for o in options if o["eligible"]),
                          key=lambda o: (o["net_minor"], not o["holds_funds_at_rail"], o["route_id"]))
        for i, o in enumerate(eligible, start=1):
            o["rank"] = i
        return eligible + [o for o in options if not o["eligible"]]

    def evidence(self, ids: list[str]) -> list[dict]:
        return [{"id": i, **self.sources[i]} for i in ids]
