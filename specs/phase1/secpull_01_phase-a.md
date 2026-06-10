# SECPull — Phase A: Scaffolding, Schema, and CLI Stubs

## What This Phase Builds

The full project skeleton: package layout, dataclasses, config, the SQLite schema with
working insert/query/dedupe functions, and a CLI whose three commands (`pull`,
`report`, `export`) parse correctly but print "not implemented yet" stubs. After this
phase, `python -m secpull pull LULU` runs without crashing, and the database layer is
fully tested.

## Depends On

None.

## Files Touched

- `secpull/__init__.py` — created (empty)
- `secpull/__main__.py` — created
- `secpull/cli.py` — created
- `secpull/models.py` — created
- `secpull/config.py` — created
- `secpull/db.py` — created
- `tests/conftest.py` — created
- `tests/test_db.py` — created
- `requirements.txt` — created (`requests`, `openpyxl`, `pytest`)
- `.gitignore` — created (`data/`, `__pycache__/`, `.pytest_cache/`)

## Key Definitions (restated — this file is self-contained)

```python
# secpull/models.py
from dataclasses import dataclass

@dataclass(frozen=True)
class Company:
    cik: str          # zero-padded 10-digit string
    ticker: str
    name: str

@dataclass(frozen=True)
class FinancialFact:
    cik: str
    metric: str
    tag_used: str
    value: float
    unit: str
    fiscal_year: int
    fiscal_period: str  # "FY", "Q1"-"Q4"
    form: str
    end_date: str       # ISO date
    filed_date: str     # ISO date

METRIC_TAGS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "total_assets": ["Assets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}
```

```sql
CREATE TABLE IF NOT EXISTS companies (
    cik        TEXT PRIMARY KEY,
    ticker     TEXT NOT NULL,
    name       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
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

## Test Cases (Write First)

```python
# tests/test_db.py
import sqlite3
import pytest
from secpull.db import init_db, upsert_company, insert_facts, get_facts
from secpull.models import Company, FinancialFact

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    return c

LULU = Company(cik="0001397187", ticker="LULU", name="lululemon athletica inc.")

FACT = FinancialFact(
    cik="0001397187", metric="revenue", tag_used="Revenues",
    value=9.6e9, unit="USD", fiscal_year=2023, fiscal_period="FY",
    form="10-K", end_date="2024-01-28", filed_date="2024-03-21",
)

def test_schema_tables_exist(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "financials"} <= names

def test_upsert_company_twice_is_one_row(conn):
    upsert_company(conn, LULU)
    upsert_company(conn, LULU)
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1

def test_insert_facts_dedupes(conn):
    upsert_company(conn, LULU)
    inserted = insert_facts(conn, [FACT, FACT])
    assert inserted == 1
    assert conn.execute("SELECT COUNT(*) FROM financials").fetchone()[0] == 1

def test_get_facts_returns_dataclasses(conn):
    upsert_company(conn, LULU)
    insert_facts(conn, [FACT])
    facts = get_facts(conn, cik="0001397187")
    assert facts == [FACT]

def test_get_facts_empty_for_unknown_cik(conn):
    assert get_facts(conn, cik="0000000000") == []
```

```python
# tests/test_cli_stub.py (delete in Phase D)
import subprocess, sys

def test_cli_parses_pull():
    r = subprocess.run([sys.executable, "-m", "secpull", "pull", "LULU"],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "not implemented" in r.stdout.lower()

def test_cli_rejects_unknown_command():
    r = subprocess.run([sys.executable, "-m", "secpull", "frobnicate"],
                       capture_output=True, text=True)
    assert r.returncode == 2
```

## Implementation Tasks

1. Create the package layout and `requirements.txt`; `pip install -r requirements.txt`.
2. Create `secpull/models.py` exactly as defined above.
3. Create `secpull/config.py`:
   - `USER_AGENT` read from env var `SECPULL_USER_AGENT`, with a clear error if unset:
     `"Set SECPULL_USER_AGENT to 'AppName (your-email)' — the SEC requires it."`
   - `DATA_DIR = Path("data")`, `DB_PATH = DATA_DIR / "secpull.db"`,
     `RAW_DIR = DATA_DIR / "raw"`. A `ensure_dirs()` helper creates them.
4. Create `secpull/db.py` with: `init_db(conn)` (executes the DDL above),
   `upsert_company(conn, company)` (INSERT OR REPLACE), `insert_facts(conn, facts)
   -> int` (INSERT OR IGNORE, returns count actually inserted),
   `get_facts(conn, cik, metric=None) -> list[FinancialFact]`. No business logic here —
   only SQL in/out of dataclasses.
5. Create `secpull/cli.py` using argparse with subcommands `pull <ticker>`,
   `report <ticker>`, `export <ticker>`. Each handler prints
   `"<command>: not implemented yet"` and returns exit code 0. Uppercase the ticker.
6. Create `secpull/__main__.py` calling `cli.main()`.
7. Create `tests/conftest.py` with the `ticker_map` fixture (see overview) and an
   empty placeholder for `lulu_companyfacts` (built in Phase B).
8. Run `pytest` — all Phase A tests pass. Commit to git.

## Deliverable Checklist

- [ ] `pip install -r requirements.txt` succeeds in a fresh venv
- [ ] `python -m secpull pull LULU` prints a stub message, exit code 0
- [ ] Unknown subcommand exits with code 2 (argparse default)
- [ ] `init_db` creates both tables with the UNIQUE constraint on financials
- [ ] Duplicate fact insert is ignored, not errored
- [ ] All Phase A tests pass via `pytest`
- [ ] Initial git commit made
