"""Tests for secpull/forecast.py."""
import json
import math
import pytest

from secpull import config
from secpull.assumptions import ForecastAssumptions, build_assumptions_from_profile
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.forecast import (
    ForecastYear,
    ProjectedStatements,
    ScenarioForecast,
    build_forecast,
)
from secpull.models import FinancialFact
from secpull.profile import build_profile
from secpull.quality import COMPLETE, QualityIssue
from secpull.statements import build_statements

# ── Synthetic helpers ─────────────────────────────────────────────────────────

_CIK = "0001111111"
_TICKER = "TST"


def _f(metric, fy, value, quality=COMPLETE, filed="2026-02-01"):
    return FinancialFact(
        cik=_CIK, metric=metric, tag_used="Tag",
        value=value, unit="USD",
        fiscal_year=fy, fiscal_period="FY",
        form="10-K", end_date=f"{fy + 1}-01-31", filed_date=filed,
        coverage_quality=quality,
    )


def _fixed_assumptions(**kwargs) -> ForecastAssumptions:
    """Return a ForecastAssumptions with controlled values for formula tests."""
    defaults = dict(
        base_revenue_growth=0.10,
        bear_revenue_growth=0.07,
        bull_revenue_growth=0.13,
        gross_margin=0.55,
        ebit_margin=0.20,
        da_pct_revenue=0.05,
        effective_tax_rate=0.25,
        capex_pct_revenue=0.06,
        nwc_pct_revenue=0.10,
        interest_rate_on_debt=None,
        n_projection_years=5,
        quality_issues=(),
    )
    defaults.update(kwargs)
    return ForecastAssumptions(**defaults)


def _revenue_profile(base_rev=100.0, base_yr=2025):
    """Profile with a single revenue fact at base_yr."""
    facts = [_f("revenue", base_yr, base_rev)]
    stmts = build_statements(_CIK, _TICKER, facts, [], max_years=5)
    return build_profile(stmts, "Test Co")


def _full_profile(base_rev=1_000.0, base_yr=2025):
    """Profile with revenue + operating_income + D&A + gross_profit + capex + NWC facts."""
    facts = [
        _f("revenue", base_yr, base_rev),
        _f("gross_profit", base_yr, base_rev * 0.55),
        _f("operating_income", base_yr, base_rev * 0.20),
        _f("depreciation_amortization", base_yr, base_rev * 0.05),
        _f("income_tax_expense", base_yr, base_rev * 0.04),
        _f("capex", base_yr, base_rev * 0.06),
        _f("accounts_receivable", base_yr, base_rev * 0.10),
        _f("inventory", base_yr, base_rev * 0.05),
        _f("prepaid_other_current", base_yr, base_rev * 0.02),
        _f("accounts_payable", base_yr, base_rev * 0.08),
        _f("accrued_liabilities", base_yr, base_rev * 0.03),
        _f("long_term_debt", base_yr, base_rev * 2.0),   # debt for interest tests
    ]
    stmts = build_statements(_CIK, _TICKER, facts, [], max_years=5)
    return build_profile(stmts, "Test Co")


# ── Shape / structure ─────────────────────────────────────────────────────────

def test_projected_statements_fields():
    profile = _revenue_profile()
    asmp = _fixed_assumptions()
    ps = build_forecast(asmp, profile)
    assert isinstance(ps, ProjectedStatements)
    assert ps.ticker == _TICKER
    assert ps.base_year == 2025
    assert abs(ps.base_revenue - 100.0) < 1e-9


def test_each_scenario_has_correct_year_count():
    profile = _revenue_profile()
    asmp = _fixed_assumptions(n_projection_years=5)
    ps = build_forecast(asmp, profile)
    for scenario in (ps.bear, ps.base, ps.bull):
        assert len(scenario.years) == 5


def test_projection_year_labels():
    """Year numbers start at base_year + 1 and increment by 1."""
    profile = _revenue_profile(base_yr=2025)
    asmp = _fixed_assumptions(n_projection_years=3)
    ps = build_forecast(asmp, profile)
    assert [fy.year for fy in ps.base.years] == [2026, 2027, 2028]


