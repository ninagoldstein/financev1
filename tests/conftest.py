import json
import pytest
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _user_agent_env(monkeypatch):
    monkeypatch.setenv("SECPULL_USER_AGENT", "SECPull/test (test@example.com)")


@pytest.fixture
def ticker_map() -> dict:
    return {"0": {"cik_str": 1397187, "ticker": "LULU",
                  "title": "lululemon athletica inc."}}


@pytest.fixture
def lulu_companyfacts() -> dict:
    return json.loads((FIXTURE_DIR / "lulu_companyfacts.json").read_text())
