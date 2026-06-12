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


# ── Builder ───────────────────────────────────────────────────────────────────


def build_assumptions_from_profile(
    profile: CompanyProfile,
    overrides: dict | None = None,
) -> ForecastAssumptions:
    """Derive ForecastAssumptions from historical CompanyProfile averages.

    Precedence: override > profile historical average > fallback default.
    Clamping is applied after overrides so all values are in-range.

    Args:
        profile:   Built by build_profile(); provides historical averages.
        overrides: Optional dict mapping ForecastAssumptions field names to
                   replacement float values.  Unknown keys are silently ignored.
    """
    ov = overrides or {}

    # ── Revenue growth: base from avg YoY, fall back to CAGR, then 3% ─────────
    base = (
        profile.avg_revenue_growth.value
        if profile.avg_revenue_growth.value is not None
        else profile.revenue_cagr.value
        if profile.revenue_cagr.value is not None
        else _SPREAD   # 3% default
    )
    base_growth = float(ov.get("base_revenue_growth", base))
    bear_growth = float(ov.get("bear_revenue_growth", base_growth - _SPREAD))
    bull_growth = float(ov.get("bull_revenue_growth", base_growth + _SPREAD))

    # ── Margins ───────────────────────────────────────────────────────────────
    gm_raw = profile.avg_gross_margin.value
    gross_margin: float | None = (
        float(ov["gross_margin"]) if "gross_margin" in ov
        else gm_raw   # may remain None
    )

    em_raw = profile.avg_ebit_margin.value
    ebit_margin = float(ov.get(
        "ebit_margin",
        em_raw if em_raw is not None else _FALLBACK_EBIT_MARGIN,
    ))

    # ── Tax rate ───────────────────────────────────────────────────────────────
    tr_raw = profile.avg_effective_tax_rate.value
    effective_tax_rate = _clamp(
        "effective_tax_rate",
        float(ov.get(
            "effective_tax_rate",
            tr_raw if tr_raw is not None else _FALLBACK_TAX_RATE,
        )),
    )

    # ── Cash flow drivers ─────────────────────────────────────────────────────
    da_raw = profile.avg_da_pct_revenue.value
    da_pct_revenue = _clamp(
        "da_pct_revenue",
        float(ov.get("da_pct_revenue", da_raw if da_raw is not None else 0.0)),
    )

    cx_raw = profile.avg_capex_pct_revenue.value
    capex_pct_revenue = _clamp(
        "capex_pct_revenue",
        float(ov.get("capex_pct_revenue", cx_raw if cx_raw is not None else 0.0)),
    )

    nwc_raw = profile.avg_nwc_pct_revenue.value
    nwc_pct_revenue = _clamp(
        "nwc_pct_revenue",
        float(ov.get("nwc_pct_revenue", nwc_raw if nwc_raw is not None else 0.0)),
    )

    # ── Debt cost — not estimable from income statement alone ─────────────────
    # Placeholder: user must supply via override.  Could be estimated as
    # interest_expense / avg_debt in a future enhancement.
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
    )
