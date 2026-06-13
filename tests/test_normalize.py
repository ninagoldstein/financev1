"""Tests for secpull/normalize.py — normalization and outlier detection."""
import json
import math

import pytest

from secpull import config
from secpull.assumptions import build_assumptions_from_profile
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.normalize import (
    OUTLIER_SIGMA,
    AssumptionDetail,
    _mean,
    _sample_std,
    _window_mean,
    clamp_detail,
    normalize_series,
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
