"""Tests for secpull/profile.py."""
import json
import pytest

from secpull import config
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.models import FinancialFact, METRIC_TAGS
from secpull.profile import Ratio, CompanyProfile, build_profile
from secpull.quality import COMPLETE, PARTIAL, DERIVED, STALE, QualityIssue
from secpull.statements import build_statements, StatementLine

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


def _make_stmts(facts, derived=None, ticker=_TICKER):
    from secpull.models import DerivedFact
    return build_statements(_CIK, ticker, facts, derived or [], max_years=5)


def _minimal_facts(fy_range=(2021, 2022, 2023, 2024, 2025)):
    """Revenue + operating_income + D&A for each year; enough for basic ratios."""
    facts = []
    for fy in fy_range:
        rev = 10e9 * (1 + 0.08) ** (fy - 2021)
        facts += [
            _f("revenue", fy, rev),
            _f("operating_income", fy, rev * 0.20),
            _f("depreciation_amortization", fy, rev * 0.04),
            _f("net_income", fy, rev * 0.14),
            _f("income_tax_expense", fy, rev * 0.05),
            _f("total_current_assets", fy, rev * 0.45),
            _f("total_current_liabilities", fy, rev * 0.28),
            _f("total_assets", fy, rev * 1.2),
            _f("total_equity", fy, rev * 0.4),
            _f("cfo", fy, rev * 0.18),
            _f("capex", fy, rev * 0.06),
            _f("long_term_debt", fy, rev * 0.5),
            _f("cash", fy, rev * 0.1),
        ]
    return facts


# ── Ratio dataclass ───────────────────────────────────────────────────────────

def test_ratio_value_and_note():
    r = Ratio(value=0.25, note="derived from PARTIAL data")
    assert r.value == 0.25
    assert r.note is not None


def test_ratio_none_value():
    r = Ratio(value=None)
    assert r.value is None


# ── Revenue growth ────────────────────────────────────────────────────────────

def test_avg_revenue_growth_single_year():
    """Single year of revenue → growth undefined → None."""
    facts = [_f("revenue", 2025, 10e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert profile.avg_revenue_growth.value is None


def test_avg_revenue_growth_two_years():
    facts = [_f("revenue", 2024, 10e9), _f("revenue", 2025, 11e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_revenue_growth.value - 0.10) < 1e-9


def test_avg_revenue_growth_five_years():
    """8% growth for 5 consecutive years → avg ~8%."""
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_revenue_growth.value - 0.08) < 0.001


def test_revenue_cagr_consistent_with_growth():
    facts = [_f("revenue", 2024, 10e9), _f("revenue", 2025, 11e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    # CAGR over 1 period = single YoY growth
    assert abs(profile.revenue_cagr.value - 0.10) < 1e-9


# ── Margin averages ───────────────────────────────────────────────────────────

def test_avg_ebit_margin_computed():
    facts = [
        _f("revenue", 2025, 10e9),
        _f("operating_income", 2025, 2e9),
    ]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_ebit_margin.value - 0.20) < 1e-9


def test_avg_gross_margin_none_when_no_gross_profit():
    """When gross_profit is a structural gap, avg_gross_margin should be None."""
    facts = [
        _f("revenue", 2025, 10e9),
        _f("operating_income", 2025, 2e9),
    ]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert profile.avg_gross_margin.value is None


def test_avg_gross_margin_with_data():
    facts = [
        _f("revenue", 2025, 10e9),
        _f("gross_profit", 2025, 6e9),
    ]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_gross_margin.value - 0.60) < 1e-9


def test_avg_net_margin_five_year_average():
    """14% net margin for 5 years → avg 14%."""
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_net_margin.value - 0.14) < 0.001


def test_avg_ebitda_margin_uses_derived_ebitda():
    """EBITDA line in statements comes from DerivedFact; margin should still compute."""
    from secpull.models import DerivedFact
    facts = [_f("revenue", 2025, 10e9)]
    d = DerivedFact(
        cik=_CIK, metric="ebitda", source="derived",
        formula_used="operating_income + depreciation_amortization",
        source_metrics_used="operating_income,depreciation_amortization",
        value=2.4e9, unit="USD",
        fiscal_year=2025, fiscal_period="FY",
        form="10-K", end_date="2026-01-31",
        coverage_flag="complete",
    )
    stmts = _make_stmts(facts, [d])
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_ebitda_margin.value - 0.24) < 1e-9


