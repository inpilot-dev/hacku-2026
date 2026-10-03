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
        assert web_search("charger", client) == [{"url": "https://shop.example.com/charger", "title": "65W charger"}]
