"""Tests for secpull/statements.py."""
import json
import pytest

from secpull import config
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.models import FinancialFact, DerivedFact
from secpull.quality import COMPLETE, PARTIAL, DERIVED, STALE, UNRELIABLE
from secpull.statements import (
    MetricPoint,
    StatementLine,
    HistoricalStatements,
    build_statements,
)

# ── Synthetic fixture helpers ─────────────────────────────────────────────────

def _fact(metric, fy, value, quality=COMPLETE, fp="FY", form="10-K",
          filed="2026-02-01", tag="SomeTag", cik="0001111111"):
    end = f"{fy + (1 if metric != 'revenue' else 0)}-01-31"  # Jan FY end
    return FinancialFact(
        cik=cik, metric=metric, tag_used=tag,
        value=value, unit="USD",
        fiscal_year=fy, fiscal_period=fp,
        form=form, end_date=end, filed_date=filed,
        coverage_quality=quality,
    )


def _derived(metric, fy, value, formula="a + b", sources="a,b",
             flag="complete", cik="0001111111"):
    return DerivedFact(
        cik=cik, metric=metric, source="derived",
        formula_used=formula, source_metrics_used=sources,
        value=value, unit="USD",
        fiscal_year=fy, fiscal_period="FY",
        form="10-K", end_date=f"{fy + 1}-01-31",
        coverage_flag=flag,
    )


# ── Year-range tests ──────────────────────────────────────────────────────────

def test_years_limited_to_max_years():
    """build_statements respects max_years — takes the last N years only."""
    facts = [_fact("revenue", yr, float(yr * 1e9)) for yr in range(2015, 2026)]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    assert stmts.years == [2021, 2022, 2023, 2024, 2025]


def test_years_all_when_fewer_than_max():
    """When fewer than max_years are available, all years are included."""
    facts = [_fact("revenue", yr, 1e9) for yr in [2023, 2024, 2025]]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    assert stmts.years == [2023, 2024, 2025]


def test_years_sorted_ascending():
    facts = [_fact("revenue", yr, 1e9) for yr in [2025, 2023, 2024]]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    assert stmts.years == [2023, 2024, 2025]


def test_years_fy_only_not_quarterly():
    """Quarterly facts must not contribute to the year list."""
    fy_fact = _fact("revenue", 2025, 10e9)
    q_fact = FinancialFact(
        cik="0001111111", metric="revenue", tag_used="T",
        value=2.5e9, unit="USD",
        fiscal_year=2025, fiscal_period="Q1",
        form="10-Q", end_date="2025-04-30", filed_date="2025-05-15",
    )
    stmts = build_statements("0001111111", "TST", [fy_fact, q_fact], [], max_years=5)
    assert stmts.years == [2025]
    assert 2025 in stmts.income_statement.revenue.values
    # Value should come from the FY fact, not Q1
    assert stmts.income_statement.revenue.values[2025].value == 10e9


# ── MetricPoint quality tests ─────────────────────────────────────────────────

def test_complete_quality_preserved():
    facts = [_fact("revenue", 2025, 10e9, quality=COMPLETE)]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    pt = stmts.income_statement.revenue.values[2025]
    assert pt.coverage_quality == COMPLETE
    assert pt.value == 10e9


def test_partial_quality_preserved():
    facts = [_fact("cash", 2025, 1e9, quality=PARTIAL)]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    pt = stmts.balance_sheet.cash.values[2025]
    assert pt.coverage_quality == PARTIAL


def test_unreliable_override_applied():
    """Ford long_term_debt must be UNRELIABLE regardless of stored coverage_quality."""
    facts = [_fact("long_term_debt", 2020, 291e6, quality=COMPLETE, cik="0000037996")]
    stmts = build_statements("0000037996", "F", facts, [], max_years=5)
    pt = stmts.balance_sheet.long_term_debt.values[2020]
    assert pt.coverage_quality == UNRELIABLE


def test_non_unreliable_metric_not_overridden():
    """Revenue for Ford is COMPLETE — UNRELIABLE override must not bleed to other metrics."""
    facts = [
        _fact("revenue", 2025, 187e9, quality=COMPLETE, cik="0000037996"),
        _fact("long_term_debt", 2020, 291e6, quality=COMPLETE, cik="0000037996"),
    ]
    stmts = build_statements("0000037996", "F", facts, [], max_years=5)
    assert stmts.income_statement.revenue.values[2025].coverage_quality == COMPLETE


# ── Stale flag tests ──────────────────────────────────────────────────────────

def test_stale_flag_set_when_latest_fy_below_threshold():
    """A metric last seen at FY2023 must have is_stale=True."""
    facts = [_fact("interest_expense", 2023, 7.6e9)]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    assert stmts.income_statement.interest_expense.is_stale is True


def test_stale_flag_clear_when_current():
    facts = [_fact("revenue", 2025, 10e9)]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    assert stmts.income_statement.revenue.is_stale is False


