"""Tests for secpull/normalize.py — normalization and outlier detection."""
import json
import math

import pytest

from secpull import config
from secpull.assumptions import build_assumptions_from_profile
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.normalize import (
    MODES,
    OUTLIER_SIGMA,
    AssumptionDetail,
    _mean,
    _sample_std,
    _window_mean,
    clamp_detail,
    normalize_series,
    select_from_detail,
)
from secpull.profile import build_profile
from secpull.statements import build_statements


# ── Math helpers ──────────────────────────────────────────────────────────────


def test_mean_basic():
    assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_sample_std_basic():
    # Sample std (Bessel n-1) of this set ≈ 2.138, not population std 2.0
    vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    import statistics
    assert _sample_std(vals) == pytest.approx(statistics.stdev(vals))


def test_sample_std_single_returns_zero():
    assert _sample_std([5.0]) == 0.0


def test_window_mean_all():
    vals = [0.10, 0.15, 0.20, 0.25, 0.30]
    years = [2021, 2022, 2023, 2024, 2025]
    assert _window_mean(vals, years, 5) == pytest.approx(0.20)


def test_window_mean_three_most_recent():
    vals = [0.10, 0.15, 0.20, 0.25, 0.30]
    years = [2021, 2022, 2023, 2024, 2025]
    # most recent 3: 2023, 2024, 2025 → [0.20, 0.25, 0.30]
    assert _window_mean(vals, years, 3) == pytest.approx(0.25)


def test_window_mean_fewer_than_n_returns_available():
    vals = [0.10, 0.20]
    years = [2024, 2025]
    # asking for 5 but only 2 exist
    assert _window_mean(vals, years, 5) == pytest.approx(0.15)


# ── normalize_series: empty / short series ────────────────────────────────────


def test_empty_series_returns_all_none():
    detail = normalize_series([], [], "test_metric")
    assert detail.historical_5y is None
    assert detail.historical_3y is None
    assert detail.normalized is None
    assert detail.selected_value is None
    assert detail.outlier_years == ()


def test_single_value_no_outlier_detection():
    detail = normalize_series([0.20], [2025], "test_metric")
    assert detail.most_recent == pytest.approx(0.20)
    assert detail.normalized == pytest.approx(0.20)
    assert detail.outlier_years == ()


def test_three_values_no_loo_detection():
    # LOO requires >= 4 observations; 3 values → no outlier flagging
    detail = normalize_series([0.10, 0.50, 0.15], [2023, 2024, 2025], "test")
    assert detail.outlier_years == ()
    assert detail.normalized == pytest.approx(_mean([0.10, 0.50, 0.15]))


# ── normalize_series: window averages ─────────────────────────────────────────


def test_five_year_window_equals_full_mean_when_five_values():
    vals  = [0.10, 0.12, 0.11, 0.13, 0.12]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.historical_5y == pytest.approx(_mean(vals))


def test_three_year_window_uses_three_most_recent():
    vals  = [0.05, 0.10, 0.20, 0.30, 0.40]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.historical_3y == pytest.approx(_mean([0.20, 0.30, 0.40]))


def test_two_year_window_uses_two_most_recent():
    vals  = [0.05, 0.10, 0.20, 0.30, 0.40]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.historical_2y == pytest.approx(_mean([0.30, 0.40]))


def test_most_recent_is_last_year_value():
    vals  = [0.10, 0.12, 0.15, 0.18, 0.22]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.most_recent == pytest.approx(0.22)


# ── normalize_series: no-outlier companies ────────────────────────────────────


def test_flat_series_no_outliers():
    vals  = [0.10, 0.11, 0.10, 0.11, 0.10]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.outlier_years == ()
    assert detail.normalized == pytest.approx(_mean(vals))


def test_gradual_trend_no_outliers():
    # Smoothly rising margins — no single year is extreme
    vals  = [0.20, 0.22, 0.24, 0.26, 0.28]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.outlier_years == ()
    assert "no outliers" in detail.rationale


