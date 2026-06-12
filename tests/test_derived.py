import sqlite3

import pytest

from secpull.db import get_derived_facts, init_db, insert_derived_facts, upsert_company
from secpull.derived import compute_derived_metrics
from secpull.models import Company, DerivedFact, FinancialFact

CIK = "0009999999"
COMPANY = Company(cik=CIK, ticker="TEST", name="Test Corp")


def _fact(metric: str, value: float) -> FinancialFact:
    return FinancialFact(
        cik=CIK, metric=metric, tag_used="TestTag",
        value=value, unit="USD", fiscal_year=2023, fiscal_period="FY",
        form="10-K", end_date="2023-12-31", filed_date="2024-02-15",
    )


# ── Core formula correctness ──────────────────────────────────────────────────

def test_ebitda_derivation():
    facts = [_fact("operating_income", 1_000_000), _fact("depreciation_amortization", 200_000)]
    derived = compute_derived_metrics(CIK, facts)
    ebitda = next(d for d in derived if d.metric == "ebitda")
    assert ebitda.value == 1_200_000.0
    assert ebitda.coverage_flag == "complete"
    assert ebitda.source == "derived"
    assert ebitda.unit == "USD"


def test_fcf_derivation():
    facts = [_fact("cfo", 5_000_000), _fact("capex", 1_500_000)]
    derived = compute_derived_metrics(CIK, facts)
    fcf = next(d for d in derived if d.metric == "fcf")
    assert fcf.value == 3_500_000.0
    assert fcf.coverage_flag == "complete"


def test_total_liabilities_derivation():
    facts = [_fact("total_assets", 10_000_000), _fact("total_equity", 3_000_000)]
    derived = compute_derived_metrics(CIK, facts)
    tl = next(d for d in derived if d.metric == "total_liabilities")
    assert tl.value == 7_000_000.0
    assert tl.coverage_flag == "complete"


def test_ebitda_margin_complete():
    facts = [
        _fact("operating_income", 1_000_000),
        _fact("depreciation_amortization", 200_000),
        _fact("revenue", 10_000_000),
    ]
    derived = compute_derived_metrics(CIK, facts)
    margin = next(d for d in derived if d.metric == "ebitda_margin")
    assert abs(margin.value - 0.12) < 1e-9
    assert margin.unit == "ratio"
    assert margin.coverage_flag == "complete"


def test_net_margin_complete():
    facts = [_fact("net_income", 800_000), _fact("revenue", 10_000_000)]
    derived = compute_derived_metrics(CIK, facts)
    m = next(d for d in derived if d.metric == "net_margin")
    assert abs(m.value - 0.08) < 1e-9


def test_working_capital_complete():
    facts = [_fact("total_current_assets", 5_000_000), _fact("total_current_liabilities", 2_000_000)]
    derived = compute_derived_metrics(CIK, facts)
    wc = next(d for d in derived if d.metric == "working_capital")
    assert wc.value == 3_000_000.0
    assert wc.coverage_flag == "complete"


# ── Coverage flags ────────────────────────────────────────────────────────────

def test_partial_flag_when_one_input_missing():
    facts = [_fact("operating_income", 1_000_000)]  # missing depreciation_amortization
    derived = compute_derived_metrics(CIK, facts)
    ebitda = next(d for d in derived if d.metric == "ebitda")
    assert ebitda.value is None
    assert ebitda.coverage_flag == "partial"


def test_missing_flag_when_no_inputs_present():
    facts = [_fact("revenue", 5_000_000)]  # no inputs for ebitda at all
    derived = compute_derived_metrics(CIK, facts)
    ebitda = next(d for d in derived if d.metric == "ebitda")
    assert ebitda.value is None
    assert ebitda.coverage_flag == "missing"


def test_all_seven_metrics_produced_per_period():
    facts = [_fact("revenue", 1.0)]
    derived = compute_derived_metrics(CIK, facts)
    metrics_produced = {d.metric for d in derived}
    expected = {
        "ebitda", "ebitda_margin", "ebit_margin", "net_margin",
        "working_capital", "fcf", "total_liabilities",
    }
    assert expected == metrics_produced


