"""
Forecast assumptions derived from a CompanyProfile.

ForecastAssumptions holds the single-point estimates used by the projection
layer.  All assumptions default to historical averages from CompanyProfile;
any field can be replaced via the ``overrides`` dict.

Clamping:
  Each assumption is bounded to a sensible range to prevent the forecast
  layer from receiving pathological inputs.  Ranges are chosen to cover
  normal corporate behaviour; a user override can still supply a value
  inside the clamped range, but the clamp applies to overrides too.

Quality propagation:
  profile.quality_issues is carried forward as quality_issues: tuple so
  downstream layers (forecast, DCF) can surface data concerns without
  re-examining the statements.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from secpull.normalize import AssumptionDetail, normalize_series, clamp_detail
from secpull.profile import CompanyProfile
from secpull.quality import QualityIssue

# ── Clamp bounds ──────────────────────────────────────────────────────────────

_CLAMP: dict[str, tuple[float, float]] = {
    "effective_tax_rate":  (0.00, 0.35),
    "capex_pct_revenue":   (0.00, 0.30),
    "da_pct_revenue":      (0.00, 0.25),
    "nwc_pct_revenue":     (-0.20, 0.40),
}

# Default fallbacks when the profile cannot compute a value
_FALLBACK_EBIT_MARGIN   = 0.10
_FALLBACK_TAX_RATE      = 0.25
_SPREAD                 = 0.03   # bear/bull distance from base


def _clamp(name: str, value: float) -> float:
    lo, hi = _CLAMP[name]
    return max(lo, min(hi, value))


# ── Public dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ForecastAssumptions:
    # Revenue growth scenarios (fractions; e.g. 0.08 = 8%)
    base_revenue_growth: float
    bear_revenue_growth: float
    bull_revenue_growth: float

    # Margin assumptions (fractions)
    gross_margin: float | None        # None when no gross profit data
    ebit_margin: float
    da_pct_revenue: float
    effective_tax_rate: float

    # Reinvestment assumptions (fractions of revenue)
    capex_pct_revenue: float
    nwc_pct_revenue: float

    # Debt cost — None when no clean long-term debt data
    interest_rate_on_debt: float | None

    # Projection horizon
    n_projection_years: int = 5

    # Quality carry-forward from profile
    quality_issues: tuple[QualityIssue, ...] = ()

    # Full normalization breakdown — None when built without a profile series
    # (e.g. in unit tests that construct ForecastAssumptions directly).
    revenue_growth_detail: AssumptionDetail | None = None
    ebit_margin_detail:    AssumptionDetail | None = None
    da_pct_detail:         AssumptionDetail | None = None
    capex_pct_detail:      AssumptionDetail | None = None
    tax_rate_detail:       AssumptionDetail | None = None
    nwc_pct_detail:        AssumptionDetail | None = None


# ── Builder ───────────────────────────────────────────────────────────────────


def _series_vals(series: tuple[tuple[int, float], ...]) -> tuple[list[float], list[int]]:
    """Unzip a (year, value) series into parallel lists."""
    if not series:
        return [], []
    years, vals = zip(*series)
    return list(vals), list(years)


def build_assumptions_from_profile(
    profile: CompanyProfile,
    overrides: dict | None = None,
) -> ForecastAssumptions:
    """Derive ForecastAssumptions from historical CompanyProfile averages.

    Each assumption is computed via normalize_series() which returns windowed
    averages (5yr, 3yr, 2yr, most-recent) and an outlier-adjusted mean using a
    leave-one-out z-score threshold of 2σ.  The outlier-adjusted (normalized)
    mean becomes the selected_value — temporary events (restructurings, capex
    spikes, COVID impacts) are automatically excluded from the base case.

    Precedence: explicit override > normalized average > fallback default.
    Clamping is applied to the float fields; AssumptionDetail.selected_value
    reflects the post-clamp value.
    """
    ov = overrides or {}

    # ── Revenue growth ────────────────────────────────────────────────────────
    rg_vals, rg_years = _series_vals(profile.revenue_growth_series)
    rg_detail = normalize_series(rg_vals, rg_years, "revenue_growth")
    normalized_base = (
        rg_detail.normalized
        if rg_detail.normalized is not None
        else profile.revenue_cagr.value
        if profile.revenue_cagr.value is not None
        else _SPREAD
    )
    base_growth = float(ov.get("base_revenue_growth", normalized_base))
    bear_growth = float(ov.get("bear_revenue_growth", base_growth - _SPREAD))
    bull_growth = float(ov.get("bull_revenue_growth", base_growth + _SPREAD))
    # Rebuild detail with final selected_value in case override was used
    revenue_growth_detail = AssumptionDetail(
        historical_5y=rg_detail.historical_5y,
        historical_3y=rg_detail.historical_3y,
        historical_2y=rg_detail.historical_2y,
        most_recent=rg_detail.most_recent,
        normalized=rg_detail.normalized,
        selected_value=base_growth,
        rationale=rg_detail.rationale,
        outlier_years=rg_detail.outlier_years,
        outlier_notes=rg_detail.outlier_notes,
    )

    # ── Gross margin (no normalization — structural, not vol.) ────────────────
    gm_raw = profile.avg_gross_margin.value
    gross_margin: float | None = (
        float(ov["gross_margin"]) if "gross_margin" in ov
        else gm_raw
    )

    # ── EBIT margin ───────────────────────────────────────────────────────────
    em_vals, em_years = _series_vals(profile.ebit_margin_series)
    em_detail = normalize_series(em_vals, em_years, "ebit_margin")
    em_normalized = em_detail.normalized if em_detail.normalized is not None else _FALLBACK_EBIT_MARGIN
    ebit_margin = float(ov.get("ebit_margin", em_normalized))

    # ── Tax rate ───────────────────────────────────────────────────────────────
    tr_vals, tr_years = _series_vals(profile.tax_rate_series)
    tr_detail = normalize_series(tr_vals, tr_years, "effective_tax_rate")
    tr_normalized = tr_detail.normalized if tr_detail.normalized is not None else _FALLBACK_TAX_RATE
    tr_raw_val = float(ov.get("effective_tax_rate", tr_normalized))
    effective_tax_rate = _clamp("effective_tax_rate", tr_raw_val)
    tr_detail = clamp_detail(
        _rebuild(tr_detail, tr_raw_val),
        *_CLAMP["effective_tax_rate"],
    )

    # ── D&A % revenue ─────────────────────────────────────────────────────────
    da_vals, da_years = _series_vals(profile.da_pct_series)
    da_detail_raw = normalize_series(da_vals, da_years, "da_pct_revenue")
    da_normalized = da_detail_raw.normalized if da_detail_raw.normalized is not None else 0.0
    da_raw_val = float(ov.get("da_pct_revenue", da_normalized))
    da_pct_revenue = _clamp("da_pct_revenue", da_raw_val)
    da_detail = clamp_detail(_rebuild(da_detail_raw, da_raw_val), *_CLAMP["da_pct_revenue"])

    # ── Capex % revenue ───────────────────────────────────────────────────────
    cx_vals, cx_years = _series_vals(profile.capex_pct_series)
    cx_detail_raw = normalize_series(cx_vals, cx_years, "capex_pct_revenue")
    cx_normalized = cx_detail_raw.normalized if cx_detail_raw.normalized is not None else 0.0
    cx_raw_val = float(ov.get("capex_pct_revenue", cx_normalized))
    capex_pct_revenue = _clamp("capex_pct_revenue", cx_raw_val)
    cx_detail = clamp_detail(_rebuild(cx_detail_raw, cx_raw_val), *_CLAMP["capex_pct_revenue"])

    # ── NWC % revenue ─────────────────────────────────────────────────────────
    nwc_vals, nwc_years = _series_vals(profile.nwc_pct_series)
    nwc_detail_raw = normalize_series(nwc_vals, nwc_years, "nwc_pct_revenue")
    nwc_normalized = nwc_detail_raw.normalized if nwc_detail_raw.normalized is not None else 0.0
    nwc_raw_val = float(ov.get("nwc_pct_revenue", nwc_normalized))
    nwc_pct_revenue = _clamp("nwc_pct_revenue", nwc_raw_val)
    nwc_detail = clamp_detail(_rebuild(nwc_detail_raw, nwc_raw_val), *_CLAMP["nwc_pct_revenue"])

    # ── Debt cost ─────────────────────────────────────────────────────────────
    interest_rate_on_debt: float | None = ov.get("interest_rate_on_debt", None)
    if interest_rate_on_debt is not None:
        interest_rate_on_debt = float(interest_rate_on_debt)

    # ── Projection horizon ────────────────────────────────────────────────────
    n_projection_years = int(ov.get("n_projection_years", 5))

    # ── Quality carry-forward ─────────────────────────────────────────────────
    quality_issues = tuple(profile.quality_issues)

    return ForecastAssumptions(
        base_revenue_growth=base_growth,
        bear_revenue_growth=bear_growth,
        bull_revenue_growth=bull_growth,
        gross_margin=gross_margin,
        ebit_margin=ebit_margin,
        da_pct_revenue=da_pct_revenue,
        effective_tax_rate=effective_tax_rate,
        capex_pct_revenue=capex_pct_revenue,
        nwc_pct_revenue=nwc_pct_revenue,
        interest_rate_on_debt=interest_rate_on_debt,
        n_projection_years=n_projection_years,
        quality_issues=quality_issues,
        revenue_growth_detail=revenue_growth_detail,
        ebit_margin_detail=_rebuild(em_detail, ebit_margin),
        da_pct_detail=da_detail,
        capex_pct_detail=cx_detail,
        tax_rate_detail=tr_detail,
        nwc_pct_detail=nwc_detail,
    )


def _rebuild(detail: AssumptionDetail, selected_value: float) -> AssumptionDetail:
    """Return a copy of detail with selected_value replaced."""
    from dataclasses import replace as _replace
    return _replace(detail, selected_value=selected_value)
