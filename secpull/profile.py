"""
Five-year historical company profile.

Aggregates HistoricalStatements into period averages, growth rates, and
coverage quality metadata.

Coverage statistics:
  raw coverage     = n_populated / _TOTAL_CANONICAL
  adjusted coverage = n_populated / (_TOTAL_CANONICAL - n_structural_gap - n_stale)

  _TOTAL_CANONICAL = len(METRIC_TAGS) — always derived dynamically.

  Canonical metric universe: only the 46 metrics in METRIC_TAGS count.
  Derived-only metrics (ebitda, fcf, working_capital, etc. from TIER1_FORMULAS)
  do NOT expand the denominator.  They may improve n_populated when a canonical
  metric (e.g. total_liabilities) is computed by formula, but no new denominator
  slots are created.

Quality:
  Ratio         — wraps float | None with an optional human-readable note
                   explaining which input(s) have quality limitations.
  QualityIssue  — structured annotation designed for downstream consumption
                   (assumptions, forecast, DCF layers).  severity:
                     INFO    — informational (derived, partial with minor limits)
                     WARNING — concern that may affect analysis (stale, absent)
                     ERROR   — economically misleading despite extraction
  quality_notes — list[str] generated automatically from quality_issues;
                   kept for backwards compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from secpull.models import METRIC_TAGS as _METRIC_TAGS
from secpull.quality import (
    STRUCTURAL_GAPS,
    UNRELIABLE_METRICS,
    QualityIssue,
)
from secpull.statements import (
    HistoricalStatements,
    StatementLine,
)

# Canonical metric count — always derived from the authoritative source.
# Adding metrics to METRIC_TAGS automatically adjusts all coverage percentages.
_TOTAL_CANONICAL: int = len(_METRIC_TAGS)


# ── Value types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ratio:
    """A computed ratio with optional context explaining quality limitations."""
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
    avg_revenue_growth: Ratio       # mean YoY growth rate
    revenue_cagr: Ratio             # (last/first)^(1/(n-1)) - 1

    # Profitability margins (fractions; ×100 for %)
    avg_gross_margin: Ratio
    avg_ebit_margin: Ratio
    avg_ebitda_margin: Ratio
    avg_net_margin: Ratio

    # Tax
    avg_effective_tax_rate: Ratio   # tax_expense / EBT (EBT = ebit - interest)

    # Cash flow drivers
    avg_da_pct_revenue: Ratio
    avg_capex_pct_revenue: Ratio
    avg_nwc_pct_revenue: Ratio      # operating NWC / revenue

    # Leverage
    avg_net_debt_to_ebitda: Ratio

    # Coverage counts
    raw_coverage_pct: float         # n_populated / 46
    adj_coverage_pct: float         # n_populated / (46 - gaps - stale)
    n_complete: int
    n_partial: int
    n_derived: int
    n_unreliable: int
    n_stale: int
    n_structural_gap: int
    n_absent: int

    # Structured quality annotations — canonical representation
    quality_issues: list[QualityIssue]

    # Human-readable — auto-generated from quality_issues; backwards-compat
    quality_notes: list[str]


# ── Internal math helpers ─────────────────────────────────────────────────────


def _vals(line: StatementLine, years: list[int]) -> list[float | None]:
    return [
        line.values[yr].value if yr in line.values else None
        for yr in years
    ]


def _ratio_series(
    num_line: StatementLine,
    den_line: StatementLine,
    years: list[int],
) -> list[float | None]:
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
    vals = [v for v in series if v is not None]
    return sum(vals) / len(vals) if vals else None


def _cagr(first: float | None, last: float | None, n: int) -> float | None:
    if first is None or last is None or first <= 0 or n <= 0:
        return None
    return (last / first) ** (1 / n) - 1


def _yoy_growth(rev_vals: list[float | None]) -> list[float | None]:
    rates: list[float | None] = []
    for i, v in enumerate(rev_vals):
        if i == 0 or rev_vals[i - 1] is None or rev_vals[i - 1] == 0 or v is None:
            rates.append(None)
        else:
            rates.append((v - rev_vals[i - 1]) / rev_vals[i - 1])
    return rates


def _quality_note(lines: list[StatementLine], label: str) -> str | None:
    """Return a Ratio.note string when any input line is stale or imperfect.

    Used exclusively for human-readable Ratio.note fields; structured quality
    information is produced separately by _build_quality_issues.
    """
    issues = []
    for ln in lines:
        if ln.metric is None:
            continue
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
    """Operating NWC = (AR + INV + prepaid) − (AP + accrued).

    Returns one value per year; None when any required component is absent.
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
    stmts: HistoricalStatements,
) -> tuple[int, int, int, int, int, int, int]:
    """Return (complete, partial, derived, unreliable, stale, gap, absent).

    Iterates stmts.all_lines which covers exactly the 46 canonical metrics.
    Derived-only metrics (ebitda, fcf, etc.) are NOT in all_lines and do not
    contribute to any counter — they cannot silently expand the denominator.
    """
    n_complete = n_partial = n_derived = n_unreliable = n_stale = n_gap = n_absent = 0
    for metric, ln in stmts.all_lines.items():
        if ln.is_structural_gap:
            n_gap += 1
        elif ln.is_stale:
            n_stale += 1
        elif not ln.values:
            n_absent += 1
        else:
            q = ln.values[max(ln.values.keys())].coverage_quality
            if q == "COMPLETE":
                n_complete += 1
            elif q == "PARTIAL":
                n_partial += 1
            elif q == "DERIVED":
                n_derived += 1
            elif q == "UNRELIABLE":
                n_unreliable += 1
            else:
                n_complete += 1
    return n_complete, n_partial, n_derived, n_unreliable, n_stale, n_gap, n_absent


