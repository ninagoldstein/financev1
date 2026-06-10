# Engineering Spec: SECPull — Overview

## Purpose

SECPull is a command-line tool that takes a stock ticker and pulls that company's
official financial data from the SEC's free EDGAR APIs, stores it in a local SQLite
database, and produces clean reports — including an explicit list of what data is
*missing*, so nothing is ever guessed or interpolated. It exists to replace the manual
workflow of downloading 10-K/10-Q filings and hand-extracting numbers, and to serve as
a deterministic data layer that AI analysis can later sit on top of without
hallucinating financials.

Built by a beginner with Claude Code. Phases are small on purpose. Complete one phase
fully (all tests passing, checklist done) before opening the next file.

## System Context

```
User (terminal)
   │  secpull pull LULU / secpull report LULU / secpull export LULU
   ▼
CLI (cli.py) ── thin, no logic
   ▼
edgar.py  ──HTTP──▶  SEC EDGAR APIs (company_tickers.json, companyfacts)
   │  raw JSON cached to data/raw/
   ▼
extract.py  (pure functions: JSON → FinancialFact records)
   ▼
db.py  (SQLite read/write)
   ▼
report.py / export.py  (pure formatting → terminal table / Excel file)
```

Key rule: data ingestion and analysis are separate. The LLM/analysis layer (future)
only ever reads from the SQLite database — never from raw filings.

## Tech Stack

| Layer      | Technology                                  |
|------------|---------------------------------------------|
| Language   | Python 3.11+                                 |
| CLI        | argparse (stdlib)                            |
| HTTP       | requests                                     |
| Database   | SQLite via stdlib `sqlite3`                  |
| Excel      | openpyxl (Phase D only)                      |
| Tests      | pytest                                       |
| External   | SEC EDGAR APIs (free, no API key)            |

## External APIs (the only data sources)

1. **Ticker → CIK map:** `https://www.sec.gov/files/company_tickers.json`
2. **Company facts (all XBRL financial data):**
   `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json`
   (CIK zero-padded to 10 digits, e.g. `CIK0001397187`)

Rules for every request:
- Send a `User-Agent` header identifying the user, e.g.
  `"SECPull/0.1 (nina.goldstein@example.com)"` — read from config, never hardcoded.
- Stay under 10 requests/second (we will be far under it; add a 0.2s sleep between calls).
- Cache raw responses to `data/raw/` so re-runs don't re-fetch.

## Architecture Principles

- All business logic (extraction, normalization, missing-data detection, formatting)
  lives in **pure functions** with no network or DB calls inside them.
- CLI handlers are thin: parse args → call functions → print results.
- **Never fabricate data.** If a metric isn't in EDGAR, it is stored as absent and
  reported as `N/A` — never estimated.
- Every external HTTP call is mocked in tests. Tests never hit the real SEC.
- SQLite file lives at `data/secpull.db`. Raw JSON cache at `data/raw/{cik}.json`.
- XBRL tag names vary across companies; metric extraction uses ordered fallback lists
  (see Data Model) and records which tag was actually used.

## Data Model

```python
# secpull/models.py
from dataclasses import dataclass

@dataclass(frozen=True)
class Company:
    cik: str          # zero-padded 10-digit string, e.g. "0001397187"
    ticker: str       # uppercase, e.g. "LULU"
    name: str         # e.g. "lululemon athletica inc."

@dataclass(frozen=True)
class FinancialFact:
    cik: str
    metric: str        # canonical name, e.g. "revenue"
    tag_used: str      # actual XBRL tag the value came from
    value: float
    unit: str          # e.g. "USD"
    fiscal_year: int   # e.g. 2024
    fiscal_period: str # "FY", "Q1", "Q2", "Q3", "Q4"
    form: str          # e.g. "10-K", "10-Q"
    end_date: str      # ISO date the period ends, e.g. "2024-01-28"
    filed_date: str    # ISO date the filing was made

# Canonical metrics and their XBRL tag fallback order (us-gaap taxonomy):
METRIC_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "total_assets": ["Assets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}
```

```sql
-- SQLite schema (data/secpull.db)
CREATE TABLE IF NOT EXISTS companies (
    cik        TEXT PRIMARY KEY,
    ticker     TEXT NOT NULL,
    name       TEXT NOT NULL,
    fetched_at TEXT NOT NULL          -- ISO timestamp of last pull
);

CREATE TABLE IF NOT EXISTS financials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cik           TEXT NOT NULL REFERENCES companies(cik),
    metric        TEXT NOT NULL,
    tag_used      TEXT NOT NULL,
    value         REAL NOT NULL,
    unit          TEXT NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    fiscal_period TEXT NOT NULL,
    form          TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    filed_date    TEXT NOT NULL,
    UNIQUE (cik, metric, fiscal_year, fiscal_period, form, end_date)
);
```

