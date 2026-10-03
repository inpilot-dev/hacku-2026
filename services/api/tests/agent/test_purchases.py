"""One-time purchases: page checks, best-deal ranking, guest checkout outcomes and the approval gate.

No browser, model or network: the tab, model, search and checkout steps are fakes.
"""

from __future__ import annotations

import json
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mandate.agent.purchase import runs as runs_module
from mandate.agent.purchase.assess import amount_minor, assess, numbers_in, quoted
from mandate.agent.purchase.browser import CARD_FIELD, FINAL_ACTION
from mandate.agent.purchase.checkout import DIRECT, OrderSummary, _cart_guesses
from mandate.agent.purchase.profile import ProfileStore, form_values, missing_fields
from mandate.agent.purchase.routes import build_purchase_router
from mandate.agent.purchase.runs import PurchaseRuns
from mandate.agent.purchase.spec import PurchaseSpec, Requirement
from mandate.payments.errors import ApiError, install_error_handlers
from mandate.payments.auth import Actor

USER = {"Authorization": "Bearer dev-user-token"}
ME = Actor("user_demo", "user")
PROFILE = {"full_name": "Chan Tai Man", "email": "tm@example.com", "phone": "91234567",
           "address_line1": "1 Test Road", "address_line2": "Flat A, 10/F", "district": "Wan Chai",
           "city": "Hong Kong", "country": "Hong Kong"}
PAN = "4242424242424242"


def spec(**kw) -> PurchaseSpec:
    base = dict(request="phone with 8GB RAM under HK$3000", item="phone", search_query="8GB RAM smartphone",
                quantity=1, max_price_minor=300000,
                requirements=(Requirement("RAM", "min", 8, "GB", None),))
    return PurchaseSpec(**{**base, **kw})


# ------------------------------------------------------------------ page checks

def test_amounts_and_quotes():
    assert amount_minor("HK$2,998.50") == 299850
    assert amount_minor("From HK$ 2538") == 253800
    assert amount_minor("HK$100 - HK$200") is None  # a range is not one price
    assert quoted("HK$ 2,538", "Price\nHK$  2,538\nAdd") and not quoted("HK$2,000", "HK$2,538")
    assert 2.0 in numbers_in("Dual USB-C") and 2.0 in numbers_in("雙 USB-C") and 8.0 in numbers_in("8GB + 256GB")


class Model:
    """Answers by question name; records what it was asked."""

    def __init__(self, **answers):
        self.answers, self.asked = answers, []

    def ask(self, name, schema, system, user):
        self.asked.append((name, user))
        answer = self.answers[name]
        return answer(user) if callable(answer) else answer


def product_answer(price="HK$2,499", ram_quote="8GB RAM", ram=8, **kw):
    return {"kind": "product", "title": "Phone X", "price_quote": price, "currency": "HKD", "in_stock": True,
            "requirements": [{"quote": ram_quote, "number": ram, "meets": True}], "product_links": [], **kw}


PAGE = {"url": "https://shop.example.com/p/1", "title": "Phone X", "text": "Phone X\nHK$2,499\n8GB RAM\n6.7\" FHD+",
        "ld": [], "links": []}


def test_assess_match_and_rules():
    assert assess(PAGE, spec(), Model(page_assessment=product_answer())).matches
    over = assess(PAGE, spec(max_price_minor=200000), Model(page_assessment=product_answer()))
    assert not over.matches and "over your" in over.problems[0]
    low = assess(PAGE, spec(requirements=(Requirement("RAM", "min", 12, "GB", None),)),
                 Model(page_assessment=product_answer()))
    assert low.checks[0].ok is False and not low.matches


def test_assess_does_not_trust_the_model():
    # A price or spec the page does not show is never taken from the model.
    invented = assess(PAGE, spec(), Model(page_assessment=product_answer(price="HK$999", ram_quote="12GB RAM", ram=12)))
    assert "no price shown on the page" in invented.problems
    assert invented.checks[0].ok is None  # unverified, not a pass
    wrong_number = assess(PAGE, spec(), Model(page_assessment=product_answer(ram=16)))
    assert wrong_number.checks[0].ok is False  # 16 is not in the quote "8GB RAM"
    silent = assess(PAGE, spec(), Model(page_assessment=product_answer(ram_quote=None, ram=None)))
    assert silent.matches and silent.unverified == ["RAM at least 8 GB"]


