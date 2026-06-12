"""Tests for secpull/assumptions.py."""
import json
import pytest

from secpull import config
from secpull.assumptions import ForecastAssumptions, build_assumptions_from_profile
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.models import FinancialFact
from secpull.profile import CompanyProfile, Ratio, build_profile
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


def _make_profile(facts, ticker=_TICKER, name="Test Co"):
    stmts = build_statements(_CIK, ticker, facts, [], max_years=5)
    return build_profile(stmts, name)


def _full_facts(fy_range=(2021, 2022, 2023, 2024, 2025)):
    """Minimal facts supplying every assumption input."""
    facts = []
    for fy in fy_range:
        rev = 10e9 * (1.08 ** (fy - 2021))
        facts += [
            _f("revenue", fy, rev),
            _f("gross_profit", fy, rev * 0.55),
            _f("operating_income", fy, rev * 0.20),
            _f("depreciation_amortization", fy, rev * 0.04),
            _f("net_income", fy, rev * 0.14),
            _f("income_tax_expense", fy, rev * 0.05),
            _f("interest_expense", fy, rev * 0.01),
            _f("capex", fy, rev * 0.06),
            _f("accounts_receivable", fy, rev * 0.10),
            _f("inventory", fy, rev * 0.05),
            _f("prepaid_other_current", fy, rev * 0.02),
            _f("accounts_payable", fy, rev * 0.08),
            _f("accrued_liabilities", fy, rev * 0.03),
        ]
    return facts


# ── Dataclass shape ───────────────────────────────────────────────────────────

def test_forecast_assumptions_is_frozen():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    with pytest.raises((AttributeError, TypeError)):
        fa.base_revenue_growth = 0.99   # type: ignore[misc]


def test_all_fields_present():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    for attr in (
        "base_revenue_growth", "bear_revenue_growth", "bull_revenue_growth",
        "gross_margin", "ebit_margin", "da_pct_revenue", "effective_tax_rate",
        "capex_pct_revenue", "nwc_pct_revenue", "interest_rate_on_debt",
        "n_projection_years", "quality_issues",
    ):
        assert hasattr(fa, attr), f"Missing field: {attr}"


# ── Defaults from profile ─────────────────────────────────────────────────────

def test_base_revenue_growth_uses_avg_yoy():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert profile.avg_revenue_growth.value is not None
    assert abs(fa.base_revenue_growth - profile.avg_revenue_growth.value) < 1e-9


def test_bear_bull_spread_is_three_pct():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.bull_revenue_growth - fa.base_revenue_growth - 0.03) < 1e-9
    assert abs(fa.base_revenue_growth - fa.bear_revenue_growth - 0.03) < 1e-9


def test_bear_less_than_base_less_than_bull():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert fa.bear_revenue_growth < fa.base_revenue_growth < fa.bull_revenue_growth


def test_gross_margin_from_profile():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert fa.gross_margin is not None
    assert abs(fa.gross_margin - profile.avg_gross_margin.value) < 1e-6


def test_ebit_margin_from_profile():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.ebit_margin - profile.avg_ebit_margin.value) < 1e-6


def test_da_pct_from_profile():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.da_pct_revenue - profile.avg_da_pct_revenue.value) < 1e-6


def test_effective_tax_rate_from_profile():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert fa.effective_tax_rate is not None
    assert abs(fa.effective_tax_rate - profile.avg_effective_tax_rate.value) < 1e-6


def test_capex_pct_from_profile():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.capex_pct_revenue - profile.avg_capex_pct_revenue.value) < 1e-6


def test_nwc_pct_from_profile():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.nwc_pct_revenue - profile.avg_nwc_pct_revenue.value) < 1e-6


def test_n_projection_years_default_five():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert fa.n_projection_years == 5


def test_interest_rate_on_debt_default_none():
    """interest_rate_on_debt is None unless supplied via override."""
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert fa.interest_rate_on_debt is None


# ── Fallback values ───────────────────────────────────────────────────────────

def test_base_growth_falls_back_to_cagr_when_single_year():
    """Single revenue year → avg_revenue_growth is None → use CAGR."""
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    assert profile.avg_revenue_growth.value is None
    # With single year, CAGR is also None → fallback to 0.03
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.base_revenue_growth - 0.03) < 1e-9


def test_base_growth_falls_back_to_cagr_when_avg_absent():
    """Two revenue years → avg is a single YoY rate; CAGR is the same value."""
    facts = [_f("revenue", 2024, 10e9), _f("revenue", 2025, 11e9)]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.base_revenue_growth - 0.10) < 1e-9


