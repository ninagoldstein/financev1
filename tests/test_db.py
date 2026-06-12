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


def test_insert_facts_updates_when_refiled_newer(conn):
    """A re-pull with a later filed_date must overwrite the stored value."""
    upsert_company(conn, LULU)
    insert_facts(conn, [FACT])

    restated = FinancialFact(
        cik=FACT.cik, metric=FACT.metric, tag_used=FACT.tag_used,
        value=9.9e9, unit=FACT.unit, fiscal_year=FACT.fiscal_year,
        fiscal_period=FACT.fiscal_period, form=FACT.form,
        end_date=FACT.end_date, filed_date="2025-06-01",  # later than FACT's 2024-03-21
    )
    n = insert_facts(conn, [restated])
    facts = get_facts(conn, cik=FACT.cik)
    assert len(facts) == 1
    assert facts[0].value == 9.9e9
    assert facts[0].filed_date == "2025-06-01"
    assert n == 1  # counts as a change


def test_insert_facts_ignores_older_refiling(conn):
    """A re-pull with an OLDER filed_date must never overwrite the stored value."""
    upsert_company(conn, LULU)
    insert_facts(conn, [FACT])  # filed 2024-03-21

    older = FinancialFact(
        cik=FACT.cik, metric=FACT.metric, tag_used=FACT.tag_used,
        value=1.0,  # wrong value from a stale source
        unit=FACT.unit, fiscal_year=FACT.fiscal_year,
        fiscal_period=FACT.fiscal_period, form=FACT.form,
        end_date=FACT.end_date, filed_date="2020-01-01",  # older
    )
    n = insert_facts(conn, [older])
    facts = get_facts(conn, cik=FACT.cik)
    assert facts[0].value == FACT.value   # original preserved
    assert facts[0].filed_date == "2024-03-21"
    assert n == 0  # no change
