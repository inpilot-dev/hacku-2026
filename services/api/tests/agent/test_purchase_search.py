"""Search provider failures must not be presented as missing products."""
import httpx
import pytest

from mandate.agent.purchase.search import SearchError, web_search


@pytest.mark.parametrize("status, body", [
    (202, "<html>Verification required</html>"),
    (200, '<form id="challenge-form"></form>'),
    (200, '<div class="anomaly-modal"></div>'),
])
def test_verification_is_a_provider_error(status, body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body))) as client:
        with pytest.raises(SearchError, match="verification"):
            web_search("charger", client)
        assert not client.is_closed


def test_shop_results_still_parse():
    body = '<a class="result__a" href="https://shop.example.com/charger">65W charger</a>'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))) as client:
        result = web_search("charger", client)
        assert result[0]["url"] == "https://shop.example.com/charger"
        assert result[0]["title"] == "65W charger"
        assert result[0]["provider"] == "duckduckgo"
        assert result[0]["observed_at"]


def test_serper_observation_and_dedup(monkeypatch):
    monkeypatch.setenv('MANDATE_SEARCH_PROVIDER', 'serper')
    monkeypatch.setenv('SERPER_API_KEY', 'fixture')
    def respond(req):
        assert req.headers['X-API-KEY'] == 'fixture'
        return httpx.Response(200, json={'organic': [{'link': 'https://shop.example.com/a', 'title': 'Rice'}, {'link': 'https://shop.example.com/a', 'title': 'Duplicate'}, {'link': 'https://youtube.com/a', 'title': 'Review'}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = web_search('rice', client)
    assert len(result) == 1 and result[0]['provider'] == 'serper' and result[0]['observed_at']


def test_serper_failure_never_invents_products(monkeypatch):
    monkeypatch.setenv('MANDATE_SEARCH_PROVIDER', 'serper')
    monkeypatch.setenv('SERPER_API_KEY', 'fixture')
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(401))) as client:
        with pytest.raises(SearchError):
            web_search('rice', client)