def test_ebit_margin_fallback_when_no_operating_income():
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    assert profile.avg_ebit_margin.value is None
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.ebit_margin - 0.10) < 1e-9


def test_effective_tax_rate_fallback_when_no_data():
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    assert profile.avg_effective_tax_rate.value is None
    fa = build_assumptions_from_profile(profile)
    assert abs(fa.effective_tax_rate - 0.25) < 1e-9


def test_da_pct_fallback_zero_when_absent():
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.da_pct_revenue == 0.0


def test_capex_pct_fallback_zero_when_absent():
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.capex_pct_revenue == 0.0


def test_nwc_pct_fallback_zero_when_absent():
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.nwc_pct_revenue == 0.0


# ── gross_margin None behaviour ───────────────────────────────────────────────

def test_gross_margin_none_when_no_gross_profit():
    """When no gross_profit data, gross_margin stays None (not replaced by fallback)."""
    facts = [_f("revenue", 2025, 10e9), _f("operating_income", 2025, 2e9)]
    profile = _make_profile(facts)
    assert profile.avg_gross_margin.value is None
    fa = build_assumptions_from_profile(profile)
    assert fa.gross_margin is None


def test_gross_margin_none_survives_override_not_present():
    """If override does not include gross_margin, None propagates through."""
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile, overrides={"ebit_margin": 0.15})
    assert fa.gross_margin is None


def test_gross_margin_override_sets_value_even_when_profile_is_none():
    facts = [_f("revenue", 2025, 10e9)]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile, overrides={"gross_margin": 0.60})
    assert abs(fa.gross_margin - 0.60) < 1e-9


# ── Override behaviour ────────────────────────────────────────────────────────

def test_override_base_revenue_growth():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile, overrides={"base_revenue_growth": 0.05})
    assert abs(fa.base_revenue_growth - 0.05) < 1e-9


def test_override_bear_and_bull_independently():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(
        profile,
        overrides={"bear_revenue_growth": 0.01, "bull_revenue_growth": 0.15},
    )
    assert abs(fa.bear_revenue_growth - 0.01) < 1e-9
    assert abs(fa.bull_revenue_growth - 0.15) < 1e-9


def test_override_ebit_margin():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile, overrides={"ebit_margin": 0.18})
    assert abs(fa.ebit_margin - 0.18) < 1e-9


def test_override_n_projection_years():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile, overrides={"n_projection_years": 10})
    assert fa.n_projection_years == 10


def test_override_interest_rate_on_debt():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(
        profile, overrides={"interest_rate_on_debt": 0.045}
    )
    assert abs(fa.interest_rate_on_debt - 0.045) < 1e-9


def test_unknown_override_keys_ignored():
    """Unrecognised keys in overrides dict must not raise."""
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(
        profile, overrides={"nonexistent_field": 99.9}
    )
    assert fa is not None


def test_none_overrides_treated_as_empty():
    profile = _make_profile(_full_facts())
    fa_no_ov = build_assumptions_from_profile(profile)
    fa_none  = build_assumptions_from_profile(profile, overrides=None)
    assert fa_no_ov.base_revenue_growth == fa_none.base_revenue_growth


# ── Clamping behaviour ────────────────────────────────────────────────────────

def test_tax_rate_clamped_at_35pct():
    """Profile tax rate above 35% → clamp to 0.35."""
    facts = [
        _f("operating_income", 2025, 1e9),
        _f("income_tax_expense", 2025, 0.5e9),  # 50% → clamp to 35%
    ]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.effective_tax_rate <= 0.35


def test_tax_rate_clamped_at_zero():
    """Negative effective tax rate (e.g. deferred benefit) → clamp to 0."""
    facts = [
        _f("operating_income", 2025, 1e9),
        _f("income_tax_expense", 2025, -0.1e9),  # negative
    ]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.effective_tax_rate >= 0.0


def test_capex_pct_clamped_at_30pct():
    """capex/revenue above 30% → clamp to 0.30."""
    facts = [
        _f("revenue", 2025, 10e9),
        _f("capex", 2025, 4e9),  # 40% → clamp to 30%
    ]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.capex_pct_revenue <= 0.30


def test_da_pct_clamped_at_25pct():
    """D&A/revenue above 25% → clamp to 0.25."""
    facts = [
        _f("revenue", 2025, 10e9),
        _f("depreciation_amortization", 2025, 3e9),  # 30% → clamp to 25%
    ]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.da_pct_revenue <= 0.25