def test_normalized_equals_full_mean_when_no_outliers():
    # Use truly flat values so no single point is an LOO outlier
    vals  = [0.30, 0.30, 0.30, 0.30, 0.30]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.outlier_years == ()
    assert detail.normalized == pytest.approx(detail.historical_5y)


# ── normalize_series: outlier detection ───────────────────────────────────────


def test_high_outlier_detected():
    # Last value is far above the rest
    vals  = [0.10, 0.11, 0.10, 0.12, 0.50]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert 2025 in detail.outlier_years


def test_low_outlier_detected():
    # One value is far below the rest
    vals  = [0.40, 0.41, 0.05, 0.42, 0.40]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert 2023 in detail.outlier_years


def test_normalized_excludes_outlier():
    vals  = [0.10, 0.11, 0.10, 0.12, 0.50]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    expected = _mean([0.10, 0.11, 0.10, 0.12])   # 0.50 excluded
    assert detail.normalized == pytest.approx(expected, rel=1e-6)


def test_outlier_note_contains_year_and_direction():
    vals  = [0.10, 0.11, 0.10, 0.12, 0.50]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "capex_pct_revenue")
    assert detail.outlier_notes
    note = detail.outlier_notes[0]
    assert "FY2025" in note
    assert "above" in note
    assert "capex_pct_revenue" in note


