"""Payment route picker: net cost after observed rewards, a stated rule, and tiers that move the ranking."""

from __future__ import annotations

import json

from mandate.payments.routing import RouteBook, tiered_reward

from .conftest import AGENT, POLICY, USER, key

RED_TIERS = [{"rate_bp": 400, "up_to_spend_minor": 1000000}, {"rate_bp": 40, "up_to_spend_minor": None}]


def options(h, quote_id, headers=AGENT):
    res = h.client.get(f"/api/v1/quotes/{quote_id}/payment-options", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_tiered_reward_crosses_the_monthly_cap():
    assert tiered_reward(29700, 0, RED_TIERS) == 1188              # 4% of HK$297
    assert tiered_reward(29700, 990000, RED_TIERS) == 400 + 79     # HK$100 at 4%, HK$197 at 0.4%
    assert tiered_reward(29700, 1000000, RED_TIERS) == 119         # all at 0.4%


def test_every_figure_cites_a_timestamped_source():
    book = RouteBook.load()
    for source in book.sources.values():
        assert source["url"].startswith("https://") and source["observed_at"].endswith("+08:00") and source["quote"]
    for r in book.routes:
        assert r["fee"]["source"] in book.sources and r["reward"]["source"] in book.sources


def test_options_rank_by_net_cost_and_show_the_spread(h):
    h.confirm()
    q = h.quote()
    out = options(h, q["id"])
    assert out["rule"].startswith("Net cost = basket total + route fee - reward value")
    ranked = [(o["route_id"], o["net_minor"], o["reward_minor"], o["rank"]) for o in out["options"]]
    # HSBC Red: 4% RewardCash on HK$297 = HK$11.88, RC1 = HK$1 (both observed). The others publish no reward;
    # Tap & Go wins the tie with FPS because it holds funds at the rail.
    assert ranked == [("card_hsbc_red", 28512, 1188, 1), ("tng_single_use_card", 29700, 0, 2),
                      ("fps_edda", 29700, 0, 3)]
    assert out["recommended_route_id"] == "card_hsbc_red"
    assert {e["id"] for e in out["evidence"]} >= {"src_hsbc_red_rebate", "src_hsbc_rewardcash_value"}
    tng = next(o for o in out["options"] if o["route_id"] == "tng_single_use_card")
    assert any("Reward not counted" in c for c in tng["caveats"])


def test_authorization_uses_the_recommended_route_and_logs_the_ranking(h):
    m = h.confirm()
    auth = h.authorize(m["id"], h.quote()["id"]).json()
    route = auth["payment_route"]
    assert (route["route_id"], route["rank"], route["recommended_route_id"]) == ("card_hsbc_red", 1, "card_hsbc_red")
    assert auth["payment_credential"]["rail"] == "card_network_token"

    paid = h.pay(auth).json()
    assert paid["receipt"]["payment_route"] == {
        "route_id": "card_hsbc_red", "label": "HSBC Red credit card (scoped network token)", "network": None,
        "rail": "card_network_token", "fee_minor": 0, "reward_minor": 1188, "net_minor": 28512,
        "rank": None, "recommended_route_id": None, "rule": None, "caveats": []}
    with h.wallet.db.read() as conn:
        logged = json.loads(conn.execute("SELECT payload_json FROM wallet_stub_audit_events "
                                         "WHERE type = 'authorization_approved'").fetchone()[0])
    assert [o["route_id"] for o in logged["payment_options"]] == ["card_hsbc_red", "tng_single_use_card", "fps_edda"]


def test_agent_can_pick_another_eligible_route(h):
    m = h.confirm()
    q = h.quote()
    res = h.client.post("/api/v1/authorizations", headers={**AGENT, **key()},
                        json={"transaction_id": "t-tng", "mandate_id": m["id"], "quote_id": q["id"],
                              "payment_route_id": "tng_single_use_card"})
    auth = res.json()
    assert auth["payment_route"]["route_id"] == "tng_single_use_card"
    assert auth["payment_route"]["recommended_route_id"] == "card_hsbc_red"
    assert auth["payment_credential"]["network"] == "mastercard" and len(auth["payment_credential"]["last4"]) == 4
    assert h.pay(auth).json()["receipt"]["payment_route"]["reward_minor"] == 0


def test_unknown_route_is_rejected_without_reserving(h):
    m = h.confirm()
    q = h.quote()
    res = h.client.post("/api/v1/authorizations", headers={**AGENT, **key()},
                        json={"transaction_id": "t-x", "mandate_id": m["id"], "quote_id": q["id"],
                              "payment_route_id": "crypto"})
    assert res.status_code == 422
    assert h.budget(m["id"])[0]["reserved_minor"] == 0


def test_tap_and_go_drops_out_above_its_hk2000_card_maximum(h):
    h.confirm({**POLICY, "per_order_limit_minor": 500000,
               "period_limits": [{"period": "calendar_week", "limit_minor": 900000, "timezone": "Asia/Hong_Kong"}]})
    big = h.quote({"merchant_id": "demo_store_a", "items": [{"product_id": "p_a_rice", "quantity": 25}],
                   "delivery_context_id": "ctx_a_standard"})
    assert big["total_minor"] > 200000
    tng = next(o for o in options(h, big["id"])["options"] if o["route_id"] == "tng_single_use_card")
    assert (tng["eligible"], tng["rank"]) == (False, None)
    assert "HK$2,000" in tng["ineligible_reason"]


def test_spend_this_month_moves_the_reward_tier(h):
    h.confirm()
    q = h.quote()
    with h.wallet.db.write_tx() as conn:  # HK$9,900 already spent on the card this month
        conn.execute("INSERT INTO reward_ledger (owner_id, route_id, transaction_id, kind, spend_minor, "
                     "reward_minor, period_start, created_at) VALUES ('user_demo','card_hsbc_red','old','earn',"
                     "990000,39600,'2026-10-01T00:00:00+08:00','2026-10-02T10:00:00+08:00')")
    red = next(o for o in options(h, q["id"], USER)["options"] if o["route_id"] == "card_hsbc_red")
    assert red["reward_minor"] == 479
