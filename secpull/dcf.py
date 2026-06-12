"""
Discounted cash flow valuation from projected bear/base/bull scenarios.

Two terminal-value methods:
  Gordon Growth Model (GGM):
    TV = FCFF_N × (1 + g) / (WACC − g)

  Exit EBITDA Multiple (optional):
    TV = EBITDA_N × exit_ebitda_multiple

Both produce separate enterprise value → equity value → price per share paths.
GGM is always computed; exit multiple is computed only when supplied.

Equity bridge:
  Equity Value = Enterprise Value − Net Debt
  Price / Share = Equity Value / diluted_shares

All dollar amounts are raw USD throughout (same units as the FCFF stream).
diluted_shares is the actual share count (e.g. 125_000_000, not 125.0).

Net Debt convention:
  Positive → company has more debt than cash (reduces equity value)
  Negative → net cash position (adds to equity value)

Price per share may be negative when EV < net_debt (distressed balance sheet);
the math is left intact — callers decide how to present the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from secpull.forecast import ProjectedStatements, ScenarioForecast
from secpull.quality import QualityIssue


# ── Inputs ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DCFInputs:
    wacc: float                         # discount rate (e.g. 0.10 = 10%)
    terminal_growth_rate: float         # Gordon Growth perpetuity rate (e.g. 0.025)
    exit_ebitda_multiple: float | None = None  # if set, also computes exit-multiple TV
    net_debt: float = 0.0               # long_term_debt − cash; positive = net debt; raw USD
    diluted_shares: float | None = None  # actual share count (e.g. 125_000_000); None → no price


# ── Per-scenario output ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScenarioDCF:
    scenario: str                       # "bear" | "base" | "bull"

    # Discounted cash flows
    pv_fcffs: tuple[float, ...]         # PV of each projected year's FCFF
    sum_pv_fcff: float                  # Σ PV(FCFF_n)

    # Gordon Growth Model
    terminal_value_gg: float            # TV = FCFF_N × (1+g) / (WACC−g)
    pv_terminal_value_gg: float         # TV_gg / (1+WACC)^N
    enterprise_value_gg: float          # sum_pv_fcff + pv_tv_gg
    equity_value_gg: float              # ev_gg − net_debt
    price_per_share_gg: float | None    # equity_gg / shares; None if no shares

    # Exit Multiple Model (all None when exit_ebitda_multiple not provided)
    terminal_value_exit: float | None
    pv_terminal_value_exit: float | None
    enterprise_value_exit: float | None
    equity_value_exit: float | None
    price_per_share_exit: float | None


# ── Full result ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DCFResult:
    ticker: str
    inputs: DCFInputs
    bear: ScenarioDCF
    base: ScenarioDCF
    bull: ScenarioDCF
    quality_issues: tuple[QualityIssue, ...]


# ── Internal engine ───────────────────────────────────────────────────────────


def _compute_scenario_dcf(
    scenario: ScenarioForecast,
    inputs: DCFInputs,
) -> ScenarioDCF:
    wacc = inputs.wacc
    g = inputs.terminal_growth_rate
    N = len(scenario.years)

    # ── PV of projected FCFFs ─────────────────────────────────────────────────
    pv_list: list[float] = []
    for n, fy in enumerate(scenario.years, start=1):
        pv_list.append(fy.fcff / (1.0 + wacc) ** n)
    sum_pv = sum(pv_list)

    # ── Terminal value — Gordon Growth Model ──────────────────────────────────
    final_fcff = scenario.years[-1].fcff
    tv_gg = final_fcff * (1.0 + g) / (wacc - g)
    pv_tv_gg = tv_gg / (1.0 + wacc) ** N

    ev_gg = sum_pv + pv_tv_gg
    equity_gg = ev_gg - inputs.net_debt
    price_gg = (
        equity_gg / inputs.diluted_shares
        if inputs.diluted_shares is not None
        else None
    )

    # ── Terminal value — Exit EBITDA Multiple ─────────────────────────────────
    tv_exit: float | None = None
    pv_tv_exit: float | None = None
    ev_exit: float | None = None
    equity_exit: float | None = None
    price_exit: float | None = None

    if inputs.exit_ebitda_multiple is not None:
        final_ebitda = scenario.years[-1].ebitda
        tv_exit = final_ebitda * inputs.exit_ebitda_multiple
        pv_tv_exit = tv_exit / (1.0 + wacc) ** N
        ev_exit = sum_pv + pv_tv_exit
        equity_exit = ev_exit - inputs.net_debt
        price_exit = (
            equity_exit / inputs.diluted_shares
            if inputs.diluted_shares is not None
            else None
        )

    return ScenarioDCF(
        scenario=scenario.scenario,
        pv_fcffs=tuple(pv_list),
        sum_pv_fcff=sum_pv,
        terminal_value_gg=tv_gg,
        pv_terminal_value_gg=pv_tv_gg,
        enterprise_value_gg=ev_gg,
        equity_value_gg=equity_gg,
        price_per_share_gg=price_gg,
        terminal_value_exit=tv_exit,
        pv_terminal_value_exit=pv_tv_exit,
        enterprise_value_exit=ev_exit,
        equity_value_exit=equity_exit,
        price_per_share_exit=price_exit,
    )


# ── Public builder ────────────────────────────────────────────────────────────


def build_dcf(
    projected: ProjectedStatements,
    inputs: DCFInputs,
) -> DCFResult:
    """Build a three-scenario DCF from projected statements and valuation inputs.

    Validates:
      - WACC > terminal_growth_rate (Gordon Growth denominator must be positive)
      - diluted_shares_m > 0 when provided (zero or negative shares is nonsensical)
      - exit_ebitda_multiple > 0 when provided

    Raises ValueError on invalid inputs.
    """
    if inputs.wacc <= inputs.terminal_growth_rate:
        raise ValueError(
            f"WACC ({inputs.wacc:.2%}) must be greater than terminal growth rate "
            f"({inputs.terminal_growth_rate:.2%}); Gordon Growth denominator would "
            f"be zero or negative."
        )
    if inputs.diluted_shares is not None and inputs.diluted_shares <= 0:
        raise ValueError(
            f"diluted_shares must be positive when provided, "
            f"got {inputs.diluted_shares}."
        )
    if inputs.exit_ebitda_multiple is not None and inputs.exit_ebitda_multiple <= 0:
        raise ValueError(
            f"exit_ebitda_multiple must be positive when provided, "
            f"got {inputs.exit_ebitda_multiple}."
        )

    bear = _compute_scenario_dcf(projected.bear, inputs)
    base = _compute_scenario_dcf(projected.base, inputs)
    bull = _compute_scenario_dcf(projected.bull, inputs)

    return DCFResult(
        ticker=projected.ticker,
        inputs=inputs,
        bear=bear,
        base=base,
        bull=bull,
        quality_issues=projected.quality_issues,
    )