def test_scenario_names_and_rates():
    profile = _revenue_profile()
    asmp = _fixed_assumptions()
    ps = build_forecast(asmp, profile)
    assert ps.bear.scenario == "bear"
    assert ps.base.scenario == "base"
    assert ps.bull.scenario == "bull"
    assert abs(ps.bear.growth_rate - 0.07) < 1e-9
    assert abs(ps.base.growth_rate - 0.10) < 1e-9
    assert abs(ps.bull.growth_rate - 0.13) < 1e-9


def test_forecast_year_is_frozen():
    profile = _revenue_profile()
    ps = build_forecast(_fixed_assumptions(), profile)
    fy = ps.base.years[0]
    with pytest.raises((AttributeError, TypeError)):
        fy.revenue = 0.0  # type: ignore[misc]


# ── Revenue compounding ───────────────────────────────────────────────────────

def test_revenue_compounds_from_base():
    """Revenue at year n = base_revenue × (1 + growth)^n."""
    base_rev = 100.0
    growth = 0.10
    profile = _revenue_profile(base_rev=base_rev)
    asmp = _fixed_assumptions(
        base_revenue_growth=growth,
        bear_revenue_growth=growth,
        bull_revenue_growth=growth,
        n_projection_years=5,
    )
    ps = build_forecast(asmp, profile)
    for i, fy in enumerate(ps.base.years, start=1):
        expected = base_rev * (1 + growth) ** i
        assert abs(fy.revenue - expected) < 1e-6, f"Year {i}: {fy.revenue} != {expected}"


def test_revenue_year1_exact():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(base_revenue_growth=0.08)
    ps = build_forecast(asmp, profile)
    assert abs(ps.base.years[0].revenue - 1_080.0) < 1e-6


def test_revenue_year_n_uses_prior_year_not_base():
    """Year 3 revenue must use Year 2 revenue as its prior, not base_revenue."""
    base_rev = 1_000.0
    growth = 0.10
    profile = _revenue_profile(base_rev=base_rev)
    asmp = _fixed_assumptions(base_revenue_growth=growth, n_projection_years=3)
    ps = build_forecast(asmp, profile)
    # Year 2 should be 1000 × 1.10^2 = 1210, year 3 = 1210 × 1.10 = 1331
    assert abs(ps.base.years[2].revenue - 1_331.0) < 1e-4


# ── Bear / base / bull ordering ───────────────────────────────────────────────

def test_bear_base_bull_revenue_ordering():
    """bear revenue < base revenue < bull revenue for every projected year."""
    profile = _revenue_profile(base_rev=100.0)
    asmp = _fixed_assumptions()
    ps = build_forecast(asmp, profile)
    for b, m, u in zip(ps.bear.years, ps.base.years, ps.bull.years):
        assert b.revenue < m.revenue < u.revenue


def test_bear_base_bull_fcff_ordering():
    """Higher growth → higher FCFF (all else equal, positive margins)."""
    profile = _revenue_profile(base_rev=100.0)
    asmp = _fixed_assumptions()
    ps = build_forecast(asmp, profile)
    for b, m, u in zip(ps.bear.years, ps.base.years, ps.bull.years):
        assert b.fcff < m.fcff < u.fcff


# ── Income statement formulas ─────────────────────────────────────────────────

def test_gross_profit_equals_revenue_times_margin():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(gross_margin=0.55, base_revenue_growth=0.10)
    ps = build_forecast(asmp, profile)
    fy = ps.base.years[0]   # Year 1: revenue = 1100
    assert fy.gross_profit is not None
    assert abs(fy.gross_profit - 1_100.0 * 0.55) < 1e-6


def test_ebit_equals_revenue_times_ebit_margin():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(ebit_margin=0.20, base_revenue_growth=0.10)
    ps = build_forecast(asmp, profile)
    fy = ps.base.years[0]
    assert abs(fy.ebit - 1_100.0 * 0.20) < 1e-6


def test_da_equals_revenue_times_da_pct():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(da_pct_revenue=0.05, base_revenue_growth=0.10)
    ps = build_forecast(asmp, profile)
    fy = ps.base.years[0]
    assert abs(fy.da - 1_100.0 * 0.05) < 1e-6