def test_stale_line_still_has_historical_values():
    """Even when is_stale=True, historical MetricPoints must be accessible."""
    facts = [_fact("interest_expense", yr, float(yr)) for yr in [2021, 2022, 2023]]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    line = stmts.income_statement.interest_expense
    assert line.is_stale is True
    assert set(line.values.keys()) == {2021, 2022, 2023}


# ── Structural gap tests ──────────────────────────────────────────────────────

def test_structural_gap_flag_for_lulu_interest_expense():
    stmts = build_statements("0001397187", "LULU", [], [], max_years=5)
    assert stmts.income_statement.interest_expense.is_structural_gap is True
    assert stmts.income_statement.interest_expense.values == {}


def test_structural_gap_takes_priority_over_stale():
    """If a metric is both in STRUCTURAL_GAPS and stale, show as structural gap."""
    # VZ share_repurchases: in STRUCTURAL_GAPS, last data was FY2017
    facts = [_fact("share_repurchases", 2017, 0.0, cik="0000732712")]
    stmts = build_statements("0000732712", "VZ", facts, [], max_years=5)
    line = stmts.cash_flow_statement.share_repurchases
    assert line.is_structural_gap is True
    assert line.is_stale is False


# ── Deduplication tests ───────────────────────────────────────────────────────

def test_latest_filed_wins_for_same_metric_and_year():
    """When two facts have the same (metric, FY), the one with the later filed_date wins."""
    old_fact = _fact("revenue", 2025, 9.9e9, filed="2026-01-15")
    new_fact = _fact("revenue", 2025, 10.0e9, filed="2026-02-20")
    stmts = build_statements("0001111111", "TST", [old_fact, new_fact], [], max_years=5)
    assert stmts.income_statement.revenue.values[2025].value == 10.0e9


def test_quarterly_facts_excluded_from_statement_values():
    """Q1 facts for revenue must not appear as FY values."""
    fy = _fact("revenue", 2025, 10e9)
    q1 = FinancialFact(
        cik="0001111111", metric="revenue", tag_used="T",
        value=2.5e9, unit="USD",
        fiscal_year=2025, fiscal_period="Q1",
        form="10-Q", end_date="2025-04-30", filed_date="2025-05-15",
    )
    stmts = build_statements("0001111111", "TST", [fy, q1], [], max_years=5)
    assert len(stmts.income_statement.revenue.values) == 1
    assert stmts.income_statement.revenue.values[2025].value == 10e9


# ── Derived fact integration ──────────────────────────────────────────────────

def test_derived_fact_populates_ebitda_line():
    """EBITDA from DerivedFact must appear in income_statement.ebitda."""
    d = _derived("ebitda", 2025, 2.7e9)
    stmts = build_statements("0001111111", "TST", [], [d], max_years=5)
    pt = stmts.income_statement.ebitda.values.get(2025)
    assert pt is not None
    assert pt.value == 2.7e9
    assert pt.coverage_quality == DERIVED


def test_derived_fact_populates_fcf_line():
    d = _derived("fcf", 2025, 1.5e9)
    stmts = build_statements("0001111111", "TST", [], [d], max_years=5)
    pt = stmts.cash_flow_statement.fcf.values.get(2025)
    assert pt is not None
    assert pt.value == 1.5e9


def test_direct_fact_takes_priority_over_derived():
    """When both a direct fact and a derived fact exist for the same metric+year,
    the direct fact must win."""
    direct = _fact("total_liabilities", 2025, 253e9, quality=COMPLETE)
    derived = _derived("total_liabilities", 2025, 254e9)
    stmts = build_statements("0001111111", "TST", [direct], [derived], max_years=5)
    pt = stmts.balance_sheet.total_liabilities.values[2025]
    assert pt.value == 253e9
    assert pt.coverage_quality == COMPLETE


def test_derived_partial_coverage_flag_excluded():
    """DerivedFacts with coverage_flag != 'complete' must not appear in statements."""
    d = DerivedFact(
        cik="0001111111", metric="fcf", source="derived",
        formula_used="cfo - capex", source_metrics_used="cfo,capex",
        value=None, unit="USD",
        fiscal_year=2025, fiscal_period="FY",
        form="10-K", end_date="2026-01-31",
        coverage_flag="partial",   # <-- not complete
    )
    stmts = build_statements("0001111111", "TST", [], [d], max_years=5)
    assert 2025 not in stmts.cash_flow_statement.fcf.values


# ── change_in_nwc tests ───────────────────────────────────────────────────────

def test_change_in_nwc_sums_components():
    """change_in_nwc = sum of all available WC CFS components for each year."""
    facts = [
        _fact("change_in_accounts_receivable", 2025, -10e6),   # AR fell → +CF
        _fact("change_in_inventory",           2025, -20e6),   # INV fell → +CF
        _fact("change_in_accounts_payable",    2025,  30e6),   # AP rose  → +CF
        _fact("change_in_deferred_revenue",    2025,   5e6),   # DR rose  → +CF
    ]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    pt = stmts.cash_flow_statement.change_in_nwc.values.get(2025)
    assert pt is not None
    assert abs(pt.value - 5e6) < 1   # -10 - 20 + 30 + 5 = +5


