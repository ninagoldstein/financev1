"""
Regression tests for the Phase-G extraction hardening pass.

These tests verify that each tag-order fix produces the expected metric value
and tag selection from the real cached EDGAR JSON files.  They are integration
tests against production cache data, not unit tests against synthetic fixtures.
"""
import json
import sqlite3
import pytest

from secpull.db import init_db, upsert_company, insert_derived_facts, get_derived_facts
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.models import Company, FinancialFact
from secpull.quality import COMPLETE, PARTIAL


FORD_CIK = "0000037996"
LULU_CIK = "0001397187"
VZ_CIK   = "0000732712"


@pytest.fixture(scope="module")
def ford_raw():
    with open("data/raw/0000037996.json") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def lulu_raw():
    with open("data/raw/0001397187.json") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def vz_raw():
    with open("data/raw/0000732712.json") as f:
        return json.load(f)


# ── net_income fix ────────────────────────────────────────────────────────────

def test_ford_net_income_fy2025_tag(ford_raw):
    """Ford FY2025 net_income must come from the new tag, not the stale NetIncomeLoss."""
    facts = extract_metrics(FORD_CIK, ford_raw)
    ni = [f for f in facts
          if f.metric == "net_income" and f.fiscal_period == "FY"
          and f.fiscal_year == 2025]
    assert len(ni) == 1, "Expected exactly one FY2025 net_income fact for Ford"
    assert ni[0].tag_used == "NetIncomeLossAvailableToCommonStockholdersBasic"
    assert abs(ni[0].value - (-8.182e9)) < 1e8   # within $100M of -$8.182B


def test_ford_net_income_fy2025_is_loss(ford_raw):
    """Verify the extracted value is a loss — not the prior-year $5.879B profit."""
    facts = extract_metrics(FORD_CIK, ford_raw)
    ni = [f for f in facts
          if f.metric == "net_income" and f.fiscal_period == "FY"
          and f.fiscal_year == 2025]
    assert ni[0].value < 0, "Ford FY2025 net income should be negative (a loss)"


# ── cash fix ──────────────────────────────────────────────────────────────────

def test_lulu_cash_fy2025_tag(lulu_raw):
    """LULU FY2025 cash must come from the ASC-230 tag, not the stale FY2019 tag."""
    facts = extract_metrics(LULU_CIK, lulu_raw)
    cash = [f for f in facts
            if f.metric == "cash" and f.fiscal_period == "FY"
            and f.fiscal_year == 2025]
    assert len(cash) == 1
    assert cash[0].tag_used == "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    assert abs(cash[0].value - 1.807e9) < 1e8


def test_lulu_cash_fy2025_is_partial(lulu_raw):
    """Cash from the ASC-230 tag must be marked PARTIAL (includes restricted cash)."""
    facts = extract_metrics(LULU_CIK, lulu_raw)
    cash = next(
        f for f in facts if f.metric == "cash" and f.fiscal_period == "FY"
        and f.fiscal_year == 2025
    )
    assert cash.coverage_quality == PARTIAL


# ── capex fix ─────────────────────────────────────────────────────────────────

def test_vz_capex_fy2025_tag(vz_raw):
    """VZ FY2025 capex must come from the current tag, not the stale FY2018 tag."""
    facts = extract_metrics(VZ_CIK, vz_raw)
    capex = [f for f in facts
             if f.metric == "capex" and f.fiscal_period == "FY"
             and f.fiscal_year == 2025]
    assert len(capex) == 1
    assert capex[0].tag_used == "PaymentsToAcquireOtherProductiveAssets"
    assert abs(capex[0].value - 17.011e9) < 1e8


def test_lulu_capex_unaffected(lulu_raw):
    """LULU capex must still use PaymentsToAcquirePropertyPlantAndEquipment."""
    facts = extract_metrics(LULU_CIK, lulu_raw)
    capex = [f for f in facts
             if f.metric == "capex" and f.fiscal_period == "FY"]
    assert capex, "LULU should have capex data"
    latest = max(capex, key=lambda f: f.fiscal_year)
    assert latest.tag_used == "PaymentsToAcquirePropertyPlantAndEquipment"


def test_ford_capex_unaffected(ford_raw):
    """Ford capex must still use PaymentsToAcquireProductiveAssets."""
    facts = extract_metrics(FORD_CIK, ford_raw)
    capex = [f for f in facts
             if f.metric == "capex" and f.fiscal_period == "FY"]
    assert capex, "Ford should have capex data"
    latest = max(capex, key=lambda f: f.fiscal_year)
    assert latest.tag_used == "PaymentsToAcquireProductiveAssets"


# ── debt_repayment fix ────────────────────────────────────────────────────────

def test_vz_debt_repayment_fy2025_tag(vz_raw):
    """VZ debt_repayment must use RepaymentsOfDebt, not the stale FY2011 tag."""
    facts = extract_metrics(VZ_CIK, vz_raw)
    dr = [f for f in facts
          if f.metric == "debt_repayment" and f.fiscal_period == "FY"
          and f.fiscal_year == 2025]
    assert len(dr) == 1
    assert dr[0].tag_used == "RepaymentsOfDebt"
    assert abs(dr[0].value - 9.034e9) < 1e8