# ── QualityIssue builder ──────────────────────────────────────────────────────


def _build_quality_issues(
    ticker: str,
    stmts: HistoricalStatements,
    n_populated: int,
    n_gap: int,
    n_stale: int,
    raw_pct: float,
    adj_pct: float,
    adj_denom: int,
) -> list[QualityIssue]:
    """Generate one QualityIssue per problematic metric plus a coverage summary.

    Severity mapping:
      ERROR   — UNRELIABLE  (economically misleading)
      WARNING — STALE, ABSENT  (data quality concern)
      INFO    — PARTIAL, DERIVED  (present but with known limits)

    Structural gaps are omitted — they are expected absences, not data issues.
    Coverage summary is always first (metric="__coverage__", severity="INFO").
    Issues are then ordered ERROR → WARNING → INFO within canonical metric order.
    """
    issues: list[QualityIssue] = []

    # ── 1. Coverage summary ───────────────────────────────────────────────────
    issues.append(QualityIssue(
        metric="__coverage__",
        severity="INFO",
        message=(
            f"Coverage: {n_populated}/{_TOTAL_CANONICAL} raw ({raw_pct:.0f}%)  |  "
            f"{n_populated}/{adj_denom} adjusted ({adj_pct:.0f}%) "
            f"[excl. {n_gap} structural gaps + {n_stale} stale]"
        ),
    ))

    # ── 2. Per-metric issues (bucketed by severity for ordering) ──────────────
    errors: list[QualityIssue] = []
    warnings: list[QualityIssue] = []
    infos: list[QualityIssue] = []

    for metric, ln in stmts.all_lines.items():
        if ln.is_structural_gap:
            continue  # expected absence — no issue needed

        # UNRELIABLE takes priority over STALE: economically misleading regardless
        # of whether current data exists.
        if (ticker, metric) in UNRELIABLE_METRICS:
            reason = UNRELIABLE_METRICS[(ticker, metric)]
            errors.append(QualityIssue(
                metric=metric,
                severity="ERROR",
                message=f"{ticker} {metric}: UNRELIABLE — {reason}",
            ))
            continue

        if ln.is_stale:
            latest_fy = max(ln.values.keys()) if ln.values else None
            fy_str = f"FY{latest_fy}" if latest_fy is not None else "pre-window"
            warnings.append(QualityIssue(
                metric=metric,
                severity="WARNING",
                message=(
                    f"{ticker} {metric}: STALE — latest available {fy_str}; "
                    "tag stopped before FY2024, no current data"
                ),
            ))
            continue

        if not ln.values:
            warnings.append(QualityIssue(
                metric=metric,
                severity="WARNING",
                message=(
                    f"{ticker} {metric}: ABSENT — no XBRL tag found in filings; "
                    "metric cannot be extracted or derived"
                ),
            ))
            continue

        q = ln.values[max(ln.values.keys())].coverage_quality
        if q == "UNRELIABLE":
            reason = UNRELIABLE_METRICS.get(
                (ticker, metric),
                "economically misleading despite successful extraction",
            )
            errors.append(QualityIssue(
                metric=metric,
                severity="ERROR",
                message=f"{ticker} {metric}: UNRELIABLE — {reason}",
            ))
        elif q == "PARTIAL":
            infos.append(QualityIssue(
                metric=metric,
                severity="INFO",
                message=(
                    f"{ticker} {metric}: PARTIAL — tag covers the metric but "
                    "includes additional items beyond the canonical definition"
                ),
            ))
        elif q == "DERIVED":
            infos.append(QualityIssue(
                metric=metric,
                severity="INFO",
                message=(
                    f"{ticker} {metric}: DERIVED — no direct XBRL tag; "
                    "value computed from formula using other canonical metrics"
                ),
            ))

    issues.extend(errors)
    issues.extend(warnings)
    issues.extend(infos)
    return issues


