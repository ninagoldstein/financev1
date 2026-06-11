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
