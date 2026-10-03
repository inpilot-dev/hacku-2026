"""Structured answers from an OpenRouter chat model (JSON schema output)."""

from __future__ import annotations

import json
import os

import httpx

from ..config import env_value

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("MANDATE_PURCHASE_MODEL", "deepseek/deepseek-v4-flash")


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
        body = {"model": self.model, "temperature": 0,
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
                return json.loads(response.json()["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError, ValueError):
                continue  # one retry on an unreadable answer
        raise ModelError("The model returned an unreadable answer.")