def test_nwc_pct_clamped_at_40pct():
    """NWC/revenue above 40% → clamp to 0.40."""
    facts = [
        _f("revenue", 2025, 10e9),
        _f("accounts_receivable", 2025, 5e9),    # NWC will be large
        _f("inventory", 2025, 4e9),
        _f("prepaid_other_current", 2025, 1e9),
        _f("accounts_payable", 2025, 0.1e9),
        _f("accrued_liabilities", 2025, 0.1e9),
    ]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.nwc_pct_revenue <= 0.40


def test_nwc_pct_clamped_at_negative_20pct():
    """NWC/revenue below -20% (large negative WC) → clamp to -0.20."""
    facts = [
        _f("revenue", 2025, 10e9),
        _f("accounts_receivable", 2025, 0.1e9),
        _f("inventory", 2025, 0.1e9),
        _f("prepaid_other_current", 2025, 0.1e9),
        _f("accounts_payable", 2025, 5e9),        # large AP → negative NWC
        _f("accrued_liabilities", 2025, 4e9),
    ]
    profile = _make_profile(facts)
    fa = build_assumptions_from_profile(profile)
    assert fa.nwc_pct_revenue >= -0.20


def test_override_tax_rate_also_clamped():
    """Clamping applies even when value comes from an override."""
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile, overrides={"effective_tax_rate": 0.99})
    assert fa.effective_tax_rate <= 0.35


# ── Quality propagation ───────────────────────────────────────────────────────

def test_quality_issues_propagated_from_profile():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert isinstance(fa.quality_issues, tuple)
    assert len(fa.quality_issues) == len(profile.quality_issues)


def test_quality_issues_are_queueuality_issue_instances():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert all(isinstance(qi, QualityIssue) for qi in fa.quality_issues)


def test_quality_issues_immutable_tuple():
    """quality_issues must be a tuple (immutable) not a list."""
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    assert type(fa.quality_issues) is tuple


def test_quality_issues_match_profile_content():
    profile = _make_profile(_full_facts())
    fa = build_assumptions_from_profile(profile)
    for qi_p, qi_a in zip(profile.quality_issues, fa.quality_issues):
        assert qi_p.metric == qi_a.metric
        assert qi_p.severity == qi_a.severity
        assert qi_p.message == qi_a.message


# ── Integration tests ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,cik,name", [
    ("LULU", "0001397187", "lululemon"),
    ("F",    "0000037996", "Ford Motor"),
    ("VZ",   "0000732712", "Verizon"),
])
def test_integration_assumptions_build_without_error(ticker, cik, name):
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        import json
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, ticker, facts, derived, max_years=5)
    profile = build_profile(stmts, name)
    fa = build_assumptions_from_profile(profile)

    assert fa.bear_revenue_growth < fa.base_revenue_growth < fa.bull_revenue_growth
    assert 0.0 <= fa.effective_tax_rate <= 0.35
    assert 0.0 <= fa.capex_pct_revenue <= 0.30
    assert 0.0 <= fa.da_pct_revenue <= 0.25
    assert -0.20 <= fa.nwc_pct_revenue <= 0.40
    assert fa.n_projection_years == 5
    assert len(fa.quality_issues) == len(profile.quality_issues)


def test_integration_lulu_has_gross_margin():
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        import json
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived)
    profile = build_profile(stmts, "lululemon")
    fa = build_assumptions_from_profile(profile)
    assert fa.gross_margin is not None
    assert 0.50 <= fa.gross_margin <= 0.65


def test_integration_ford_gross_margin_is_none():
    """Ford has gross_profit as structural gap → gross_margin should be None."""
    cik = "0000037996"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        import json
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "F", facts, derived)
    profile = build_profile(stmts, "Ford Motor")
    fa = build_assumptions_from_profile(profile)
    assert fa.gross_margin is None


def test_integration_vz_ebit_margin_reasonable():
    cik = "0000732712"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        import json
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "VZ", facts, derived)
    profile = build_profile(stmts, "Verizon")
    fa = build_assumptions_from_profile(profile)
    assert 0.15 <= fa.ebit_margin <= 0.30


def test_integration_override_survives_integration_profile():
    """Overrides should work even for real extracted profiles."""
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        import json
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived)
    profile = build_profile(stmts, "lululemon")
    fa = build_assumptions_from_profile(
        profile,
        overrides={"base_revenue_growth": 0.10, "n_projection_years": 7},
    )
    assert abs(fa.base_revenue_growth - 0.10) < 1e-9
    assert fa.n_projection_years == 7