def test_listing_links_are_only_the_pages_own():
    page = {**PAGE, "links": [{"href": "https://shop.example.com/p/2", "text": "Phone Y"},
                              {"href": "https://other.example.com/p/3", "text": "Phone Z"}]}
    answer = {"kind": "listing", "title": None, "price_quote": None, "currency": None, "in_stock": None,
              "requirements": [{"quote": None, "number": None, "meets": False}], "product_links": [0, 1, 7]}
    result = assess(page, spec(), Model(page_assessment=answer))
    assert result.product_links == ["https://shop.example.com/p/2"]  # other sites and invented indexes dropped


# ----------------------------------------------------------------- browser rules

@pytest.mark.parametrize("label", ["Place order", "Pay now", "Pay HK$249.00", "Complete order", "立即付款", "提交訂單"])
def test_final_actions_are_stopped(label):
    assert FINAL_ACTION.search(label)


@pytest.mark.parametrize("label", ["Continue to payment", "繼續付款", "Continue to shipping", "Add to cart", "結帳"])
def test_steps_before_paying_are_allowed(label):
    assert not FINAL_ACTION.search(label)


def test_card_fields_and_direct_buttons():
    assert CARD_FIELD.search("Card number") and CARD_FIELD.search("安全碼") and not CARD_FIELD.search("Address")
    assert DIRECT["add_to_cart"].search("加入購物車") and not DIRECT["add_to_cart"].search("加入會員")
    assert DIRECT["checkout"].search("Check out") and DIRECT["checkout"].search("結帳")
    assert _cart_guesses("https://shop.example.com/zh-hk/product/1") == [
        "https://shop.example.com/zh-hk/cart", "https://shop.example.com/cart"]


def test_payable_needs_a_card_form():
    cod = OrderSummary("payment", "u", total_minor=24900, currency="HKD", card_fields=[])
    assert not cod.payable  # e.g. a review page with cash on delivery chosen
    assert OrderSummary("payment", "u", total_minor=24900, currency="HKD", card_fields=["number"]).payable


# ---------------------------------------------------------------------- profile

def test_profile_storage_and_form_values(tmp_path):
    store = ProfileStore(tmp_path)
    saved = store.put("user_demo", PROFILE)
    assert saved["region"] == "Hong Kong Island"  # from the district
    assert stat.S_IMODE((tmp_path / "user_demo.json").stat().st_mode) == 0o600
    assert missing_fields(saved) == [] and missing_fields({"email": "x"})
    values = form_values(saved)
    assert values["first_name"] == "Chan Tai" and values["last_name"] == "Man"
    assert values["full_address"] == "Flat A, 10/F, 1 Test Road, Wan Chai, Hong Kong"


# ------------------------------------------------------------------------- runs

class Now:
    def submit(self, fn, *args):
        fn(*args)


class Tab:
    instances: list["Tab"] = []

    def __init__(self):
        self.opened, self.closed, self.clicked, self.filled = [], False, [], None
        Tab.instances.append(self)

    def open(self, url):
        self.opened.append(url)
        return {**PAGE, "url": url}

    def snapshot(self):
        return {**PAGE, "url": self.opened[-1], "text": "Thank you! Order #A123"}

    def wait_loaded(self):
        pass

    def close(self):
        self.closed = True

    def fill_card(self, card):
        self.filled = card["pan"]
        return ["number", "exp", "cvc"]

    def final_buttons(self):
        return [{"index": 3, "text": "Pay now"}]

    def click_button(self, button):
        self.clicked.append(button["text"])
        return True


def offer(url, price, ram="8GB RAM"):
    return {"url": url, "title": f"Phone at {url}", "text": f"Phone\n{price}\n{ram}"}


