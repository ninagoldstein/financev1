"""
Five-year historical company profile.

Aggregates HistoricalStatements into period averages, growth rates, and
coverage quality metadata.  All ratio computations skip years where either
the numerator or denominator is missing/None.

Coverage statistics reported in quality_notes:
  raw coverage     = populated canonical metrics / 46 total canonical metrics
  adjusted coverage = populated metrics / (46 - structural_gaps - stale)

A Ratio wraps a float | None value with an optional human-readable note that
explains quality limitations inherited from the underlying data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from secpull.quality import STRUCTURAL_GAPS, UNRELIABLE_METRICS
from secpull.statements import (
    HistoricalStatements,
    MetricPoint,
    StatementLine,
)

from secpull.models import METRIC_TAGS as _METRIC_TAGS

_TOTAL_CANONICAL = len(_METRIC_TAGS)


# ── Value types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ratio:
    """A computed ratio with optional quality context."""
    value: float | None
    note: str | None = None


# ── Profile ───────────────────────────────────────────────────────────────────


@dataclass
class CompanyProfile:
    ticker: str
    cik: str
    name: str
    years: list[int]                # FY years used in this profile

    statements: HistoricalStatements

    # Growth
    avg_revenue_growth: Ratio       # mean YoY
    revenue_cagr: Ratio             # (last/first) ^ (1/(n-1)) - 1

    # Profitability margins (as fractions; multiply by 100 for %)
    avg_gross_margin: Ratio
    avg_ebit_margin: Ratio
    avg_ebitda_margin: Ratio
    avg_net_margin: Ratio

    # Tax
    avg_effective_tax_rate: Ratio   # income_tax_expense / ebt (ebt derived inline)

    # Cash flow drivers
    avg_da_pct_revenue: Ratio
    avg_capex_pct_revenue: Ratio
    avg_nwc_pct_revenue: Ratio      # operating NWC / revenue

    # Leverage
    avg_net_debt_to_ebitda: Ratio

    # Coverage statistics
    raw_coverage_pct: float         # populated / 46
    adj_coverage_pct: float         # populated / (46 - gaps - stale)
    n_complete: int
    n_partial: int
    n_derived: int
    n_unreliable: int
    n_stale: int
    n_structural_gap: int
    n_absent: int

    # Human-readable quality flags
    quality_notes: list[str]


# ── Internal helpers ──────────────────────────────────────────────────────────


def _vals(line: StatementLine, years: list[int]) -> list[float | None]:
    """Extract ordered float values for the given years from a StatementLine."""
    return [
        line.values[yr].value if yr in line.values else None
        for yr in years
    ]


def _ratio_series(
    num_line: StatementLine,
    den_line: StatementLine,
    years: list[int],
) -> list[float | None]:
    """Compute num/den for each year; None if either is missing/zero."""
    result: list[float | None] = []
    for yr in years:
        n = num_line.values.get(yr)
        d = den_line.values.get(yr)
        if (
            n is not None and n.value is not None
            and d is not None and d.value is not None
            and d.value != 0
        ):
            result.append(n.value / d.value)
        else:
            result.append(None)
    return result


def _avg(series: list[float | None]) -> float | None:
    """Mean of non-None values; None when empty."""
    vals = [v for v in series if v is not None]
    return sum(vals) / len(vals) if vals else None


def _cagr(first: float | None, last: float | None, n: int) -> float | None:
    """Compound annual growth rate over n periods."""
    if first is None or last is None or first <= 0 or n <= 0:
        return None
    return (last / first) ** (1 / n) - 1


def _yoy_growth(rev_vals: list[float | None]) -> list[float | None]:
    """Year-over-year growth rates; None for first year or when prev is zero/None."""
    rates: list[float | None] = []
    for i, v in enumerate(rev_vals):
        if i == 0 or rev_vals[i - 1] is None or rev_vals[i - 1] == 0 or v is None:
            rates.append(None)
        else:
            rates.append((v - rev_vals[i - 1]) / rev_vals[i - 1])
    return rates


def _quality_note(lines: list[StatementLine], label: str) -> str | None:
    """Return a note if any of the lines contributing to a ratio is stale/partial."""
    issues = []
    for ln in lines:
        if ln.is_stale:
            latest_fy = max(ln.values.keys()) if ln.values else None
            issues.append(f"{ln.metric} STALE (last FY{latest_fy})")
        elif any(
            pt.coverage_quality in ("PARTIAL", "UNRELIABLE")
            for pt in ln.values.values()
        ):
            issues.append(f"{ln.metric} PARTIAL/UNRELIABLE")
    return f"{label}: {'; '.join(issues)}" if issues else None


def _operating_nwc(bs, years: list[int]) -> list[float | None]:
    """Operating NWC = (AR + INV + other_current) - (AP + accrued + deferred_rev_current).

    Returns one value per year; None when any component is missing.
    """
    asset_lines = [bs.accounts_receivable, bs.inventory, bs.other_current_assets]
    liab_lines = [bs.accounts_payable, bs.accrued_liabilities]

    result: list[float | None] = []
    for yr in years:
        assets = sum(
            ln.values[yr].value
            for ln in asset_lines
            if yr in ln.values and ln.values[yr].value is not None
        )
        liabs = sum(
            ln.values[yr].value
            for ln in liab_lines
            if yr in ln.values and ln.values[yr].value is not None
        )
        has_all = all(yr in ln.values for ln in asset_lines + liab_lines)
        result.append(assets - liabs if has_all else None)
    return result


def _coverage_counts(
    ticker: str,
    stmts: HistoricalStatements,
) -> tuple[int, int, int, int, int, int, int]:
    """Return (complete, partial, derived, unreliable, stale, gap, absent).

    Uses stmts.all_lines which covers all 46 canonical metrics, including
    those not shown in the condensed three-statement display views.
    """
    n_complete = n_partial = n_derived = n_unreliable = n_stale = n_gap = n_absent = 0

    for metric, ln in stmts.all_lines.items():
        if ln.is_structural_gap:
            n_gap += 1
            continue
        if ln.is_stale:
            n_stale += 1
            continue
        if not ln.values:
            n_absent += 1
            continue
        latest_yr = max(ln.values.keys())
        quality = ln.values[latest_yr].coverage_quality
        if quality == "COMPLETE":
            n_complete += 1
        elif quality == "PARTIAL":
            n_partial += 1
        elif quality == "DERIVED":
            n_derived += 1
        elif quality == "UNRELIABLE":
            n_unreliable += 1
        else:
            n_complete += 1  # fallback for unexpected values

    return n_complete, n_partial, n_derived, n_unreliable, n_stale, n_gap, n_absent


# ── Public builder ────────────────────────────────────────────────────────────


def build_profile(
    stmts: HistoricalStatements,
    name: str,
) -> CompanyProfile:
    """Build a CompanyProfile from HistoricalStatements.

    All averages use the years present in stmts.years.
    """
    ticker = stmts.ticker
    years = stmts.years
    is_ = stmts.income_statement
    bs = stmts.balance_sheet
    cfs = stmts.cash_flow_statement

    # ── Revenue growth ────────────────────────────────────────────────────────
    rev_vals = _vals(is_.revenue, years)
    yoy_rates = _yoy_growth(rev_vals)
    avg_rev_growth = Ratio(value=_avg(yoy_rates[1:]))

    first_rev = next((v for v in rev_vals if v is not None), None)
    last_rev = next((v for v in reversed(rev_vals) if v is not None), None)
    n_periods = sum(1 for v in rev_vals if v is not None)
    rev_cagr = Ratio(value=_cagr(first_rev, last_rev, n_periods - 1))

    # ── Margins ───────────────────────────────────────────────────────────────
    gm_note = _quality_note([is_.gross_profit, is_.revenue], "gross_margin")
    ebit_note = _quality_note([is_.ebit, is_.revenue], "ebit_margin")
    ebitda_note = _quality_note([is_.ebitda, is_.revenue], "ebitda_margin")
    net_note = _quality_note([is_.net_income, is_.revenue], "net_margin")

    avg_gross_margin = Ratio(
        value=_avg(_ratio_series(is_.gross_profit, is_.revenue, years)),
        note=gm_note,
    )
    avg_ebit_margin = Ratio(
        value=_avg(_ratio_series(is_.ebit, is_.revenue, years)),
        note=ebit_note,
    )
    avg_ebitda_margin = Ratio(
        value=_avg(_ratio_series(is_.ebitda, is_.revenue, years)),
        note=ebitda_note,
    )
    avg_net_margin = Ratio(
        value=_avg(_ratio_series(is_.net_income, is_.revenue, years)),
        note=net_note,
    )

    # ── Effective tax rate: income_tax / (ebit - interest_expense) ───────────
    # EBT is not a direct fact; derive inline as ebit - interest_expense
    tax_series: list[float | None] = []
    for yr in years:
        ebit_pt = is_.ebit.values.get(yr)
        ie_pt = is_.interest_expense.values.get(yr)
        tax_pt = is_.income_tax_expense.values.get(yr)
        if (
            ebit_pt and ebit_pt.value is not None
            and tax_pt and tax_pt.value is not None
        ):
            # Use ebt if interest_expense is available; else use ebit as proxy
            base = (
                ebit_pt.value - ie_pt.value
                if ie_pt and ie_pt.value is not None
                else ebit_pt.value
            )
            if base != 0:
                tax_series.append(tax_pt.value / base)
            else:
                tax_series.append(None)
        else:
            tax_series.append(None)

    ie_note = _quality_note([is_.interest_expense], "effective_tax_rate")
    avg_tax_rate = Ratio(value=_avg(tax_series), note=ie_note)

    # ── D&A % revenue ─────────────────────────────────────────────────────────
    avg_da_pct = Ratio(
        value=_avg(_ratio_series(is_.da, is_.revenue, years)),
        note=_quality_note([is_.da], "da_pct_revenue"),
    )

    # ── Capex % revenue ───────────────────────────────────────────────────────
    avg_capex_pct = Ratio(
        value=_avg(_ratio_series(cfs.capex, is_.revenue, years)),
        note=_quality_note([cfs.capex], "capex_pct_revenue"),
    )

    # ── Operating NWC % revenue ───────────────────────────────────────────────
    nwc_vals = _operating_nwc(bs, years)
    nwc_pct_series: list[float | None] = []
    for nwc, rev in zip(nwc_vals, rev_vals):
        if nwc is not None and rev is not None and rev != 0:
            nwc_pct_series.append(nwc / rev)
        else:
            nwc_pct_series.append(None)
    avg_nwc_pct = Ratio(value=_avg(nwc_pct_series))

    # ── Net debt / EBITDA ─────────────────────────────────────────────────────
    nd_series: list[float | None] = []
    for yr in years:
        cash_pt = bs.cash.values.get(yr)
        ltd_pt = bs.long_term_debt.values.get(yr)
        std_pt = bs.total_current_liabilities.values.get(yr)   # proxy fallback
        ebitda_pt = is_.ebitda.values.get(yr)
        if (
            cash_pt and cash_pt.value is not None
            and ltd_pt and ltd_pt.value is not None
            and ebitda_pt and ebitda_pt.value is not None
            and ebitda_pt.value != 0
        ):
            net_debt = ltd_pt.value - cash_pt.value
            nd_series.append(net_debt / ebitda_pt.value)
        else:
            nd_series.append(None)

    ltd_note = _quality_note([bs.long_term_debt], "net_debt_to_ebitda")
    avg_nd_ebitda = Ratio(value=_avg(nd_series), note=ltd_note)

    # ── Coverage statistics ───────────────────────────────────────────────────
    n_complete, n_partial, n_derived, n_unreliable, n_stale, n_gap, n_absent = (
        _coverage_counts(ticker, stmts)
    )
    n_populated = n_complete + n_partial + n_derived + n_unreliable
    raw_pct = n_populated / _TOTAL_CANONICAL * 100

    n_excluded = n_gap + n_stale
    adj_denom = _TOTAL_CANONICAL - n_excluded
    adj_pct = n_populated / adj_denom * 100 if adj_denom else 0.0

    # ── Quality notes ─────────────────────────────────────────────────────────
    notes: list[str] = []
    notes.append(
        f"Coverage: {n_populated}/{_TOTAL_CANONICAL} raw ({raw_pct:.0f}%)  |  "
        f"{n_populated}/{adj_denom} adjusted ({adj_pct:.0f}%) "
        f"[excl. {n_gap} structural gaps + {n_stale} stale]"
    )
    if n_stale:
        notes.append(f"{n_stale} metric(s) STALE (latest FY < 2024; excluded from coverage denominator)")
    if n_partial:
        notes.append(f"{n_partial} metric(s) PARTIAL (tag covers metric but with known economic limitations)")
    if n_unreliable:
        notes.append(f"{n_unreliable} metric(s) UNRELIABLE (technically extracted; economically misleading)")
    if n_absent:
        notes.append(f"{n_absent} metric(s) ABSENT (no XBRL tag found; not structurally expected)")
    for (t, m), reason in UNRELIABLE_METRICS.items():
        if t == ticker:
            notes.append(f"ECONOMIC FLAG — {m}: {reason}")
    for ratio_note in (gm_note, ebit_note, ebitda_note, net_note, ie_note, ltd_note):
        if ratio_note:
            notes.append(ratio_note)

    return CompanyProfile(
        ticker=ticker,
        cik=stmts.cik,
        name=name,
        years=years,
        statements=stmts,
        avg_revenue_growth=avg_rev_growth,
        revenue_cagr=rev_cagr,
        avg_gross_margin=avg_gross_margin,
        avg_ebit_margin=avg_ebit_margin,
        avg_ebitda_margin=avg_ebitda_margin,
        avg_net_margin=avg_net_margin,
        avg_effective_tax_rate=avg_tax_rate,
        avg_da_pct_revenue=avg_da_pct,
        avg_capex_pct_revenue=avg_capex_pct,
        avg_nwc_pct_revenue=avg_nwc_pct,
        avg_net_debt_to_ebitda=avg_nd_ebitda,
        raw_coverage_pct=raw_pct,
        adj_coverage_pct=adj_pct,
        n_complete=n_complete,
        n_partial=n_partial,
        n_derived=n_derived,
        n_unreliable=n_unreliable,
        n_stale=n_stale,
        n_structural_gap=n_gap,
        n_absent=n_absent,
        quality_notes=notes,
    )
