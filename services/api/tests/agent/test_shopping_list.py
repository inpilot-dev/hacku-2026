from __future__ import annotations

import base64
import json

import httpx
import pytest

from mandate.agent import shopping_list
from mandate.agent.shopping_list import ShoppingListError, ShoppingListService, parse_with_rules, split_quantity


@pytest.mark.parametrize("text,expected", [
    ("3 apples", ("apples", 3)), ("apples x3", ("apples", 3)), ("2× kiwis", ("kiwis", 2)),
    ("two kiwis", ("kiwis", 2)), ("jasmine rice 5kg", ("jasmine rice 5kg", 1)), ("milk", ("milk", 1)),
    ("a dozen eggs", ("eggs", 12)),
])
def test_split_quantity(text, expected):
    assert split_quantity(text) == expected


def test_rules_split_a_typed_list():
    assert parse_with_rules("I need rice, 2 milk and sugar\nthree apples, rice") == [
        {"name": "rice", "quantity": 2, "unit": None}, {"name": "milk", "quantity": 2, "unit": None},
        {"name": "sugar", "quantity": 1, "unit": None}, {"name": "apples", "quantity": 3, "unit": None}]


def service(handler):
    return ShoppingListService(httpx.Client(transport=httpx.MockTransport(handler)))


def reply(content, model="m"):
    return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": content}}]})


def test_parse_uses_the_model_and_cleans_its_output(monkeypatch):
    monkeypatch.setenv("OPENROUTER_KEY", "k")
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply(json.dumps({"items": [{"name": " Milk ", "quantity": 2, "unit": "L"},
                                           {"name": "milk", "quantity": 1, "unit": None},
                                           {"name": "", "quantity": 1, "unit": None},
                                           {"name": "rice", "quantity": 500, "unit": " "}]}), "haiku")

    parsed = service(handler).parse("two litres of milk, more milk, rice")
    assert parsed.source == "model" and parsed.model_id == "haiku"
    assert parsed.items == [{"name": "Milk", "quantity": 3, "unit": "L"}, {"name": "rice", "quantity": 99, "unit": None}]
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["messages"][1]["content"] == "two litres of milk, more milk, rice"


def test_parse_falls_back_to_rules_when_the_model_fails(monkeypatch):
    monkeypatch.setenv("OPENROUTER_KEY", "k")
    parsed = service(lambda request: httpx.Response(500)).parse("rice, 2 milk")
    assert parsed.source == "rules" and parsed.model_id is None
    assert "fixed rules" in parsed.note
    assert parsed.items == [{"name": "rice", "quantity": 1, "unit": None}, {"name": "milk", "quantity": 2, "unit": None}]
    garbled = service(lambda request: reply("not json")).parse("rice")
    assert garbled.source == "rules"


class FakeWhisper:
    def __init__(self, text=" I need rice. ", error=None):
        self.text, self.error, self.calls = text, error, []

    def transcribe(self, audio, **options):
        if self.error:
            raise self.error
        self.calls.append((audio.read(), options))
        return [type("Segment", (), {"text": self.text})()], None


def test_transcribe_runs_locally_and_validates_input(monkeypatch):
    fake = FakeWhisper()
    monkeypatch.setattr(shopping_list, "whisper_model", lambda: fake)
    audio = base64.b64encode(b"OggS....").decode()
    text, model_id = ShoppingListService().transcribe(audio, "OGG")
    assert text == "I need rice." and model_id.startswith("faster-whisper/")
    assert fake.calls[0][0] == b"OggS...." and fake.calls[0][1]["language"] == "en"
    with pytest.raises(ShoppingListError) as bad_format:
        ShoppingListService().transcribe(audio, "exe")
    assert bad_format.value.status == 422
    with pytest.raises(ShoppingListError):
        ShoppingListService().transcribe("@@not-base64@@", "ogg")


def test_undecodable_audio_is_a_422(monkeypatch):
    monkeypatch.setattr(shopping_list, "whisper_model", lambda: FakeWhisper(error=RuntimeError("bad stream")))
    with pytest.raises(ShoppingListError) as failure:
        ShoppingListService().transcribe(base64.b64encode(b"x").decode(), "ogg")
    assert failure.value.status == 422


def test_missing_whisper_install_is_a_503(monkeypatch):
    def missing():
        raise ShoppingListError("Local transcription is not installed.", status=503)
    monkeypatch.setattr(shopping_list, "whisper_model", missing)
    with pytest.raises(ShoppingListError) as failure:
        ShoppingListService().transcribe(base64.b64encode(b"x").decode(), "ogg")
    assert failure.value.status == 503