# ── Formula metadata ──────────────────────────────────────────────────────────

def test_source_metrics_used_recorded():
    facts = [_fact("cfo", 1.0), _fact("capex", 0.5)]
    derived = compute_derived_metrics(CIK, facts)
    fcf = next(d for d in derived if d.metric == "fcf")
    assert "cfo" in fcf.source_metrics_used
    assert "capex" in fcf.source_metrics_used


def test_formula_used_recorded():
    facts = [_fact("total_assets", 10.0), _fact("total_equity", 3.0)]
    derived = compute_derived_metrics(CIK, facts)
    tl = next(d for d in derived if d.metric == "total_liabilities")
    assert tl.formula_used == "total_assets - total_equity"


# ── DB round-trip ─────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    return c


def test_insert_and_get_derived_facts(conn):
    upsert_company(conn, COMPANY)
    df = DerivedFact(
        cik=CIK, metric="ebitda", source="derived",
        formula_used="operating_income + depreciation_amortization",
        source_metrics_used="operating_income,depreciation_amortization",
        value=1_200_000.0, unit="USD", fiscal_year=2023, fiscal_period="FY",
        form="10-K", end_date="2023-12-31", coverage_flag="complete",
    )
    n = insert_derived_facts(conn, [df])
    assert n == 1
    facts = get_derived_facts(conn, cik=CIK)
    assert len(facts) == 1
    assert facts[0] == df


def test_insert_derived_facts_upserts_on_recompute(conn):
    upsert_company(conn, COMPANY)
    df = DerivedFact(
        cik=CIK, metric="ebitda", source="derived",
        formula_used="operating_income + depreciation_amortization",
        source_metrics_used="operating_income,depreciation_amortization",
        value=1_000_000.0, unit="USD", fiscal_year=2023, fiscal_period="FY",
        form="10-K", end_date="2023-12-31", coverage_flag="complete",
    )
    insert_derived_facts(conn, [df])
    updated = DerivedFact(
        cik=CIK, metric="ebitda", source="derived",
        formula_used="operating_income + depreciation_amortization",
        source_metrics_used="operating_income,depreciation_amortization",
        value=1_500_000.0, unit="USD", fiscal_year=2023, fiscal_period="FY",
        form="10-K", end_date="2023-12-31", coverage_flag="complete",
    )
    insert_derived_facts(conn, [updated])
    facts = get_derived_facts(conn, cik=CIK)
    assert len(facts) == 1
    assert facts[0].value == 1_500_000.0


def test_get_derived_facts_filter_by_metric(conn):
    upsert_company(conn, COMPANY)
    facts = [_fact("cfo", 5_000_000), _fact("capex", 1_500_000),
             _fact("total_assets", 10_000_000), _fact("total_equity", 3_000_000)]
    derived = compute_derived_metrics(CIK, facts)
    insert_derived_facts(conn, derived)
    fcf_rows = get_derived_facts(conn, cik=CIK, metric="fcf")
    assert all(d.metric == "fcf" for d in fcf_rows)
    assert fcf_rows[0].value == 3_500_000.0


def test_get_derived_facts_filter_by_coverage_flag(conn):
    upsert_company(conn, COMPANY)
    facts = [_fact("operating_income", 1_000_000)]  # partial only for ebitda
    derived = compute_derived_metrics(CIK, facts)
    insert_derived_facts(conn, derived)
    complete = get_derived_facts(conn, cik=CIK, coverage_flag="complete")
    partial = get_derived_facts(conn, cik=CIK, coverage_flag="partial")
    assert all(d.coverage_flag == "complete" for d in complete)
    assert all(d.coverage_flag == "partial" for d in partial)
    assert not any(d.metric == "ebitda" for d in complete)
    assert any(d.metric == "ebitda" for d in partial)