## Worked Example

**Company: Lululemon Athletica (ticker `LULU`, CIK `0001397187`).** Note: Lululemon's
fiscal year ends in late January/early February, so its "FY2023" annual figures come
from a 10-K filed in early calendar 2024. This makes it a good test case for not
assuming calendar years.

### Input
```
secpull pull LULU
secpull report LULU
```

### Expected Output
- `companies` table contains one row: ticker `LULU`, CIK `0001397187`.
- `financials` contains a `revenue` row with `fiscal_period = "FY"`, `form = "10-K"`,
  whose annual revenue for the fiscal year ending January 2024 is **between
  $9.0 billion and $10.5 billion** (9.0e9 ≤ value ≤ 1.05e10, unit USD).
- `secpull report LULU` prints a table of metrics by fiscal year/period, and a
  **Missing Data** section listing any (metric, period) combinations not found —
  printed as `N/A`, never as a number.
- Asking for an unknown ticker (`secpull pull ZZZZZZ`) exits with code 1 and the
  message `Ticker not found in SEC registry: ZZZZZZ`. Nothing is written to the DB.

### Test Assertions (fixture used across all phases)
```python
# tests/conftest.py
import json, pytest
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"

@pytest.fixture
def lulu_companyfacts() -> dict:
    """Trimmed real-shaped companyfacts payload for LULU (see Phase B tasks
    for how to construct it). Contains us-gaap Revenues-family and
    NetIncomeLoss data for FY ending 2024-01-28, plus one quarterly point."""
    return json.loads((FIXTURE_DIR / "lulu_companyfacts.json").read_text())

@pytest.fixture
def ticker_map() -> dict:
    return {"0": {"cik_str": 1397187, "ticker": "LULU",
                  "title": "lululemon athletica inc."}}
```

```python
# canonical worked-example assertion (Phase C)
def test_lulu_fy_revenue_in_range(lulu_companyfacts):
    facts = extract_metrics("0001397187", lulu_companyfacts)
    fy_rev = [f for f in facts
              if f.metric == "revenue" and f.fiscal_period == "FY"
              and f.end_date == "2024-01-28"]
    assert len(fy_rev) == 1
    assert 9.0e9 <= fy_rev[0].value <= 1.05e10
```

## File Index

| File path                        | Phase | What it contains                          |
|----------------------------------|-------|-------------------------------------------|
| `secpull/__init__.py`            | A     | Package marker                             |
| `secpull/__main__.py`            | A     | `python -m secpull` entry point            |
| `secpull/cli.py`                 | A     | argparse setup, thin command handlers      |
| `secpull/models.py`              | A     | Dataclasses + METRIC_TAGS                  |
| `secpull/config.py`              | A     | User-Agent string, paths, constants        |
| `secpull/db.py`                  | A     | Schema creation, insert/query functions    |
| `secpull/edgar.py`               | B     | HTTP client: ticker map + companyfacts     |
| `secpull/extract.py`             | C     | Pure functions: JSON → FinancialFact list  |
| `secpull/report.py`              | D     | Pure formatting: table + missing-data list |
| `secpull/export.py`              | D     | Excel export via openpyxl                  |
| `tests/conftest.py`              | A     | Fixtures (ticker map, LULU companyfacts)   |
| `tests/fixtures/lulu_companyfacts.json` | B | Trimmed real-shaped EDGAR payload     |
| `tests/test_db.py`               | A     | Schema + insert/dedupe tests               |
| `tests/test_edgar.py`            | B     | Mocked HTTP client tests                   |
| `tests/test_extract.py`          | C     | Extraction logic tests (worked example)    |
| `tests/test_report.py`           | D     | Report + missing-data tests                |

## Phase Plan

| Phase | File                          | What exists after it                                |
|-------|-------------------------------|-----------------------------------------------------|
| A     | `secpull_01_phase-a.md`       | Project scaffold, DB schema, models, CLI stubs      |
| B     | `secpull_02_phase-b.md`       | Working EDGAR client with caching; `pull` fetches   |
| C     | `secpull_03_phase-c.md`       | Extraction logic; `pull` stores real financials     |
| D     | `secpull_04_phase-d.md`       | `report` and `export` commands; missing-data logic  |
| E     | `secpull_05_phase-e.md`       | (Stretch) Peer comparison across multiple tickers   |
