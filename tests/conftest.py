import json
import pytest
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def ticker_map() -> dict:
    return {"0": {"cik_str": 1397187, "ticker": "LULU",
                  "title": "lululemon athletica inc."}}


@pytest.fixture
def lulu_companyfacts() -> dict:
    """Placeholder — real fixture constructed in Phase B."""
    return {}
