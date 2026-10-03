"""Choose catalog products for shopping-list items with Jev (TypeSafe System One).

Jev answers choice questions: for each list item it picks one catalog product ID
or NONE, with a probability for every option. It cannot write prices, totals or
free text, so listing text cannot make it do anything except pick another
listed product. The wallet prices whatever is picked.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from .config import env_value

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
NONE = "NONE"
MIN_PROBABILITY = 0.5

RULES = ("Pick the catalog product a shopper would accept for this shopping-list item. Among products that "
         "match equally well, prefer the lower price. Pick NONE if no product is a reasonable match. "
         "Follow the shopper's preference when it is given.")


class SelectorError(Exception):
    pass


@dataclass(frozen=True)
class Pick:
    item: str
    quantity: int
    product_id: str | None  # None when nothing matched confidently
    probability: float
    reason: str  # why it was left out, when product_id is None


@dataclass(frozen=True)
class Selection:
    picks: list[Pick]
    model_id: str


def typesafe_key() -> str:
    key = env_value("TYPESAFE_API_KEY")
    if not key:
        raise SelectorError("TYPESAFE_API_KEY is not set, so Jev cannot choose products.")
    return key


def build_questions(items: list[dict], products: list[dict], instruction: str | None) -> dict:
    criteria = {p["id"]: {"title": p["title"], "price_hkd": f"{p['unit_price_minor'] / 100:.2f}",
                          "category": p["category"]} for p in products}
    criteria[NONE] = "No listed product is a reasonable match for this shopping item."
    questions = {}
    for index, item in enumerate(items):
        instructions = {"shopping_item": item["name"], "rules": RULES}
        if item.get("unit"):
            instructions["unit"] = item["unit"]
        if instruction:
            instructions["shopper_preference"] = instruction
        questions[f"item_{index}"] = {"type": "choice", "criteria": criteria, "instructions": instructions}
    return questions


def read_answers(answers: dict, items: list[dict], product_ids: set[str]) -> list[Pick]:
    picks = []
    for index, item in enumerate(items):
        answer = answers.get(f"item_{index}") or {}
        choice = answer.get("choice")
        probabilities = answer.get("probabilities") or {}
        probability = probabilities.get(choice)
        if choice not in product_ids | {NONE} or not isinstance(probability, (int, float)):
            raise SelectorError(f"Jev returned an invalid answer for '{item['name']}'; no basket was built.")
        if choice == NONE:
            picks.append(Pick(item["name"], item["quantity"], None, float(probability), "no matching product"))
        elif probability < MIN_PROBABILITY:
            picks.append(Pick(item["name"], item["quantity"], None, float(probability),
                              f"no confident match (best {probability:.0%})"))
        else:
            picks.append(Pick(item["name"], item["quantity"], choice, float(probability), ""))
    return picks


class JevSelector:
    def __init__(self, model: str | None = None, client: httpx.Client | None = None):
        self.model = model or os.environ.get("TYPESAFE_MODEL", "jev-latest")
        self.client = client or httpx.Client(timeout=45)

    def choose(self, items: list[dict], products: list[dict], instruction: str | None) -> Selection:
        if not products:
            raise SelectorError("No candidate products to choose from.")
        body = {
            "model": self.model,
            "state": {"task": "Build a grocery basket from a shopping list using only the listed catalog products."},
            "questions": build_questions(items, products, instruction),
        }
        response = None
        for attempt in range(3):
            try:
                response = self.client.post(TYPESAFE_URL, json=body,
                                            headers={"Authorization": f"Bearer {typesafe_key()}"})
            except httpx.HTTPError:
                raise SelectorError("Could not reach Jev; no basket was built.") from None
            if response.status_code in (429, 503, 529) and attempt < 2:
                time.sleep(0.5 * 2 ** attempt)
                continue
            break
        if response.is_error:
            raise SelectorError(f"Jev returned HTTP {response.status_code}; no basket was built.")
        data = response.json()
        picks = read_answers(data.get("answers") or {}, items, {p["id"] for p in products})
        return Selection(picks, str(data.get("model") or self.model))
