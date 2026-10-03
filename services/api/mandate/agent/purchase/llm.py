"""Structured answers from an OpenRouter chat model (JSON schema output)."""

from __future__ import annotations

import json
import os

import httpx

from ..config import env_value

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Mercury answered page questions like DeepSeek V4 Flash, about 4x faster (6 s vs 27 s on a product page).
MODEL = os.environ.get("MANDATE_PURCHASE_MODEL", "inception/mercury-2.5")
SPEC_MODEL = os.environ.get("MANDATE_PURCHASE_SPEC_MODEL", "deepseek/deepseek-v4-flash")


class ModelError(Exception):
    pass


class JsonModel:
    def __init__(self, model: str = MODEL, client: httpx.Client | None = None):
        self.model = model
        self.client = client or httpx.Client(timeout=90)

    def ask(self, name: str, schema: dict, system: str, user: str) -> dict:
        key = env_value("OPENROUTER_KEY") or env_value("OPENROUTER_API_KEY")
        if not key:
            raise ModelError("OPENROUTER_KEY is not set.")
        body = {"model": self.model, "temperature": 0, "reasoning": {"enabled": False},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": name, "strict": True, "schema": schema}}}
        for _attempt in range(2):
            try:
                response = self.client.post(OPENROUTER_URL, headers={"Authorization": f"Bearer {key}"}, json=body)
            except httpx.HTTPError:
                raise ModelError("Could not reach the model provider.") from None
            if response.is_error:
                raise ModelError(f"Model provider returned HTTP {response.status_code}.")
            try:
                answer = json.loads(response.json()["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError, ValueError):
                continue  # one retry on an unreadable answer
            if conforms(answer, schema):
                return answer  # fast models do not always honour the schema: check it here
        raise ModelError("The model returned an unreadable answer.")


TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def conforms(value, schema: dict) -> bool:
    """Checks the JSON-schema subset these prompts use: type, enum, required, properties, items, min/maxItems."""
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        ok = any((k == "integer" and isinstance(value, int) and not isinstance(value, bool))
                 or (k == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
                 or (k in TYPES and isinstance(value, TYPES[k])) for k in kinds)
        if not ok:
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            return False
        return all(conforms(value[k], sub) for k, sub in schema.get("properties", {}).items() if k in value)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            return False
        return all(conforms(item, schema["items"]) for item in value) if "items" in schema else True
    return True
