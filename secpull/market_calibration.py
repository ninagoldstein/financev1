"""
Market calibration: compare model EV to market EV and solve for implied assumptions.

Four single-variable implied values are computed against the base-scenario GGM DCF:
  1. Implied WACC            — holding the projected FCFF stream and TGR fixed
  2. Implied terminal growth — holding the FCFF stream and WACC fixed
  3. Implied exit multiple   — analytical (linear in exit_mult)
  4. Implied capex % revenue — analytical (EV is linear in capex_pct)

Guardrail ranges (solver returns None when no solution exists):
  WACC            [5%,  15%]
  Terminal growth [0%,   5%]
  Exit multiple   [5x,  30x]
  Capex %         [5%,  40%]

All solver values operate on the base scenario projected FCFF stream.  Bear/bull
scenarios are not used here — calibration is a base-case question.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from secpull.dcf import DCFInputs, DCFResult
from secpull.forecast import ProjectedStatements

# ── Guardrail bounds ──────────────────────────────────────────────────────────

_WACC_LO  = 0.05;  _WACC_HI  = 0.15
_TGR_LO   = 0.00;  _TGR_HI   = 0.05
_EXIT_LO  = 5.0;   _EXIT_HI  = 30.0
_CAPEX_LO = 0.05;  _CAPEX_HI = 0.40


# ── Output type ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketCalibrationResult:
    """Comparison of model EV to market EV with implied single-variable assumptions."""
    market_equity_value: float          # price × shares OR market_cap directly
    market_enterprise_value: float      # market_equity + net_debt
    model_enterprise_value: float       # base-scenario GGM EV from the DCF
    ev_gap: float                       # market_EV − model_EV  (+ = market premium)
    ev_gap_pct: float                   # ev_gap / |model_EV|
    implied_wacc: float | None          # None = no solution in guardrail range
    implied_terminal_growth_rate: float | None
    implied_exit_ebitda_multiple: float | None
    implied_capex_pct_revenue: float | None
    notes: tuple[str, ...]              # human-readable explanation of any None values


# ── Internal math ─────────────────────────────────────────────────────────────


def _ev_gg(fcffs: tuple[float, ...], wacc: float, tgr: float) -> float:
    """Gordon Growth EV for a fixed FCFF stream at arbitrary wacc and tgr.

    Requires wacc > tgr.  Caller must ensure this before calling.
    """
    N = len(fcffs)
    sum_pv = sum(f / (1.0 + wacc) ** (n + 1) for n, f in enumerate(fcffs))
    tv = fcffs[-1] * (1.0 + tgr) / (wacc - tgr)
    pv_tv = tv / (1.0 + wacc) ** N
    return sum_pv + pv_tv


def _bisect(
    f,
    lo: float,
    hi: float,
    target: float,
    tol: float = 1e-9,
    max_iter: int = 100,
) -> float | None:
    """Find x in [lo, hi] where f(x) = target by bisection.

    Returns None when f(lo) and f(hi) do not bracket the target (same sign),
    or when either bound evaluates to NaN.
    """
    f_lo = f(lo) - target
    f_hi = f(hi) - target
    if math.isnan(f_lo) or math.isnan(f_hi):
        return None
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        if hi - lo < tol:
            break
        mid = (lo + hi) / 2.0
        f_mid = f(mid) - target
        if f_lo * f_mid <= 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


# ── Public builder ────────────────────────────────────────────────────────────


def calibrate(
    projected: ProjectedStatements,
    base_inputs: DCFInputs,
    dcf: DCFResult,
    market_price_per_share: float | None = None,
    market_cap: float | None = None,
) -> MarketCalibrationResult:
    """Compare the base-case GGM model EV to the market EV, then solve for
    the single-variable implied assumption for each of four key drivers.

    Exactly one of ``market_price_per_share`` or ``market_cap`` must be provided.
    When using ``market_price_per_share``, ``base_inputs.diluted_shares`` must be set.

    All implied values are solved against the base scenario.  When no solution
    exists within the guardrail range, the field is None and a note is appended.
    """
    # ── Validate inputs ───────────────────────────────────────────────────────
    if market_price_per_share is None and market_cap is None:
        raise ValueError("Provide market_price_per_share or market_cap.")
    if market_price_per_share is not None and market_cap is not None:
        raise ValueError("Provide market_price_per_share OR market_cap, not both.")

    # ── Market EV ─────────────────────────────────────────────────────────────
    if market_price_per_share is not None:
        if base_inputs.diluted_shares is None or base_inputs.diluted_shares <= 0:
            raise ValueError(
                "base_inputs.diluted_shares must be positive when using market_price_per_share."
            )
        market_equity = market_price_per_share * base_inputs.diluted_shares
    else:
        market_equity = float(market_cap)  # type: ignore[arg-type]

    net_debt      = base_inputs.net_debt
    market_ev     = market_equity + net_debt
    model_ev      = dcf.base.enterprise_value_gg
    ev_gap        = market_ev - model_ev
    ev_gap_pct    = ev_gap / abs(model_ev) if model_ev != 0.0 else math.nan

    notes: list[str] = []
    fcffs = tuple(fy.fcff for fy in projected.base.years)
    wacc  = base_inputs.wacc
    tgr   = base_inputs.terminal_growth_rate
    N     = len(fcffs)

    # ── 1. Implied WACC ───────────────────────────────────────────────────────
    # EV is strictly decreasing in WACC when all FCFFs and TV are positive.
    # Search [max(5%, tgr+ε), 15%] to ensure GGM denominator stays positive.
    w_lo = max(_WACC_LO, tgr + 1e-4)
    implied_wacc: float | None = None
    if w_lo < _WACC_HI:
        implied_wacc = _bisect(
            lambda w: _ev_gg(fcffs, w, tgr),
            w_lo, _WACC_HI,
            target=market_ev,
        )
    if implied_wacc is None:
        notes.append(
            f"Implied WACC: no solution within [{_WACC_LO:.0%}, {_WACC_HI:.0%}]. "
            f"Market EV ({market_ev:,.0f}) is outside the EV range achievable "
            f"at these bounds given the current FCFF stream."
        )

    # ── 2. Implied terminal growth rate ───────────────────────────────────────
    # EV is strictly increasing in TGR (for WACC fixed > TGR).
    # Search [0%, min(5%, wacc-ε)].
    g_hi = min(_TGR_HI, wacc - 1e-4)
    implied_tgr: float | None = None
    if _TGR_LO < g_hi:
        implied_tgr = _bisect(
            lambda g: _ev_gg(fcffs, wacc, g),
            _TGR_LO, g_hi,
            target=market_ev,
        )
    if implied_tgr is None:
        notes.append(
            f"Implied TGR: no solution within [{_TGR_LO:.0%}, {_TGR_HI:.0%}]. "
            f"Market EV ({market_ev:,.0f}) is outside the achievable range."
        )

    # ── 3. Implied exit EBITDA multiple (analytical) ──────────────────────────
    # EV_exit = sum_pv_fcff + exit_mult × EBITDA_N / (1+WACC)^N
    # → exit_mult = (market_EV − sum_pv) × (1+WACC)^N / EBITDA_N
    ebitda_N = projected.base.years[-1].ebitda
    implied_exit: float | None = None
    if ebitda_N is not None and ebitda_N > 0:
        sum_pv_fcff = dcf.base.sum_pv_fcff
        pv_factor   = (1.0 + wacc) ** N
        raw_exit    = (market_ev - sum_pv_fcff) * pv_factor / ebitda_N
        if _EXIT_LO <= raw_exit <= _EXIT_HI:
            implied_exit = raw_exit
        else:
            notes.append(
                f"Implied exit multiple ({raw_exit:.1f}x): no solution within "
                f"[{_EXIT_LO:.0f}x, {_EXIT_HI:.0f}x]."
            )
    else:
        notes.append(
            "Implied exit multiple: terminal EBITDA is zero or negative; cannot solve."
        )

    # ── 4. Implied capex % revenue (analytical) ───────────────────────────────
    # EV(c) = model_EV + (capex_pct_base − c) × K  where:
    #   K = Σ R_n/(1+w)^n + R_N×(1+g)/(w−g)/(1+w)^N  (PV-weighted revenue sum)
    # → c = capex_pct_base + (model_EV − market_EV) / K
    implied_capex: float | None = None
    revenues = [fy.revenue for fy in projected.base.years]
    base_year_revenue = revenues[0]
    if base_year_revenue > 0:
        K = sum(revenues[n] / (1.0 + wacc) ** (n + 1) for n in range(N))
        K += revenues[-1] * (1.0 + tgr) / (wacc - tgr) / (1.0 + wacc) ** N

        capex_pct_base = projected.base.years[0].capex / base_year_revenue
        raw_capex = capex_pct_base + (model_ev - market_ev) / K

        if _CAPEX_LO <= raw_capex <= _CAPEX_HI:
            implied_capex = raw_capex
        else:
            notes.append(
                f"Implied capex % ({raw_capex:.1%}): no solution within "
                f"[{_CAPEX_LO:.0%}, {_CAPEX_HI:.0%}]."
            )
    else:
        notes.append("Implied capex: projected revenue is zero.")

    return MarketCalibrationResult(
        market_equity_value=market_equity,
        market_enterprise_value=market_ev,
        model_enterprise_value=model_ev,
        ev_gap=ev_gap,
        ev_gap_pct=ev_gap_pct,
        implied_wacc=implied_wacc,
        implied_terminal_growth_rate=implied_tgr,
        implied_exit_ebitda_multiple=implied_exit,
        implied_capex_pct_revenue=implied_capex,
        notes=tuple(notes),
    )
