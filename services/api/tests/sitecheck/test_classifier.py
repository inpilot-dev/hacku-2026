import json

import httpx
import pytest

from mandate.sitecheck import JevSiteClassifier, Page, SiteCheckError
from mandate.sitecheck.classifier import MAX_TEXT, build_question

FAKE = Page("https://we1come-hk.shop/login", "Wellcome sign in", "Enter your card number to claim HK$500")
REAL = Page("https://www.wellcome.com.hk/en", "Wellcome", "Fresh fruit and vegetables")


def classifier(answer: dict | None = None, status: int = 200, seen: list | None = None) -> JevSiteClassifier:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json={"model": "jev-test", "answers": {"site": answer} if answer else {}})
    return JevSiteClassifier(client=httpx.Client(transport=httpx.MockTransport(handle)))


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")


def test_scam_page_is_flagged_with_a_reason():
    verdict = classifier({"choice": "SCAM", "probabilities": {"SCAM": 0.92, "LEGIT": 0.08}}).classify(FAKE)
    assert verdict.scam and verdict.scam_probability == 0.92 and verdict.model_id == "jev-test"
    assert "scam" in verdict.reason and FAKE.url in verdict.reason


def test_legitimate_page_passes():
    verdict = classifier({"choice": "LEGIT", "probabilities": {"SCAM": 0.03, "LEGIT": 0.97}}).classify(REAL)
    assert not verdict.scam


def test_only_the_page_is_sent_and_text_is_capped():
    seen = []
    long_page = Page(REAL.url, REAL.title, "x" * (MAX_TEXT + 500))
    classifier({"choice": "LEGIT", "probabilities": {"SCAM": 0.1, "LEGIT": 0.9}}, seen=seen).classify(long_page)
    question = seen[0]["questions"]["site"]
    assert set(question["criteria"]) == {"SCAM", "LEGIT"}
    assert len(question["instructions"]["visible_text"]) == MAX_TEXT
    assert question["instructions"]["url"] == REAL.url


def test_invalid_answer_is_an_error_not_a_pass():
    with pytest.raises(SiteCheckError):
        classifier({"choice": "MAYBE", "probabilities": {"MAYBE": 1.0}}).classify(FAKE)
    with pytest.raises(SiteCheckError):
        classifier(None).classify(FAKE)


def test_http_error_is_an_error_not_a_pass():
    with pytest.raises(SiteCheckError, match="HTTP 401"):
        classifier(status=401).classify(FAKE)


def test_missing_key_is_explained(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setattr("mandate.agent.selector.env_value", lambda name: None)
    with pytest.raises(SiteCheckError, match="check sites"):
        classifier().classify(FAKE)


def test_page_reads_the_browser_snapshot():
    page = Page.from_json(json.dumps({"url": FAKE.url, "title": None, "text": "hi"}))
    assert page == Page(FAKE.url, "", "hi")
    assert build_question(page)["site"]["instructions"]["title"] == ""