# ── Public builder ────────────────────────────────────────────────────────────


def build_profile(
    stmts: HistoricalStatements,
    name: str,
) -> CompanyProfile:
    """Build a CompanyProfile from HistoricalStatements."""
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
    avg_gross_margin = Ratio(
        value=_avg(_ratio_series(is_.gross_profit, is_.revenue, years)),
        note=_quality_note([is_.gross_profit, is_.revenue], "gross_margin"),
    )
    avg_ebit_margin = Ratio(
        value=_avg(_ratio_series(is_.ebit, is_.revenue, years)),
        note=_quality_note([is_.ebit, is_.revenue], "ebit_margin"),
    )
    avg_ebitda_margin = Ratio(
        value=_avg(_ratio_series(is_.ebitda, is_.revenue, years)),
        note=_quality_note([is_.ebitda, is_.revenue], "ebitda_margin"),
    )
    avg_net_margin = Ratio(
        value=_avg(_ratio_series(is_.net_income, is_.revenue, years)),
        note=_quality_note([is_.net_income, is_.revenue], "net_margin"),
    )

    # ── Effective tax rate: tax / (ebit − interest_expense) ──────────────────
    tax_series: list[float | None] = []
    for yr in years:
        ebit_pt = is_.ebit.values.get(yr)
        ie_pt = is_.interest_expense.values.get(yr)
        tax_pt = is_.income_tax_expense.values.get(yr)
        if ebit_pt and ebit_pt.value is not None and tax_pt and tax_pt.value is not None:
            base = (
                ebit_pt.value - ie_pt.value
                if ie_pt and ie_pt.value is not None
                else ebit_pt.value
            )
            tax_series.append(tax_pt.value / base if base != 0 else None)
        else:
            tax_series.append(None)
    avg_tax_rate = Ratio(
        value=_avg(tax_series),
        note=_quality_note([is_.interest_expense], "effective_tax_rate"),
    )

    # ── Cash flow drivers ─────────────────────────────────────────────────────
    avg_da_pct = Ratio(
        value=_avg(_ratio_series(is_.da, is_.revenue, years)),
        note=_quality_note([is_.da], "da_pct_revenue"),
    )
    avg_capex_pct = Ratio(
        value=_avg(_ratio_series(cfs.capex, is_.revenue, years)),
        note=_quality_note([cfs.capex], "capex_pct_revenue"),
    )

    nwc_vals = _operating_nwc(bs, years)
    nwc_pct_series: list[float | None] = [
        nwc / rev if (nwc is not None and rev is not None and rev != 0) else None
        for nwc, rev in zip(nwc_vals, rev_vals)
    ]
    avg_nwc_pct = Ratio(value=_avg(nwc_pct_series))

    # ── Net debt / EBITDA ─────────────────────────────────────────────────────
    nd_series: list[float | None] = []
    for yr in years:
        cash_pt = bs.cash.values.get(yr)
        ltd_pt = bs.long_term_debt.values.get(yr)
        ebitda_pt = is_.ebitda.values.get(yr)
        if (
            cash_pt and cash_pt.value is not None
            and ltd_pt and ltd_pt.value is not None
            and ebitda_pt and ebitda_pt.value is not None
            and ebitda_pt.value != 0
        ):
            nd_series.append((ltd_pt.value - cash_pt.value) / ebitda_pt.value)
        else:
            nd_series.append(None)
    avg_nd_ebitda = Ratio(
        value=_avg(nd_series),
        note=_quality_note([bs.long_term_debt], "net_debt_to_ebitda"),
    )

    # ── Coverage statistics ───────────────────────────────────────────────────
    n_complete, n_partial, n_derived, n_unreliable, n_stale, n_gap, n_absent = (
        _coverage_counts(stmts)
    )
    n_populated = n_complete + n_partial + n_derived + n_unreliable
    raw_pct = n_populated / _TOTAL_CANONICAL * 100
    adj_denom = _TOTAL_CANONICAL - n_gap - n_stale
    adj_pct = n_populated / adj_denom * 100 if adj_denom else 0.0

    # ── Structured quality issues + backward-compat notes ────────────────────
    quality_issues = _build_quality_issues(
        ticker=ticker,
        stmts=stmts,
        n_populated=n_populated,
        n_gap=n_gap,
        n_stale=n_stale,
        raw_pct=raw_pct,
        adj_pct=adj_pct,
        adj_denom=adj_denom,
    )
    quality_notes = [issue.message for issue in quality_issues]

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
        quality_issues=quality_issues,
        quality_notes=quality_notes,
    )