def test_ebitda_equals_ebit_plus_da():
    """EBITDA = EBIT + D&A for every year, every scenario."""
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions()
    ps = build_forecast(asmp, profile)
    for scenario in (ps.bear, ps.base, ps.bull):
        for fy in scenario.years:
            assert abs(fy.ebitda - (fy.ebit + fy.da)) < 1e-9, (
                f"{scenario.scenario} year {fy.year}: "
                f"ebitda={fy.ebitda} != ebit+da={fy.ebit + fy.da}"
            )


# ── Gross margin None behavior ────────────────────────────────────────────────

def test_gross_profit_is_none_when_gross_margin_none():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(gross_margin=None)
    ps = build_forecast(asmp, profile)
    for scenario in (ps.bear, ps.base, ps.bull):
        for fy in scenario.years:
            assert fy.gross_profit is None


def test_gross_profit_present_when_gross_margin_set():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(gross_margin=0.60)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.gross_profit is not None


# ── Interest / EBT path ───────────────────────────────────────────────────────

def test_interest_fields_none_when_no_rate():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(interest_rate_on_debt=None)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.interest_expense is None
        assert fy.ebt is None
        assert fy.tax_expense is None


def test_net_income_uses_nopat_when_no_interest():
    """net_income = EBIT × (1 − tax) when interest path is off."""
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(
        ebit_margin=0.20,
        effective_tax_rate=0.25,
        interest_rate_on_debt=None,
        base_revenue_growth=0.10,
    )
    ps = build_forecast(asmp, profile)
    fy = ps.base.years[0]   # ebit = 1100 × 0.20 = 220; net_income = 220 × 0.75 = 165
    assert abs(fy.net_income - 220.0 * 0.75) < 1e-6


def test_interest_fields_set_when_rate_and_debt_available():
    """When interest_rate_on_debt is set and LTD exists, interest fields are populated."""
    profile = _full_profile(base_rev=1_000.0)   # includes LTD = 2000
    asmp = _fixed_assumptions(interest_rate_on_debt=0.05)
    ps = build_forecast(asmp, profile)
    fy = ps.base.years[0]
    assert fy.interest_expense is not None
    assert fy.ebt is not None
    assert fy.tax_expense is not None
    # IE = 2000 × 0.05 = 100; EBT = ebit - 100; tax = max(0, EBT) × 0.25
    expected_ie = 1_000.0 * 2.0 * 0.05   # LTD = 2× base_rev = 2000
    assert abs(fy.interest_expense - expected_ie) < 1e-6


def test_interest_expense_flat_across_years():
    """Interest expense is the same for all projection years (no BS rollforward)."""
    profile = _full_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(interest_rate_on_debt=0.05)
    ps = build_forecast(asmp, profile)
    ie_values = [fy.interest_expense for fy in ps.base.years]
    assert all(ie is not None for ie in ie_values)
    assert all(abs(ie - ie_values[0]) < 1e-9 for ie in ie_values)


def test_net_income_correct_with_interest():
    """net_income = EBT - tax_expense when interest path is active."""
    profile = _full_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(interest_rate_on_debt=0.05)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.ebt is not None
        assert fy.tax_expense is not None
        assert abs(fy.net_income - (fy.ebt - fy.tax_expense)) < 1e-9


def test_tax_not_negative_when_ebt_negative():
    """When EBT < 0 (loss), tax_expense = 0 and net_income = EBT."""
    profile = _full_profile(base_rev=1_000.0)
    # Make EBIT very small so IE > EBIT → EBT < 0
    asmp = _fixed_assumptions(
        ebit_margin=0.01,          # EBIT = tiny
        interest_rate_on_debt=0.10,  # IE = 2000 × 0.10 = 200 >> EBIT ≈ 11
    )
    ps = build_forecast(asmp, profile)
    fy = ps.base.years[0]
    assert fy.ebt is not None and fy.ebt < 0
    assert fy.tax_expense == 0.0
    assert abs(fy.net_income - fy.ebt) < 1e-9


def test_interest_fields_none_when_no_debt_data():
    """If interest_rate_on_debt is set but LTD is absent, fall back to no-interest path."""
    facts = [_f("revenue", 2025, 1_000.0)]   # no LTD
    stmts = build_statements(_CIK, _TICKER, facts, [], max_years=5)
    profile = build_profile(stmts, "Test Co")
    asmp = _fixed_assumptions(interest_rate_on_debt=0.05)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.interest_expense is None