def test_rationale_mentions_excluded_year_and_raw_average():
    vals  = [0.10, 0.11, 0.10, 0.12, 0.50]
    years = [2021, 2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric", fmt=".1%")
    assert "FY2025" in detail.rationale
    # Raw average should appear in rationale
    raw = _mean(vals)
    assert f"{raw:.1%}" in detail.rationale


def test_safety_no_values_excluded_when_all_outliers():
    # Pathological: every value could be an outlier — must keep at least one
    vals  = [1.0, -1.0, 1.0, -1.0]
    years = [2022, 2023, 2024, 2025]
    detail = normalize_series(vals, years, "metric")
    assert detail.normalized is not None
    assert not math.isnan(detail.normalized)


# ── META capex spike (FY2025) ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def meta_profile():
    CIK = "0001326801"
    with open(config.DATA_DIR / "raw" / f"{CIK}.json") as f:
        payload = json.load(f)
    facts   = extract_metrics(CIK, payload)
    derived = compute_derived_metrics(CIK, facts)
    stmts   = build_statements(CIK, "META", facts, derived, max_years=5)
    return build_profile(stmts, "Meta Platforms")


@pytest.fixture(scope="module")
def meta_assumptions(meta_profile):
    return build_assumptions_from_profile(meta_profile)


def test_meta_capex_fy2025_flagged_as_outlier(meta_assumptions):
    """FY2025 META capex (34.7% of revenue) must be flagged by LOO detection."""
    detail = meta_assumptions.capex_pct_detail
    assert detail is not None
    assert 2025 in detail.outlier_years


def test_meta_capex_normalized_below_raw_5yr(meta_assumptions):
    """Normalized capex must be lower than raw 5yr avg because FY2025 spike is excluded."""
    detail = meta_assumptions.capex_pct_detail
    assert detail.normalized < detail.historical_5y


def test_meta_capex_normalized_near_pre_ai_levels(meta_assumptions):
    """Normalized capex should be in the 18-23% range (pre-AI buildout levels)."""
    detail = meta_assumptions.capex_pct_detail
    assert 0.18 <= detail.normalized <= 0.23, (
        f"Expected 18-23%, got {detail.normalized:.1%}"
    )


def test_meta_capex_outlier_note_references_fy2025(meta_assumptions):
    detail = meta_assumptions.capex_pct_detail
    notes  = " ".join(detail.outlier_notes)
    assert "FY2025" in notes
    assert "above" in notes


def test_meta_capex_selected_value_is_normalized(meta_assumptions):
    """selected_value must equal the normalized (outlier-excluded) average."""
    detail = meta_assumptions.capex_pct_detail
    assert detail.selected_value == pytest.approx(detail.normalized)


# ── META FY2022 margin collapse ───────────────────────────────────────────────


def test_meta_ebit_margin_fy2022_flagged(meta_assumptions):
    """FY2022 EBIT margin (24.8%) must be flagged — Year of Efficiency trough."""
    detail = meta_assumptions.ebit_margin_detail
    assert detail is not None
    assert 2022 in detail.outlier_years


def test_meta_ebit_margin_normalized_above_raw_5yr(meta_assumptions):
    """Excluding the FY2022 trough raises the margin above the raw 5yr average."""
    detail = meta_assumptions.ebit_margin_detail
    assert detail.normalized > detail.historical_5y


def test_meta_ebit_margin_normalized_near_recent_run_rate(meta_assumptions):
    """Normalized margin should be close to FY2023-25 average (~39-40%)."""
    detail = meta_assumptions.ebit_margin_detail
    assert 0.37 <= detail.normalized <= 0.42, (
        f"Expected 37-42%, got {detail.normalized:.1%}"
    )


def test_meta_ebit_margin_3yr_consistent_with_normalized(meta_assumptions):
    """3yr average (FY2023-25) should be close to normalized since FY2022 excluded."""
    detail = meta_assumptions.ebit_margin_detail
    assert abs(detail.historical_3y - detail.normalized) < 0.03


# ── META FY2022 revenue growth collapse ──────────────────────────────────────


def test_meta_revenue_growth_fy2022_flagged(meta_assumptions):
    """FY2022 revenue growth (−1.1%) must be flagged as an outlier."""
    detail = meta_assumptions.revenue_growth_detail
    assert detail is not None
    assert 2022 in detail.outlier_years


def test_meta_revenue_growth_normalized_above_raw(meta_assumptions):
    detail = meta_assumptions.revenue_growth_detail
    assert detail.normalized > detail.historical_5y


def test_meta_revenue_growth_normalized_reflects_recovery(meta_assumptions):
    """Normalized growth should be in ~18-22% range (FY2023-25 actual growth)."""
    detail = meta_assumptions.revenue_growth_detail
    assert 0.17 <= detail.normalized <= 0.23, (
        f"Expected 17-23%, got {detail.normalized:.1%}"
    )


# ── Window averages on META ───────────────────────────────────────────────────


def test_meta_ebit_5yr_vs_3yr_differ_due_to_fy2022(meta_assumptions):
    """3yr average (FY2023-25) should be meaningfully higher than 5yr (FY2021-25)."""
    detail = meta_assumptions.ebit_margin_detail
    assert detail.historical_3y > detail.historical_5y + 0.02


def test_meta_all_detail_fields_present(meta_assumptions):
    for attr in ("revenue_growth_detail", "ebit_margin_detail", "da_pct_detail",
                 "capex_pct_detail", "tax_rate_detail"):
        detail = getattr(meta_assumptions, attr)
        assert detail is not None, f"{attr} should not be None"
        assert isinstance(detail, AssumptionDetail)


# ── clamp_detail ──────────────────────────────────────────────────────────────


def test_clamp_detail_no_change_within_bounds():
    detail = normalize_series([0.20], [2025], "metric")
    clamped = clamp_detail(detail, 0.0, 0.30)
    assert clamped.selected_value == pytest.approx(detail.selected_value)
    assert clamped.rationale == detail.rationale


def test_clamp_detail_upper_bound():
    detail = normalize_series([0.45], [2025], "metric", fmt=".1%")
    clamped = clamp_detail(detail, 0.0, 0.30)
    assert clamped.selected_value == pytest.approx(0.30)
    assert "upper bound" in clamped.rationale
    assert "30.0%" in clamped.rationale


def test_clamp_detail_lower_bound():
    detail = normalize_series([-0.10], [2025], "metric", fmt=".1%")
    clamped = clamp_detail(detail, 0.0, 0.30)
    assert clamped.selected_value == pytest.approx(0.0)
    assert "lower bound" in clamped.rationale


def test_clamp_detail_none_selected_value_passthrough():
    detail = normalize_series([], [], "metric")
    clamped = clamp_detail(detail, 0.0, 0.30)
    assert clamped.selected_value is None


# ── AssumptionDetail used by DCF (selected_value drives model) ────────────────


def test_meta_capex_selected_value_drives_model_capex(meta_assumptions):
    """The float field capex_pct_revenue must equal the detail's selected_value."""
    a = meta_assumptions
    assert a.capex_pct_revenue == pytest.approx(
        a.capex_pct_detail.selected_value, abs=1e-9
    )


def test_meta_ebit_margin_selected_value_drives_model(meta_assumptions):
    a = meta_assumptions
    assert a.ebit_margin == pytest.approx(
        a.ebit_margin_detail.selected_value, abs=1e-9
    )


def test_meta_base_revenue_growth_matches_detail(meta_assumptions):
    a = meta_assumptions
    assert a.base_revenue_growth == pytest.approx(
        a.revenue_growth_detail.selected_value, abs=1e-9
    )


# ── select_from_detail: all six modes ─────────────────────────────────────────

# Shared fixture for mode tests: 5-value series with one high outlier (FY2025)
_MODE_VALS  = [0.10, 0.11, 0.10, 0.12, 0.50]
_MODE_YEARS = [2021, 2022, 2023, 2024, 2025]


@pytest.fixture(scope="module")
def _base_detail():
    return normalize_series(_MODE_VALS, _MODE_YEARS, "metric")


def test_select_normalized_preserves_selected_value(_base_detail):
    result = select_from_detail(_base_detail, "normalized")
    assert result.selected_value == pytest.approx(_base_detail.normalized)
    assert result.mode == "normalized"


def test_select_normalized_preserves_rationale(_base_detail):
    result = select_from_detail(_base_detail, "normalized")
    assert result.rationale == _base_detail.rationale


def test_select_raw_5y(_base_detail):
    result = select_from_detail(_base_detail, "raw_5y")
    assert result.selected_value == pytest.approx(_base_detail.historical_5y)
    assert result.mode == "raw_5y"
    assert "Raw 5-year average" in result.rationale


def test_select_recent_3y(_base_detail):
    result = select_from_detail(_base_detail, "recent_3y")
    assert result.selected_value == pytest.approx(_base_detail.historical_3y)
    assert result.mode == "recent_3y"
    assert "3-year average" in result.rationale


def test_select_recent_2y(_base_detail):
    result = select_from_detail(_base_detail, "recent_2y")
    assert result.selected_value == pytest.approx(_base_detail.historical_2y)
    assert result.mode == "recent_2y"
    assert "2-year average" in result.rationale


def test_select_most_recent(_base_detail):
    result = select_from_detail(_base_detail, "most_recent")
    assert result.selected_value == pytest.approx(_base_detail.most_recent)
    assert result.mode == "most_recent"
    assert "most-recent year" in result.rationale


def test_select_manual_override_sets_selected_value(_base_detail):
    result = select_from_detail(_base_detail, "manual_override", manual_value=0.155)
    assert result.selected_value == pytest.approx(0.155)
    assert result.mode == "manual_override"
    assert result.manual_value == pytest.approx(0.155)


def test_select_manual_override_rationale_says_analyst_override(_base_detail):
    result = select_from_detail(_base_detail, "manual_override", manual_value=0.155)
    assert "Manual analyst override" in result.rationale
    assert "15.5%" in result.rationale


def test_select_manual_override_rationale_mentions_normalized(_base_detail):
    result = select_from_detail(_base_detail, "manual_override", manual_value=0.155)
    # Rationale must state what the statistical normalized average was
    assert "Statistical normalized average" in result.rationale
    norm_str = f"{_base_detail.normalized:.1%}"
    assert norm_str in result.rationale


def test_select_manual_override_rationale_mentions_outlier_years(_base_detail):
    # FY2025 is flagged; rationale must reference it so the audit trail is clear
    result = select_from_detail(_base_detail, "manual_override", manual_value=0.155)
    assert "FY2025" in result.rationale


def test_select_manual_override_without_value_raises(_base_detail):
    with pytest.raises(ValueError, match="manual_value"):
        select_from_detail(_base_detail, "manual_override")


def test_select_invalid_mode_raises(_base_detail):
    with pytest.raises(ValueError, match="mode must be one of"):
        select_from_detail(_base_detail, "bogus_mode")


def test_select_does_not_mutate_original(_base_detail):
    original_sv = _base_detail.selected_value
    select_from_detail(_base_detail, "raw_5y")
    assert _base_detail.selected_value == pytest.approx(original_sv)


def test_modes_constant_contains_all_expected_modes():
    expected = {"normalized", "raw_5y", "recent_3y", "recent_2y", "most_recent", "manual_override"}
    assert set(MODES) == expected


# ── META capex: normalized remains ~21.3%, manual override to 15.5% ──────────


def test_meta_capex_normalized_approximately_21_3_pct(meta_assumptions):
    """Normalized capex (FY2021-2024, FY2025 excluded) should be ~21.3%."""
    detail = meta_assumptions.capex_pct_detail
    assert detail.normalized == pytest.approx(0.213, abs=0.005), (
        f"Expected ~21.3%, got {detail.normalized:.1%}"
    )


def test_meta_capex_manual_override_changes_selected_value(meta_profile):
    """build_assumptions_from_profile with manual_override should use 15.5%."""
    asmp = build_assumptions_from_profile(
        meta_profile,
        modes={"capex_pct_revenue": ("manual_override", 0.155)},
    )
    assert asmp.capex_pct_revenue == pytest.approx(0.155, abs=1e-9)
    assert asmp.capex_pct_detail.selected_value == pytest.approx(0.155, abs=1e-9)
    assert asmp.capex_pct_detail.mode == "manual_override"


def test_meta_capex_manual_override_rationale_distinguishes_from_statistical(meta_profile):
    """Manual override rationale must clearly identify analyst judgment vs. LOO outlier."""
    asmp = build_assumptions_from_profile(
        meta_profile,
        modes={"capex_pct_revenue": ("manual_override", 0.155)},
    )
    rationale = asmp.capex_pct_detail.rationale
    assert "Manual analyst override" in rationale
    assert "Statistical normalized average" in rationale
    assert "FY2025" in rationale  # outlier year must appear in the audit trail


def test_meta_capex_normalized_default_mode_still_21_3(meta_profile):
    """Explicitly passing normalized mode must preserve the same result as default."""
    asmp_default = build_assumptions_from_profile(meta_profile)
    asmp_explicit = build_assumptions_from_profile(
        meta_profile,
        modes={"capex_pct_revenue": ("normalized", None)},
    )
    assert asmp_explicit.capex_pct_revenue == pytest.approx(
        asmp_default.capex_pct_revenue, abs=1e-9
    )


def test_meta_capex_raw_5y_mode_includes_fy2025(meta_profile):
    """raw_5y includes FY2025 spike so selected_value > normalized."""
    asmp = build_assumptions_from_profile(
        meta_profile,
        modes={"capex_pct_revenue": ("raw_5y", None)},
    )
    detail = asmp.capex_pct_detail
    # raw_5y includes FY2025 so selected_value should equal historical_5y
    assert asmp.capex_pct_revenue == pytest.approx(detail.historical_5y, abs=1e-9)
    # and it should be higher than the normalized value
    assert asmp.capex_pct_revenue > detail.normalized
