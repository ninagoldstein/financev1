# SECPull — Phase B: EDGAR Client and Raw-Data Caching

## What This Phase Builds

A working HTTP client for the two SEC EDGAR endpoints, with polite headers, rate
limiting, error handling, and a local raw-JSON cache. After this phase,
`secpull pull LULU` resolves the ticker to a CIK, downloads the company's full
XBRL facts payload, and saves it to `data/raw/0001397187.json` — but does not yet
parse it (that's Phase C).

## Depends On

Phase A (package scaffold, `config.py`, CLI stubs).

## Files Touched

- `secpull/edgar.py` — created
- `secpull/cli.py` — modified (`pull` now calls the client and caches)
- `tests/test_edgar.py` — created
- `tests/fixtures/lulu_companyfacts.json` — created (see Task 5)
- `tests/conftest.py` — modified (`lulu_companyfacts` fixture now loads the file)

## External Endpoints (restated)

| Purpose         | URL                                                                  |
|-----------------|----------------------------------------------------------------------|
| Ticker → CIK    | `https://www.sec.gov/files/company_tickers.json`                     |
| Company facts   | `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json`      |

Every request sends `{"User-Agent": config.USER_AGENT}`. Sleep 0.2s between requests.
The ticker map response is a dict of `{"0": {"cik_str": 1397187, "ticker": "LULU",
"title": "lululemon athletica inc."}, ...}` — note `cik_str` is an **int** that must
be zero-padded to a 10-character string.

## Public Interface

```python
# secpull/edgar.py
from secpull.models import Company

class TickerNotFound(Exception): ...
class EdgarError(Exception): ...

def resolve_ticker(ticker: str, session=None) -> Company:
    """Look up ticker (case-insensitive) in the SEC registry.
    Raises TickerNotFound if absent."""

def fetch_companyfacts(cik: str, session=None) -> dict:
    """Download full companyfacts JSON for a zero-padded CIK.
    Raises EdgarError on non-200 or invalid JSON."""

def pull_and_cache(ticker: str, session=None) -> tuple[Company, dict]:
    """resolve_ticker → fetch_companyfacts → write raw JSON to
    config.RAW_DIR / f"{cik}.json" → return (company, payload).
    If the cache file exists and is < 24h old, read it instead of fetching."""
```

`session` is an injectable `requests.Session`-like object so tests can pass a mock.
Never call the real network in tests.

## Test Cases (Write First)

```python
# tests/test_edgar.py
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
        self.responses = responses   # url-substring -> FakeResponse
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
```

## Implementation Tasks

1. Implement `resolve_ticker`: GET the ticker map, scan values for a case-insensitive
   ticker match, build `Company` with `cik=f"{cik_str:010d}"`. Raise `TickerNotFound`
   with the message `Ticker not found in SEC registry: <TICKER>`.
2. Implement `fetch_companyfacts`: GET the companyfacts URL; raise `EdgarError` on
   any non-200 status or JSON decode failure. Include the status code in the message.
3. Implement `pull_and_cache` with the 24-hour cache rule (compare file mtime against
   `time.time()`); call `config.ensure_dirs()` first.
4. Wire the `pull` CLI handler: call `pull_and_cache`, upsert the company into the DB
   (`db.upsert_company`), print `Fetched <name> (CIK <cik>) — raw data cached.` On
   `TickerNotFound`, print the error to stderr and exit with code 1.
5. Build `tests/fixtures/lulu_companyfacts.json`: download the real LULU payload
   **once** with `curl` (User-Agent header set), then trim it to only the keys
   `cik`, `entityName`, and `facts.us-gaap` entries for `Revenues`,
   `RevenueFromContractWithCustomerExcludingAssessedTax`, `NetIncomeLoss`, and
   `EarningsPerShareDiluted`, keeping only USD/USD-per-share units and only data
   points whose `end` is in 2023 or 2024. Keep the file under ~100 KB.
6. Run `pytest` — Phases A and B green. Manually run
   `SECPULL_USER_AGENT="SECPull/0.1 (you@email)" python -m secpull pull LULU`
   once against the real API and confirm `data/raw/0001397187.json` exists. Commit.

## Deliverable Checklist

- [ ] `resolve_ticker("lulu")` returns CIK `0001397187` (case-insensitive, padded)
- [ ] Unknown ticker → `TickerNotFound`; CLI exits 1 with the specified message
- [ ] Every request includes a non-empty `User-Agent` header
- [ ] Raw payload cached to `data/raw/{cik}.json`; second pull within 24h hits cache
- [ ] No test makes a real network call
- [ ] Fixture file exists, < 100 KB, real EDGAR shape
- [ ] All tests pass; git commit made