# ── FCFF formula ─────────────────────────────────────────────────────────────

def test_fcff_formula_exact():
    """FCFF = EBIT×(1−t) + D&A − Capex − ΔNWC  (exact arithmetic check)."""
    base_rev = 100.0
    growth = 0.10
    # Year 1 revenue = 110
    # EBIT = 110 × 0.20 = 22
    # D&A  = 110 × 0.05 = 5.5
    # Capex = 110 × 0.06 = 6.6
    # ΔNWC = 0.10 × (110 − 100) = 1.0
    # NOPAT = 22 × 0.75 = 16.5
    # FCFF = 16.5 + 5.5 − 6.6 − 1.0 = 14.4
    profile = _revenue_profile(base_rev=base_rev)
    asmp = _fixed_assumptions(
        base_revenue_growth=growth,
        bear_revenue_growth=growth,
        bull_revenue_growth=growth,
        ebit_margin=0.20,
        da_pct_revenue=0.05,
        capex_pct_revenue=0.06,
        nwc_pct_revenue=0.10,
        effective_tax_rate=0.25,
    )
    ps = build_forecast(asmp, profile)
    fy1 = ps.base.years[0]
    assert abs(fy1.fcff - 14.4) < 1e-6


def test_fcff_equals_nopat_plus_da_minus_capex_minus_dnwc():
    """FCFF structural identity holds for every year and scenario."""
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions()
    ps = build_forecast(asmp, profile)
    for scenario in (ps.bear, ps.base, ps.bull):
        prior_rev = ps.base_revenue
        for fy in scenario.years:
            nopat = fy.ebit * (1 - asmp.effective_tax_rate)
            expected_fcff = nopat + fy.da - fy.capex - fy.delta_nwc
            assert abs(fy.fcff - expected_fcff) < 1e-9, (
                f"{scenario.scenario} year {fy.year}: "
                f"fcff={fy.fcff} != identity={expected_fcff}"
            )


def test_fcff_independent_of_interest_path():
    """FCFF must be the same whether or not interest_rate_on_debt is set."""
    profile = _full_profile(base_rev=1_000.0)
    asmp_no_ie = _fixed_assumptions(interest_rate_on_debt=None)
    asmp_with_ie = _fixed_assumptions(interest_rate_on_debt=0.05)
    ps_no_ie   = build_forecast(asmp_no_ie, profile)
    ps_with_ie = build_forecast(asmp_with_ie, profile)
    for fy_a, fy_b in zip(ps_no_ie.base.years, ps_with_ie.base.years):
        assert abs(fy_a.fcff - fy_b.fcff) < 1e-9


def test_delta_nwc_positive_when_growing():
    """Growing revenue with positive NWC % → positive ΔNWC (cash outflow)."""
    profile = _revenue_profile(base_rev=100.0)
    asmp = _fixed_assumptions(nwc_pct_revenue=0.10, base_revenue_growth=0.10)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.delta_nwc > 0


def test_delta_nwc_negative_when_shrinking():
    """Declining revenue with positive NWC % → negative ΔNWC (cash inflow)."""
    profile = _revenue_profile(base_rev=100.0)
    asmp = _fixed_assumptions(nwc_pct_revenue=0.10, base_revenue_growth=-0.05)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.delta_nwc < 0


# ── Capex ─────────────────────────────────────────────────────────────────────

def test_capex_equals_revenue_times_capex_pct():
    profile = _revenue_profile(base_rev=1_000.0)
    asmp = _fixed_assumptions(capex_pct_revenue=0.08, base_revenue_growth=0.10)
    ps = build_forecast(asmp, profile)
    fy = ps.base.years[0]   # revenue = 1100
    assert abs(fy.capex - 1_100.0 * 0.08) < 1e-6


# ── Quality propagation ───────────────────────────────────────────────────────

def test_quality_issues_propagated_as_tuple():
    profile = _revenue_profile()
    asmp = _fixed_assumptions(quality_issues=(
        QualityIssue(metric="__coverage__", severity="INFO", message="test"),
    ))
    ps = build_forecast(asmp, profile)
    assert isinstance(ps.quality_issues, tuple)
    assert len(ps.quality_issues) == 1
    assert ps.quality_issues[0].metric == "__coverage__"