# ── VZ total_liabilities: derived, not direct ────────────────────────────────

def test_vz_total_liabilities_has_no_direct_tag(vz_raw):
    """VZ total_liabilities should be absent as a direct metric."""
    facts = extract_metrics(VZ_CIK, vz_raw)
    direct = [f for f in facts
              if f.metric == "total_liabilities" and f.fiscal_period == "FY"]
    assert len(direct) == 0, "VZ should have no direct total_liabilities tag"


def test_vz_total_liabilities_derived_fy2025(vz_raw):
    """VZ FY2025 total_liabilities must be derivable as total_assets - total_equity."""
    facts = extract_metrics(VZ_CIK, vz_raw)
    derived = compute_derived_metrics(VZ_CIK, facts)
    tl = [d for d in derived
          if d.metric == "total_liabilities" and d.fiscal_period == "FY"
          and d.fiscal_year == 2025 and d.coverage_flag == "complete"]
    assert len(tl) == 1
    # total_assets ~$404.3B, total_equity ~$105.7B → ~$298.5B
    assert abs(tl[0].value - 298.517e9) < 1e9


# ── FCF period isolation ──────────────────────────────────────────────────────

def test_derived_fcf_does_not_mix_periods():
    """When FY2025 CFO exists but capex is only in FY2018, FCF for FY2025 must
    be None with coverage_flag='partial', not computed from cross-period data."""
    cfo_2025 = FinancialFact(
        cik=VZ_CIK, metric="cfo",
        tag_used="NetCashProvidedByUsedInOperatingActivities",
        value=37.137e9, unit="USD", fiscal_year=2025, fiscal_period="FY",
        form="10-K", end_date="2025-12-31", filed_date="2026-02-17",
    )
    capex_2018 = FinancialFact(
        cik=VZ_CIK, metric="capex",
        tag_used="PaymentsToAcquireProductiveAssets",
        value=16.658e9, unit="USD", fiscal_year=2018, fiscal_period="FY",
        form="10-K", end_date="2018-12-31", filed_date="2019-02-15",
    )
    derived = compute_derived_metrics(VZ_CIK, [cfo_2025, capex_2018])

    fcf_2025 = next(
        (d for d in derived if d.metric == "fcf" and d.fiscal_year == 2025
         and d.fiscal_period == "FY"),
        None,
    )
    assert fcf_2025 is not None, "FCF row for FY2025 must exist even when inputs are partial"
    assert fcf_2025.coverage_flag in ("partial", "missing"), (
        "FCF for FY2025 must be partial/missing when FY2025 capex is absent"
    )
    assert fcf_2025.value is None, (
        "FCF value must be None — must not mix FY2025 CFO with FY2018 capex"
    )


def test_derived_fcf_computes_when_both_periods_present(vz_raw):
    """After the capex tag fix, VZ FY2025 FCF must be fully computable."""
    facts = extract_metrics(VZ_CIK, vz_raw)
    derived = compute_derived_metrics(VZ_CIK, facts)
    fcf_2025 = next(
        (d for d in derived if d.metric == "fcf" and d.fiscal_year == 2025
         and d.fiscal_period == "FY"),
        None,
    )
    assert fcf_2025 is not None
    assert fcf_2025.coverage_flag == "complete"
    assert fcf_2025.value is not None
    # CFO $37.137B - capex $17.011B ≈ $20.1B
    assert abs(fcf_2025.value - 20.126e9) < 5e8


# ── coverage_quality field round-trip ────────────────────────────────────────

def test_coverage_quality_stored_and_retrieved():
    """coverage_quality must survive DB insert/select round-trip."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    company = Company(cik="0009999999", ticker="TST", name="Test")
    upsert_company(conn, company)
    fact = FinancialFact(
        cik="0009999999", metric="cash",
        tag_used="CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        value=1.807e9, unit="USD", fiscal_year=2025, fiscal_period="FY",
        form="10-K", end_date="2026-02-01", filed_date="2026-03-17",
        coverage_quality=PARTIAL,
    )
    from secpull.db import insert_facts, get_facts
    insert_facts(conn, [fact])
    result = get_facts(conn, cik="0009999999")
    assert len(result) == 1
    assert result[0].coverage_quality == PARTIAL


def test_complete_quality_is_default():
    """Metrics extracted via clean tags must have coverage_quality=COMPLETE."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    company = Company(cik="0009999999", ticker="TST", name="Test")
    upsert_company(conn, company)
    fact = FinancialFact(
        cik="0009999999", metric="revenue",
        tag_used="Revenues",
        value=10e9, unit="USD", fiscal_year=2025, fiscal_period="FY",
        form="10-K", end_date="2025-12-31", filed_date="2026-02-01",
    )
    from secpull.db import insert_facts, get_facts
    insert_facts(conn, [fact])
    result = get_facts(conn, cik="0009999999")
    assert result[0].coverage_quality == COMPLETE