@pytest.fixture
def make_runs(tmp_path, monkeypatch):
    Tab.instances = []
    monkeypatch.setattr(runs_module.time, "sleep", lambda _s: None)

    def build(pages, checkout, preference=None):
        """pages: url -> page text; checkout: url -> summary stage ('payment' | 'sign_in_required' | ...)."""
        profiles = ProfileStore(tmp_path)
        profiles.put("user_demo", PROFILE)

        def page_answer(user):
            page = json.loads(user)["page"]
            text = page["text"]
            price = text.split("\n")[1]
            return product_answer(price=price, ram_quote=text.split("\n")[2], title=f"Phone at {page['url']}")

        def summary(user):
            url = state["at"]
            return {"stage": checkout[url], "total_quote": "HK$2,499", "shipping_quote": "Free", "currency": "HKD",
                    "product_in_cart": True}

        state = {"at": None}
        model = Model(purchase_spec={"item": "phone", "search_query": "8GB RAM smartphone", "quantity": 1,
                                     "max_price_hkd": 3000, "preference": preference, "requirements": [
                                         {"name": "RAM", "kind": "min", "number": 8, "unit": "GB", "text": None}]},
                      page_assessment=page_answer, order_summary=summary,
                      order_result={"outcome": "confirmed", "order_number_quote": "Order #A123"},
                      rank={"order": [1, 0]})

        class PageTab(Tab):
            def open(self, url):
                state["at"] = url
                self.opened.append(url)
                return {"url": url, "title": "Phone", "text": pages[url], "ld": [], "links": []}

            def snapshot(self):
                return {"url": state["at"], "title": "Checkout", "text": "Total HK$2,499 Free Thank you! Order #A123",
                        "ld": [], "links": []}

            def card_fields(self):
                return {"s": ["number", "exp", "cvc"]} if checkout[state["at"]] == "payment" else {}

        monkeypatch.setattr(runs_module, "go_to_payment", lambda *a, **k: ("details", "card_field"))
        runs = PurchaseRuns(profiles, model=model, search=lambda q: [{"url": u, "title": u} for u in pages],
                            tab_factory=PageTab, executor=Now())
        return runs, model

    return build


PAGES = {"https://a.example.com/p": "Phone\nHK$2,899\n8GB RAM", "https://b.example.com/p": "Phone\nHK$2,499\n8GB RAM",
         "https://c.example.com/p": "Phone\nHK$1,999\n4GB RAM"}


def test_best_deal_is_checked_out_and_waits_for_approval(make_runs):
    runs, _ = make_runs(PAGES, {u: "payment" for u in PAGES})
    run = runs.start(ME, "phone with 8GB RAM under HK$3000")
    assert run["status"] == "awaiting_approval"
    assert run["choice"]["url"] == "https://b.example.com/p"  # cheapest that meets 8GB; the 4GB one is out
    assert run["order"]["total_minor"] == 249900 and "Approve to pay" in run["message"]
    assert not Tab.instances[-1].closed  # the checkout stays open for approval


def test_stated_preference_overrides_the_cheapest(make_runs):
    runs, model = make_runs(PAGES, {u: "payment" for u in PAGES}, preference="from shop a")
    run = runs.start(ME, "phone from shop a")
    assert run["choice"]["url"] == "https://a.example.com/p"
    assert any(name == "rank" for name, _ in model.asked)


def test_account_only_shops_are_offered_as_links(make_runs):
    runs, _ = make_runs(PAGES, {u: "sign_in_required" for u in PAGES})
    run = runs.start(ME, "phone")
    assert run["status"] == "needs_account"
    assert "https://b.example.com/p" in run["message"] and "needs an account" in run["message"]
    assert [o["checkout"] for o in run["options"]] == ["account_required", "account_required"]
    assert Tab.instances[-1].closed


def test_guest_shop_is_bought_when_the_best_deal_needs_an_account(make_runs):
    runs, _ = make_runs(PAGES, {"https://a.example.com/p": "payment", "https://b.example.com/p": "sign_in_required"})
    run = runs.start(ME, "phone")
    assert run["status"] == "awaiting_approval" and run["choice"]["url"] == "https://a.example.com/p"
    assert "https://b.example.com/p" in run["message"]  # the cheaper account-only option is still mentioned


