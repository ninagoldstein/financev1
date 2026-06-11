import json
import pytest
from secpull.edgar import resolve_ticker, fetch_companyfacts, TickerNotFound, EdgarError
from secpull.models import Company


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = responses  # url-substring -> FakeResponse
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        for key, resp in self.responses.items():
            if key in url:
                return resp
        return FakeResponse(404)


TICKER_MAP = {"0": {"cik_str": 1397187, "ticker": "LULU",
                    "title": "lululemon athletica inc."}}


def test_resolve_ticker_pads_cik():
    s = FakeSession({"company_tickers.json": FakeResponse(200, TICKER_MAP)})
    c = resolve_ticker("lulu", session=s)
    assert c == Company(cik="0001397187", ticker="LULU",
                        name="lululemon athletica inc.")


def test_resolve_ticker_unknown_raises():
    s = FakeSession({"company_tickers.json": FakeResponse(200, TICKER_MAP)})
    with pytest.raises(TickerNotFound):
        resolve_ticker("ZZZZZZ", session=s)


def test_resolve_ticker_sends_user_agent():
    s = FakeSession({"company_tickers.json": FakeResponse(200, TICKER_MAP)})
    resolve_ticker("LULU", session=s)
    _, headers = s.calls[0]
    assert "User-Agent" in headers and headers["User-Agent"]


def test_fetch_companyfacts_happy(lulu_companyfacts):
    s = FakeSession({"companyfacts/CIK0001397187":
                     FakeResponse(200, lulu_companyfacts)})
    payload = fetch_companyfacts("0001397187", session=s)
    assert payload["cik"] == 1397187


def test_fetch_companyfacts_500_raises():
    s = FakeSession({"companyfacts": FakeResponse(500)})
    with pytest.raises(EdgarError):
        fetch_companyfacts("0001397187", session=s)