# ── Effective tax rate ────────────────────────────────────────────────────────

def test_effective_tax_rate_with_interest():
    """Tax rate = income_tax / (ebit - interest_expense)."""
    facts = [
        _f("operating_income", 2025, 2e9),
        _f("interest_expense", 2025, 0.5e9),
        _f("income_tax_expense", 2025, 0.375e9),  # 0.375 / 1.5 = 25%
    ]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_effective_tax_rate.value - 0.25) < 1e-9


def test_effective_tax_rate_without_interest_uses_ebit():
    """When interest_expense absent, ebit is used as EBT proxy."""
    facts = [
        _f("operating_income", 2025, 2e9),
        _f("income_tax_expense", 2025, 0.5e9),   # 0.5 / 2.0 = 25%
    ]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_effective_tax_rate.value - 0.25) < 1e-9


def test_effective_tax_rate_none_when_no_data():
    facts = [_f("revenue", 2025, 10e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert profile.avg_effective_tax_rate.value is None


# ── Cash flow drivers ─────────────────────────────────────────────────────────

def test_avg_da_pct_revenue():
    facts = [_f("revenue", 2025, 10e9), _f("depreciation_amortization", 2025, 0.5e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_da_pct_revenue.value - 0.05) < 1e-9


def test_avg_capex_pct_revenue():
    facts = [_f("revenue", 2025, 10e9), _f("capex", 2025, 0.6e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert abs(profile.avg_capex_pct_revenue.value - 0.06) < 1e-9


def test_avg_nwc_pct_revenue_computed():
    """NWC = AR + INV + prepaid - AP - accruals."""
    facts = [
        _f("revenue", 2025, 10e9),
        _f("accounts_receivable", 2025, 1e9),
        _f("inventory", 2025, 0.5e9),
        _f("prepaid_other_current", 2025, 0.2e9),
        _f("accounts_payable", 2025, 0.8e9),
        _f("accrued_liabilities", 2025, 0.3e9),
    ]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    # NWC = (1 + 0.5 + 0.2) - (0.8 + 0.3) = 1.7 - 1.1 = 0.6; pct = 0.6/10 = 6%
    assert abs(profile.avg_nwc_pct_revenue.value - 0.06) < 1e-9


def test_avg_nwc_pct_none_when_components_missing():
    facts = [_f("revenue", 2025, 10e9)]   # no BS items
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert profile.avg_nwc_pct_revenue.value is None


# ── Coverage statistics ───────────────────────────────────────────────────────

def test_coverage_counts_complete_metrics():
    facts = [_f("revenue", 2025, 10e9), _f("net_income", 2025, 1.4e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert profile.n_complete >= 2


def test_raw_coverage_pct_is_fraction_of_46():
    """raw_coverage_pct = n_populated / 46 × 100."""
    facts = [_f("revenue", 2025, 10e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    total = len(METRIC_TAGS)
    expected_raw = profile.n_complete / total * 100
    # Allow for derived/partial/unreliable in numerator
    n_pop = profile.n_complete + profile.n_partial + profile.n_derived + profile.n_unreliable
    assert abs(profile.raw_coverage_pct - n_pop / total * 100) < 0.01


def test_adj_coverage_pct_excludes_gaps_and_stale():
    """adj denominator = 46 - structural_gaps - stale."""
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    total = len(METRIC_TAGS)
    denom = total - profile.n_structural_gap - profile.n_stale
    n_pop = profile.n_complete + profile.n_partial + profile.n_derived + profile.n_unreliable
    assert abs(profile.adj_coverage_pct - n_pop / denom * 100) < 0.01


def test_coverage_note_appears_in_quality_notes():
    """quality_notes[0] must contain raw and adjusted coverage figures."""
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert any("Coverage:" in n for n in profile.quality_notes)
    coverage_note = next(n for n in profile.quality_notes if "Coverage:" in n)
    assert "raw" in coverage_note.lower() or "/" in coverage_note
    assert "adjusted" in coverage_note.lower() or "adj" in coverage_note.lower()


def test_stale_note_appears_when_stale_metrics_present():
    facts = [_f("interest_expense", 2023, 5e9)]   # FY2023 < STALE_THRESHOLD
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert profile.n_stale >= 1
    assert any("STALE" in n for n in profile.quality_notes)


def test_unreliable_note_for_ford():
    """Ford profile must emit ERROR QualityIssue for UNRELIABLE metrics."""
    facts = [
        _f("revenue", 2025, 187e9, quality=COMPLETE),
        _f("long_term_debt", 2020, 291e6, quality=COMPLETE),
    ]
    stmts = build_statements("0000037996", "F", facts, [], max_years=5)
    profile = build_profile(stmts, "Ford Motor Company")
    errors = [qi for qi in profile.quality_issues if qi.severity == "ERROR"]
    assert any(qi.metric == "long_term_debt" for qi in errors)
    # quality_notes must mirror quality_issues content
    assert any("long_term_debt" in n for n in profile.quality_notes)


# ── QualityIssue structure ────────────────────────────────────────────────────

def test_quality_issues_field_exists():
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert hasattr(profile, "quality_issues")
    assert isinstance(profile.quality_issues, list)
    assert all(isinstance(qi, QualityIssue) for qi in profile.quality_issues)


def test_quality_issues_first_is_coverage_summary():
    """First QualityIssue must be the __coverage__ summary at INFO severity."""
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    first = profile.quality_issues[0]
    assert first.metric == "__coverage__"
    assert first.severity == "INFO"
    assert "Coverage:" in first.message


def test_quality_issues_stale_emits_warning():
    """Metrics older than FY2024 must produce WARNING QualityIssue."""
    facts = [_f("interest_expense", 2023, 5e9)]
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    warnings = [qi for qi in profile.quality_issues if qi.severity == "WARNING"]
    assert any(qi.metric == "interest_expense" for qi in warnings)
    stale_issue = next(qi for qi in warnings if qi.metric == "interest_expense")
    assert "STALE" in stale_issue.message
    assert "FY2023" in stale_issue.message


def test_quality_issues_absent_emits_warning():
    """Metrics with no extracted data must produce WARNING QualityIssue."""
    facts = [_f("revenue", 2025, 10e9)]   # only revenue; everything else absent
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    warnings = [qi for qi in profile.quality_issues if qi.severity == "WARNING"]
    # net_income is absent in this minimal fact set
    assert any(qi.metric == "net_income" for qi in warnings)
    absent_issue = next(qi for qi in warnings if qi.metric == "net_income")
    assert "ABSENT" in absent_issue.message


def test_quality_issues_unreliable_emits_error():
    """UNRELIABLE metrics must produce ERROR QualityIssue."""
    facts = [_f("long_term_debt", 2025, 291e6, quality=COMPLETE)]
    stmts = build_statements("0000037996", "F", facts, [], max_years=5)
    profile = build_profile(stmts, "Ford Motor Company")
    errors = [qi for qi in profile.quality_issues if qi.severity == "ERROR"]
    assert len(errors) >= 1
    ltd_error = next((qi for qi in errors if qi.metric == "long_term_debt"), None)
    assert ltd_error is not None, "long_term_debt must emit an ERROR QualityIssue"
    assert "UNRELIABLE" in ltd_error.message


def test_quality_issues_structural_gaps_excluded():
    """Structural gaps should NOT produce a QualityIssue — they are expected absences."""
    facts = [_f("revenue", 2025, 10e9)]
    stmts = build_statements("0001397187", "LULU", facts, [], max_years=5)
    profile = build_profile(stmts, "lululemon")
    # LULU structural gaps: interest_expense, long_term_debt, etc.
    # None of these should appear in quality_issues
    gap_in_issues = [
        qi for qi in profile.quality_issues
        if qi.metric in {"interest_expense", "long_term_debt", "dividends_paid"}
    ]
    assert len(gap_in_issues) == 0


def test_quality_notes_derived_from_quality_issues():
    """quality_notes list must exactly mirror quality_issues messages."""
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    expected = [qi.message for qi in profile.quality_issues]
    assert profile.quality_notes == expected


def test_severity_ordering_errors_before_warnings():
    """ERROR issues must appear before WARNING issues in quality_issues."""
    facts = [
        _f("long_term_debt", 2025, 291e6, quality=COMPLETE),
        _f("interest_expense", 2023, 5e9),   # will be STALE → WARNING
    ]
    stmts = build_statements("0000037996", "F", facts, [], max_years=5)
    profile = build_profile(stmts, "Ford Motor Company")
    severities = [qi.severity for qi in profile.quality_issues[1:]]  # skip __coverage__
    error_idx = next((i for i, s in enumerate(severities) if s == "ERROR"), None)
    warning_idx = next((i for i, s in enumerate(severities) if s == "WARNING"), None)
    if error_idx is not None and warning_idx is not None:
        assert error_idx < warning_idx


# ── Canonical metric universe ─────────────────────────────────────────────────

def test_total_canonical_equals_metric_tags_length():
    """_TOTAL_CANONICAL must be derived from METRIC_TAGS, not hardcoded."""
    from secpull.profile import _TOTAL_CANONICAL
    assert _TOTAL_CANONICAL == len(METRIC_TAGS)


def test_derived_only_metrics_not_in_all_lines():
    """Derived-only metrics (ebitda, fcf) are NOT in METRIC_TAGS and must not appear in all_lines."""
    from secpull.models import DerivedFact
    facts = _minimal_facts()
    d = DerivedFact(
        cik=_CIK, metric="ebitda", source="derived",
        formula_used="operating_income + depreciation_amortization",
        source_metrics_used="operating_income,depreciation_amortization",
        value=3e9, unit="USD",
        fiscal_year=2025, fiscal_period="FY",
        form="10-K", end_date="2026-01-31",
        coverage_flag="complete",
    )
    stmts = build_statements(_CIK, _TICKER, facts, [d], max_years=5)
    # ebitda and fcf are TIER1_FORMULAS derived-only — NOT in METRIC_TAGS
    # all_lines must match METRIC_TAGS exactly; derived-only metrics cannot sneak in
    assert "ebitda" not in METRIC_TAGS     # design invariant
    assert "fcf" not in METRIC_TAGS        # design invariant
    assert "ebitda" not in stmts.all_lines
    assert "fcf" not in stmts.all_lines
    # all_lines is bounded by METRIC_TAGS
    assert set(stmts.all_lines.keys()) == set(METRIC_TAGS.keys())


def test_all_lines_count_equals_metric_tags():
    """all_lines must have exactly as many entries as METRIC_TAGS."""
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    assert len(stmts.all_lines) == len(METRIC_TAGS)


# ── years field ───────────────────────────────────────────────────────────────

def test_profile_years_match_statement_years():
    facts = _minimal_facts()
    stmts = _make_stmts(facts)
    profile = build_profile(stmts, "Test Co")
    assert profile.years == stmts.years


# ── Integration tests ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,cik,name", [
    ("LULU", "0001397187", "lululemon"),
    ("F",    "0000037996", "Ford Motor"),
    ("VZ",   "0000732712", "Verizon"),
])
def test_integration_profile_builds_without_error(ticker, cik, name):
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, ticker, facts, derived, max_years=5)
    profile = build_profile(stmts, name)

    assert profile.ticker == ticker
    assert profile.cik == cik
    assert len(profile.years) >= 1
    assert profile.raw_coverage_pct > 0
    assert profile.adj_coverage_pct >= profile.raw_coverage_pct
    assert len(profile.quality_notes) >= 1
    assert "Coverage:" in profile.quality_notes[0]


def test_integration_lulu_100pct_adjusted_coverage():
    """LULU has no absent metrics and all stale/gaps are excluded → 100% adjusted."""
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived)
    profile = build_profile(stmts, "lululemon")
    assert abs(profile.adj_coverage_pct - 100.0) < 0.1
    assert profile.n_absent == 0


def test_integration_vz_100pct_adjusted_coverage():
    cik = "0000732712"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "VZ", facts, derived)
    profile = build_profile(stmts, "Verizon")
    assert abs(profile.adj_coverage_pct - 100.0) < 0.1
    assert profile.n_absent == 0


def test_integration_ford_has_two_absent():
    """Ford is missing current_portion_ltd and short_term_debt → n_absent == 2."""
    cik = "0000037996"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "F", facts, derived)
    profile = build_profile(stmts, "Ford Motor")
    assert profile.n_absent == 2


def test_integration_lulu_ebit_margin_reasonable():
    """LULU's 5yr avg EBIT margin should be in [15%, 25%]."""
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived)
    profile = build_profile(stmts, "lululemon")
    assert profile.avg_ebit_margin.value is not None
    assert 0.15 <= profile.avg_ebit_margin.value <= 0.25


def test_integration_vz_capex_pct_revenue_reasonable():
    """VZ's 5yr avg capex/revenue should be in [10%, 20%]."""
    cik = "0000732712"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "VZ", facts, derived)
    profile = build_profile(stmts, "Verizon")
    assert profile.avg_capex_pct_revenue.value is not None
    assert 0.10 <= profile.avg_capex_pct_revenue.value <= 0.20