def test_purchase_needs_delivery_details(tmp_path):
    runs = PurchaseRuns(ProfileStore(tmp_path), model=Model(), search=lambda q: [], tab_factory=Tab, executor=Now())
    with pytest.raises(ApiError) as err:
        runs.start(ME, "a phone")
    assert err.value.details["reason"] == "PROFILE_INCOMPLETE"


def test_approval_gate(make_runs, monkeypatch):
    runs, _ = make_runs(PAGES, {u: "payment" for u in PAGES})
    run = runs.start(ME, "phone")
    with pytest.raises(ApiError) as err:
        runs.approve(ME, run["id"], 100)  # not the checkout total
    assert err.value.status == 422
    with pytest.raises(ApiError):
        runs.approve(Actor("someone_else", "user"), run["id"], 249900)

    monkeypatch.delenv("MANDATE_LIVE_PAYMENTS", raising=False)
    done = runs.approve(ME, run["id"], 249900)
    tab = Tab.instances[-1]
    assert done["status"] == "stopped_before_payment" and tab.filled == PAN and tab.clicked == []
    assert tab.closed and PAN not in json.dumps(done)
    with pytest.raises(ApiError) as again:
        runs.approve(ME, run["id"], 249900)  # one-shot
    assert again.value.status == 409


def test_live_payment_clicks_the_one_final_button(make_runs, monkeypatch):
    monkeypatch.setenv("MANDATE_LIVE_PAYMENTS", "1")
    runs, _ = make_runs(PAGES, {u: "payment" for u in PAGES})
    run = runs.start(ME, "phone")
    done = runs.approve(ME, run["id"], 249900)
    assert done["status"] == "ordered" and "Order #A123" in done["message"]
    assert Tab.instances[-1].clicked == ["Pay now"] and PAN not in json.dumps(done)


def test_cancel_closes_the_checkout(make_runs):
    runs, _ = make_runs(PAGES, {u: "payment" for u in PAGES})
    run = runs.start(ME, "phone")
    assert runs.cancel(ME, run["id"])["status"] == "cancelled" and Tab.instances[-1].closed


def test_routes(make_runs, tmp_path):
    runs, _ = make_runs(PAGES, {u: "payment" for u in PAGES})
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(build_purchase_router(runs, runs.profiles), prefix="/api/v1")
    client = TestClient(app)
    assert client.put("/api/v1/profile", headers=USER, json={**PROFILE, "region": None}).json()["missing"] == []
    started = client.post("/api/v1/purchases", headers=USER, json={"text": "phone"})
    assert started.status_code == 202
    got = client.get(f"/api/v1/purchases/{started.json()['id']}", headers=USER).json()
    assert got["status"] == "awaiting_approval"
    assert client.post(f"/api/v1/purchases/{got['id']}/approve", headers=USER,
                       json={"total_minor": 1}).status_code == 422
    assert client.get(f"/api/v1/purchases/{got['id']}", headers={"Authorization": "Bearer dev-agent-token"}
                      ).status_code == 403


def test_responses_follow_the_contract(make_runs):
    from pathlib import Path

    schemas = json.loads((Path(__file__).resolve().parents[4] / "contracts" / "openapi.json").read_text())[
        "components"]["schemas"]
    runs, _ = make_runs(PAGES, {u: "payment" for u in PAGES})
    run = runs.start(ME, "phone")
    assert set(run) == set(schemas["Purchase"]["required"])
    assert set(run["choice"]) == set(schemas["PurchaseCandidate"]["required"])
    assert set(run["order"]) == set(schemas["PurchaseOrder"]["required"])
    assert set(run["options"][0]) == set(schemas["PurchaseOption"]["required"])
    assert set(run["spec"]) == set(schemas["PurchaseSpec"]["required"])
    assert run["status"] in schemas["Purchase"]["properties"]["status"]["enum"]