def test_quality_issues_empty_tuple_by_default():
    profile = _revenue_profile()
    asmp = _fixed_assumptions(quality_issues=())
    ps = build_forecast(asmp, profile)
    assert ps.quality_issues == ()


def test_quality_issues_match_assumptions():
    profile = _revenue_profile()
    qi = QualityIssue(metric="interest_expense", severity="WARNING", message="stale")
    asmp = _fixed_assumptions(quality_issues=(qi,))
    ps = build_forecast(asmp, profile)
    assert ps.quality_issues[0] is qi


# ── No revenue error ──────────────────────────────────────────────────────────

def test_raises_when_no_revenue():
    """build_forecast must raise ValueError when profile has no revenue data."""
    facts = [_f("net_income", 2025, 1e6)]   # no revenue
    stmts = build_statements(_CIK, _TICKER, facts, [], max_years=5)
    profile = build_profile(stmts, "Test Co")
    asmp = _fixed_assumptions()
    with pytest.raises(ValueError, match="revenue"):
        build_forecast(asmp, profile)


# ── Integration tests ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,cik,name", [
    ("LULU", "0001397187", "lululemon"),
    ("F",    "0000037996", "Ford Motor"),
    ("VZ",   "0000732712", "Verizon"),
])
def test_integration_forecast_builds_without_error(ticker, cik, name):
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, ticker, facts, derived, max_years=5)
    profile = build_profile(stmts, name)
    asmp = build_assumptions_from_profile(profile)
    ps = build_forecast(asmp, profile)

    assert ps.ticker == ticker
    assert len(ps.base.years) == 5
    assert ps.base_year == max(profile.years)
    assert ps.base_revenue > 0

    for scenario in (ps.bear, ps.base, ps.bull):
        for fy in scenario.years:
            # EBITDA identity
            assert abs(fy.ebitda - (fy.ebit + fy.da)) < 1e-6
            # FCFF identity
            nopat = fy.ebit * (1 - asmp.effective_tax_rate)
            expected_fcff = nopat + fy.da - fy.capex - fy.delta_nwc
            assert abs(fy.fcff - expected_fcff) < 1e-6


def test_integration_lulu_gross_margin_projected():
    """LULU has gross_profit data → gross_profit should be present in forecast."""
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived)
    profile = build_profile(stmts, "lululemon")
    asmp = build_assumptions_from_profile(profile)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.gross_profit is not None


def test_integration_ford_gross_margin_none():
    """Ford (no gross_profit data) → gross_profit is None in forecast."""
    cik = "0000037996"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "F", facts, derived)
    profile = build_profile(stmts, "Ford Motor")
    asmp = build_assumptions_from_profile(profile)
    ps = build_forecast(asmp, profile)
    for fy in ps.base.years:
        assert fy.gross_profit is None


def test_integration_bear_base_bull_revenue_ordered():
    """bear < base < bull revenue for every year, all three real companies."""
    for ticker, cik, name in [
        ("LULU", "0001397187", "lululemon"),
        ("F",    "0000037996", "Ford Motor"),
        ("VZ",   "0000732712", "Verizon"),
    ]:
        with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
            payload = json.load(f)
        facts = extract_metrics(cik, payload)
        derived = compute_derived_metrics(cik, facts)
        stmts = build_statements(cik, ticker, facts, derived)
        profile = build_profile(stmts, name)
        asmp = build_assumptions_from_profile(profile)
        ps = build_forecast(asmp, profile)
        for b, m, u in zip(ps.bear.years, ps.base.years, ps.bull.years):
            assert b.revenue < m.revenue < u.revenue, (
                f"{ticker}: bear={b.revenue} base={m.revenue} bull={u.revenue}"
            )


def test_integration_quality_issues_propagated():
    """quality_issues from assumptions must appear in ProjectedStatements."""
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived)
    profile = build_profile(stmts, "lululemon")
    asmp = build_assumptions_from_profile(profile)
    ps = build_forecast(asmp, profile)
    assert isinstance(ps.quality_issues, tuple)
    assert len(ps.quality_issues) == len(asmp.quality_issues)
