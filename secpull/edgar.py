import json
import time

import requests

from secpull import config
from secpull.models import Company


class TickerNotFound(Exception): ...
class EdgarError(Exception): ...


_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def resolve_ticker(ticker: str, session=None) -> Company:
    if session is None:
        session = requests.Session()
    resp = session.get(
        _TICKER_URL,
        headers={"User-Agent": config.get_user_agent()},
        timeout=15,
    )
    if resp.status_code != 200:
        raise EdgarError(f"Ticker map fetch failed: HTTP {resp.status_code}")

    ticker_upper = ticker.upper()
    for entry in resp.json().values():
        if entry["ticker"].upper() == ticker_upper:
            cik = f"{entry['cik_str']:010d}"
            return Company(cik=cik, ticker=ticker_upper, name=entry["title"])

    raise TickerNotFound(f"Ticker not found in SEC registry: {ticker_upper}")


def fetch_companyfacts(cik: str, session=None) -> dict:
    if session is None:
        session = requests.Session()
    resp = session.get(
        _FACTS_URL.format(cik=cik),
        headers={"User-Agent": config.get_user_agent()},
        timeout=30,
    )
    if resp.status_code != 200:
        raise EdgarError(f"Company facts fetch failed: HTTP {resp.status_code}")
    try:
        return resp.json()
    except Exception as e:
        raise EdgarError(f"Invalid JSON in company facts response: {e}") from e


def pull_and_cache(ticker: str, session=None) -> tuple[Company, dict]:
    config.ensure_dirs()
    company = resolve_ticker(ticker, session=session)
    time.sleep(0.2)

    cache_path = config.RAW_DIR / f"{company.cik}.json"
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < 86400:
        return company, json.loads(cache_path.read_text())

    payload = fetch_companyfacts(company.cik, session=session)
    cache_path.write_text(json.dumps(payload))
    return company, payload
