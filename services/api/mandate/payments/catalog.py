"""Trusted catalog adapter behind POST /quotes.

Prices, categories and delivery fees come only from here, never from the
agent. The data itself is Abdullah's (data/catalog/); this module reads any
file in the shape below. ``fixtures/placeholder_catalog.json`` is a
development placeholder whose evidence is explicitly marked as not observed.
It must be replaced with captured observations before the compliance demo.

File shape::

    {"merchants": {"<merchant_id>": {"revision": str, "data_mode": "observed_snapshot",
                                     "evidence_ids": [str]}},
     "products": [<contract Product>],
     "delivery_contexts": [{"id": str, "merchant_id": str,
                            "fee_rules": [{"label": str, "min_subtotal_minor": int,
                                           "max_subtotal_minor": int | null,
                                           "amount_minor": int, "evidence_ids": [str]}]}],
     "evidence": [<contract Evidence>]}
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULT_CATALOG = Path(__file__).with_name("fixtures") / "placeholder_catalog.json"


class CatalogError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # not_found | invalid | unavailable | stale


class Catalog:
    def __init__(self, data: dict):
        self._data = data
        self._products = {p["id"]: p for p in data["products"]}
        self._contexts = {c["id"]: c for c in data["delivery_contexts"]}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Catalog":
        path = path or os.environ.get("MANDATE_CATALOG_PATH") or DEFAULT_CATALOG
        return cls(json.loads(Path(path).read_text()))

    def listing(self, merchant_id: str | None = None) -> dict:
        """Products plus every evidence record they, their merchant and its fee rules cite.

        Read-only view for GET /catalog (contract CatalogResponse). Raises
        CatalogError("not_found") for an unknown merchant.
        """
        merchants = [merchant_id] if merchant_id is not None else list(self._data["merchants"])
        cited: list[str] = []
        for mid in merchants:
            cited += self.merchant(mid).get("evidence_ids", [])
        products = [copy.deepcopy(p) for p in self._data["products"] if p["merchant_id"] in merchants]
        for product in products:
            cited += product["evidence_ids"]
        for context in self._data["delivery_contexts"]:
            if context["merchant_id"] in merchants:
                for rule in context["fee_rules"]:
                    cited += rule["evidence_ids"]
        wanted = set(cited)
        evidence = [copy.deepcopy(e) for e in self._data["evidence"] if e["id"] in wanted]
        return {"products": products, "evidence": evidence}

    def merchant(self, merchant_id: str) -> dict:
        merchant = self._data["merchants"].get(merchant_id)
        if merchant is None:
            raise CatalogError("not_found", f"Unknown merchant {merchant_id}.")
        return merchant

    def price(self, merchant_id: str, items: list[dict], delivery_context_id: str) -> dict:
        """Server-side pricing of a basket. Returns the priced, hash-free quote fields."""
        merchant = self.merchant(merchant_id)
        context = self._contexts.get(delivery_context_id)
        if context is None or context["merchant_id"] != merchant_id:
            raise CatalogError("not_found", f"Unknown delivery context {delivery_context_id} for {merchant_id}.")

        seen: set[str] = set()
        lines = []
        for item in items:
            product = self._products.get(item["product_id"])
            if product is None or product["merchant_id"] != merchant_id:
                raise CatalogError("not_found", f"Product {item['product_id']} is not sold by {merchant_id}.")
            if product["id"] in seen:
                raise CatalogError("invalid", f"Product {product['id']} appears twice; combine quantities.")
            seen.add(product["id"])
            if not product["available"]:
                raise CatalogError("unavailable", f"Product {product['id']} is currently unavailable.")
            lines.append({
                "product_id": product["id"],
                "title": product["title"],
                "quantity": item["quantity"],
                "unit_price_minor": product["unit_price_minor"],
                "line_total_minor": product["unit_price_minor"] * item["quantity"],
                "category": product["category"],
                "category_status": product["category_status"],
                "evidence_ids": list(product["evidence_ids"]),
            })
        subtotal = sum(line["line_total_minor"] for line in lines)

        charges = []
        for rule in context["fee_rules"]:
            upper = rule.get("max_subtotal_minor")
            if subtotal >= rule["min_subtotal_minor"] and (upper is None or subtotal <= upper):
                charges.append({
                    "kind": rule.get("kind", "delivery"),
                    "label": rule["label"],
                    "amount_minor": rule["amount_minor"],
                    "evidence_ids": list(rule["evidence_ids"]),
                })
        total = subtotal + sum(c["amount_minor"] for c in charges)

        evidence = list(merchant.get("evidence_ids", []))
        for part in lines + charges:
            for ev in part["evidence_ids"]:
                if ev not in evidence:
                    evidence.append(ev)

        return {
            "merchant_id": merchant_id,
            "revision": merchant["revision"],
            "currency": "HKD",
            "items": lines,
            "subtotal_minor": subtotal,
            "charges": charges,
            "total_minor": total,
            "delivery_context_id": delivery_context_id,
            "data_mode": merchant.get("data_mode", "observed_snapshot"),
            "evidence_ids": evidence,
        }

    # --- scenario hooks (local demo mode only; never exposed to the agent) ---

    def with_changes(self, *, merchant_revision: dict | None = None, fee_rules: dict | None = None) -> "Catalog":
        data = copy.deepcopy(self._data)
        for merchant_id, revision in (merchant_revision or {}).items():
            data["merchants"][merchant_id]["revision"] = revision
        for context_id, rules in (fee_rules or {}).items():
            for ctx in data["delivery_contexts"]:
                if ctx["id"] == context_id:
                    ctx["fee_rules"] = rules
        return Catalog(data)