def test_change_in_nwc_quality_derived_when_all_complete():
    facts = [
        _fact("change_in_accounts_receivable", 2025, -5e6, quality=COMPLETE),
        _fact("change_in_accounts_payable",    2025,  8e6, quality=COMPLETE),
    ]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    pt = stmts.cash_flow_statement.change_in_nwc.values[2025]
    assert pt.coverage_quality == DERIVED


def test_change_in_nwc_quality_partial_when_any_component_partial():
    facts = [
        _fact("change_in_accounts_receivable", 2025, -5e6, quality=PARTIAL),
        _fact("change_in_accounts_payable",    2025,  8e6, quality=COMPLETE),
    ]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    pt = stmts.cash_flow_statement.change_in_nwc.values[2025]
    assert pt.coverage_quality == PARTIAL


def test_change_in_nwc_absent_when_no_components():
    facts = [_fact("revenue", 2025, 10e9)]
    stmts = build_statements("0001111111", "TST", facts, [], max_years=5)
    assert 2025 not in stmts.cash_flow_statement.change_in_nwc.values


# ── Statement field existence tests ──────────────────────────────────────────

def test_income_statement_has_all_required_fields():
    stmts = build_statements("0001111111", "TST", [], [], max_years=5)
    is_ = stmts.income_statement
    for attr in ("revenue", "gross_profit", "ebit", "da", "ebitda",
                 "interest_expense", "income_tax_expense", "net_income",
                 "eps_diluted", "shares_diluted"):
        assert hasattr(is_, attr), f"IncomeStatement missing {attr}"
        assert isinstance(getattr(is_, attr), StatementLine)


def test_balance_sheet_has_all_required_fields():
    stmts = build_statements("0001111111", "TST", [], [], max_years=5)
    bs = stmts.balance_sheet
    for attr in ("cash", "accounts_receivable", "inventory", "other_current_assets",
                 "total_current_assets", "ppe_net", "goodwill", "intangibles_net",
                 "other_noncurrent_assets", "total_assets",
                 "accounts_payable", "accrued_liabilities", "current_portion_ltd",
                 "total_current_liabilities", "long_term_debt", "total_liabilities",
                 "retained_earnings", "total_equity"):
        assert hasattr(bs, attr), f"BalanceSheet missing {attr}"
        assert isinstance(getattr(bs, attr), StatementLine)


def test_cash_flow_statement_has_all_required_fields():
    stmts = build_statements("0001111111", "TST", [], [], max_years=5)
    cfs = stmts.cash_flow_statement
    for attr in ("net_income", "da", "stock_based_compensation", "change_in_nwc",
                 "cfo", "capex", "acquisitions", "cfi",
                 "debt_repayment", "dividends_paid", "share_repurchases", "cff", "fcf"):
        assert hasattr(cfs, attr), f"CashFlowStatement missing {attr}"
        assert isinstance(getattr(cfs, attr), StatementLine)


# ── Integration test against real cached data ─────────────────────────────────

@pytest.mark.parametrize("ticker,cik", [
    ("LULU", "0001397187"),
    ("F",    "0000037996"),
    ("VZ",   "0000732712"),
])
def test_integration_real_data_produces_valid_statements(ticker, cik):
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = __import__("json").load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, ticker, facts, derived, max_years=5)

    assert stmts.ticker == ticker
    assert stmts.cik == cik
    assert len(stmts.years) >= 1
    assert max(stmts.years) >= 2024

    # Revenue must have FY2025 for all three
    assert 2025 in stmts.income_statement.revenue.values
    assert stmts.income_statement.revenue.values[2025].value > 0

    # EBITDA derived for all three (no direct XBRL tag)
    assert 2025 in stmts.income_statement.ebitda.values
    assert stmts.income_statement.ebitda.values[2025].coverage_quality == DERIVED


def test_integration_lulu_interest_expense_is_structural_gap():
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = __import__("json").load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived)
    assert stmts.income_statement.interest_expense.is_structural_gap is True


def test_integration_vz_total_liabilities_derived():
    """VZ has no direct Liabilities tag — total_liabilities must come from derived."""
    cik = "0000732712"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = __import__("json").load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "VZ", facts, derived)
    tl = stmts.balance_sheet.total_liabilities
    assert 2025 in tl.values
    assert tl.values[2025].coverage_quality == DERIVED
    assert abs(tl.values[2025].value - 298.5e9) < 1e9


def test_integration_ford_interest_expense_is_stale():
    cik = "0000037996"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = __import__("json").load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "F", facts, derived)
    assert stmts.income_statement.interest_expense.is_stale is True
