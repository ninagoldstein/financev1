"""
Normalization layer for forecast assumptions.

Computes windowed averages (5yr, 3yr, 2yr, most-recent) and an
outlier-adjusted average for any per-year ratio series.  Outliers are
detected using a leave-one-out (LOO) z-score, which avoids the masking
effect that a single extreme value has on the inclusive mean and std.

LOO z-score for observation i:
    mu_loo  = mean of all other n-1 observations
    std_loo = sample std of all other n-1 observations
    z_i     = (value_i - mu_loo) / std_loo

    Flagged when |z_i| >= OUTLIER_SIGMA (default 2.0).

Minimum 4 observations are required for LOO detection; shorter series
return averages with no outlier analysis.

AssumptionDetail is the output type.  It stores all computed averages,
the outlier metadata, and the selected_value that the DCF layer uses
(= normalized average, clamped by the caller if needed).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

OUTLIER_SIGMA: float = 2.0   # LOO z-score threshold for outlier flagging
_MIN_LOO: int = 4            # minimum observations for LOO detection


# ── Output type ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssumptionDetail:
    """Full breakdown of one forecast assumption, including normalized value."""
    historical_5y: float | None      # mean of up to 5 most-recent years
    historical_3y: float | None      # mean of up to 3 most-recent years
    historical_2y: float | None      # mean of up to 2 most-recent years
    most_recent: float | None        # single most-recent year value
    normalized: float | None         # outlier-adjusted mean
    selected_value: float | None     # value DCF uses (= normalized, post-clamp)
    rationale: str                   # why selected_value was chosen
    outlier_years: tuple[int, ...]   # FY years flagged as outliers
    outlier_notes: tuple[str, ...]   # one explanation per outlier year


# ── Internal math ─────────────────────────────────────────────────────────────


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals)


def _sample_std(vals: list[float]) -> float:
    """Sample standard deviation (Bessel-corrected, n-1 denominator)."""
    if len(vals) < 2:
        return 0.0
    mu = _mean(vals)
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))


def _window_mean(values: list[float], years: list[int], n: int) -> float | None:
    """Mean of the n most-recent observations (by year), or None if no data."""
    paired = sorted(zip(years, values), key=lambda p: p[0], reverse=True)[:n]
    vals = [v for _, v in paired]
    return _mean(vals) if vals else None


# ── Public API ────────────────────────────────────────────────────────────────


def normalize_series(
    values: list[float],
    years: list[int],
    metric_label: str,
    fmt: str = ".1%",
) -> AssumptionDetail:
    """Compute windowed and outlier-adjusted averages for a metric series.

    Args:
        values:       Non-None observations, paired with ``years``.
        years:        Fiscal year label for each observation (same order as values).
        metric_label: Human-readable metric name used in note strings.
        fmt:          Python format spec for numeric values in note strings.
                      Use ".1%" for ratio series, ".2f" for absolute values.

    Returns:
        AssumptionDetail with all averages and outlier metadata.
        ``selected_value`` equals ``normalized`` (caller may clamp and replace).
    """
    if not values:
        return AssumptionDetail(
            historical_5y=None, historical_3y=None, historical_2y=None,
            most_recent=None, normalized=None, selected_value=None,
            rationale="No historical data available.",
            outlier_years=(), outlier_notes=(),
        )

    # ── Window averages ───────────────────────────────────────────────────────
    hist_5y = _window_mean(values, years, 5)
    hist_3y = _window_mean(values, years, 3)
    hist_2y = _window_mean(values, years, 2)

    most_recent_yr = max(years)
    most_recent_idx = years.index(most_recent_yr)
    most_recent = values[most_recent_idx]

    # ── Outlier detection (LOO z-score) ───────────────────────────────────────
    outlier_indices: list[int] = []
    outlier_notes: list[str] = []

    if len(values) >= _MIN_LOO:
        for i, v in enumerate(values):
            loo = [x for j, x in enumerate(values) if j != i]
            mu_loo  = _mean(loo)
            std_loo = _sample_std(loo)
            if std_loo == 0.0:
                continue
            z = (v - mu_loo) / std_loo
            if abs(z) >= OUTLIER_SIGMA:
                outlier_indices.append(i)
                direction = "above" if z > 0 else "below"
                note = (
                    f"FY{years[i]} {metric_label} ({v:{fmt}}) is "
                    f"{abs(z):.1f}σ {direction} the mean of remaining "
                    f"years ({mu_loo:{fmt}}); potentially a transient event."
                )
                outlier_notes.append(note)

    # ── Normalized mean (exclude outliers; keep at least 1 observation) ───────
    clean_idx = [i for i in range(len(values)) if i not in outlier_indices]
    if not clean_idx:
        clean_idx = list(range(len(values)))  # safety: never discard everything

    normalized = _mean([values[i] for i in clean_idx])
    outlier_years_tuple = tuple(years[i] for i in outlier_indices)

    # ── Rationale ─────────────────────────────────────────────────────────────
    if not outlier_years_tuple:
        rationale = f"5-year average; no outliers detected."
    else:
        excl = ", ".join(f"FY{y}" for y in sorted(outlier_years_tuple))
        raw_mean = _mean(values)
        rationale = (
            f"Outlier-adjusted average excluding {excl}. "
            f"Raw {len(values)}-year average was {raw_mean:{fmt}}."
        )

    return AssumptionDetail(
        historical_5y=hist_5y,
        historical_3y=hist_3y,
        historical_2y=hist_2y,
        most_recent=most_recent,
        normalized=normalized,
        selected_value=normalized,
        rationale=rationale,
        outlier_years=outlier_years_tuple,
        outlier_notes=tuple(outlier_notes),
    )


def clamp_detail(
    detail: AssumptionDetail,
    lo: float,
    hi: float,
    fmt: str = ".1%",
) -> AssumptionDetail:
    """Return a copy of detail with selected_value clamped to [lo, hi].

    If clamping changes the value the rationale is updated to note the bound.
    """
    if detail.selected_value is None:
        return detail
    clamped = max(lo, min(hi, detail.selected_value))
    if clamped == detail.selected_value:
        return detail
    bound = "lower" if clamped > detail.selected_value else "upper"
    return replace(
        detail,
        selected_value=clamped,
        rationale=f"{detail.rationale} Clamped to {bound} bound ({clamped:{fmt}}).",
    )
