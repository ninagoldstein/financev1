"""
Historical financial statement views built from extracted and derived facts.

Three-statement model:
  IncomeStatement      — IS line items with quality metadata per cell
  BalanceSheet         — BS line items with quality metadata per cell
  CashFlowStatement    — CFS line items with quality metadata per cell

All three are assembled into HistoricalStatements, which is the authoritative
input for the profile, assumptions, forecast, and DCF layers.

Design notes:
  - Coverage_quality per MetricPoint reflects the individual data point's quality
    (COMPLETE, PARTIAL, DERIVED) as carried from FinancialFact / DerivedFact.
  - UNRELIABLE is applied at this layer for (ticker, metric) pairs in
    UNRELIABLE_METRICS — the MetricPoint reflects this so downstream consumers
    can see the flag without needing to re-check quality.py.
  - STALE lives at the StatementLine level (is_stale: bool) rather than per cell —
    individual historical points are accurate when filed; what's stale is the
    absence of current (FY2024+) data.
  - STRUCTURAL_GAP lives at the StatementLine level (is_structural_gap: bool).
  - CFS working-capital items are stored in CFS sign convention by XBRL
    (positive = cash inflow), so change_in_nwc = direct sum of components.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from secpull.models import DerivedFact, FinancialFact
from secpull.quality import (
    COMPLETE,
    DERIVED,
    PARTIAL,
    STALE,
    STRUCTURAL_GAPS,
    UNRELIABLE,
    UNRELIABLE_METRICS,
    classify_freshness,
)

# ── Core value type ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricPoint:
    value: float | None
    coverage_quality: str   # COMPLETE | PARTIAL | DERIVED | UNRELIABLE


# ── Statement line ────────────────────────────────────────────────────────────


@dataclass
class StatementLine:
    label: str
    metric: str | None              # None for inline-computed subtotals
    values: dict[int, MetricPoint]  # keyed by fiscal_year
    is_stale: bool = False
    is_structural_gap: bool = False


# ── Statement containers ──────────────────────────────────────────────────────


@dataclass
class IncomeStatement:
    ticker: str
    years: list[int]
    revenue: StatementLine
    gross_profit: StatementLine
    ebit: StatementLine             # = operating_income
    da: StatementLine
    ebitda: StatementLine           # ebit + da (DERIVED)
    interest_expense: StatementLine
    income_tax_expense: StatementLine
    net_income: StatementLine
    eps_diluted: StatementLine
    shares_diluted: StatementLine


@dataclass
class BalanceSheet:
    ticker: str
    years: list[int]
    # Current assets
    cash: StatementLine
    accounts_receivable: StatementLine
    inventory: StatementLine
    other_current_assets: StatementLine
    total_current_assets: StatementLine
    # Non-current assets
    ppe_net: StatementLine
    goodwill: StatementLine
    intangibles_net: StatementLine
    other_noncurrent_assets: StatementLine
    total_assets: StatementLine
    # Current liabilities
    accounts_payable: StatementLine
    accrued_liabilities: StatementLine
    current_portion_ltd: StatementLine
    total_current_liabilities: StatementLine
    # Non-current liabilities
    long_term_debt: StatementLine
    total_liabilities: StatementLine
    # Equity
    retained_earnings: StatementLine
    total_equity: StatementLine


@dataclass
class CashFlowStatement:
    ticker: str
    years: list[int]
    # Operating
    net_income: StatementLine
    da: StatementLine
    stock_based_compensation: StatementLine
    change_in_nwc: StatementLine    # computed subtotal; CFS sign convention
    cfo: StatementLine
    # Investing
    capex: StatementLine
    acquisitions: StatementLine
    cfi: StatementLine
    # Financing
    debt_repayment: StatementLine
    dividends_paid: StatementLine
    share_repurchases: StatementLine
    cff: StatementLine
    # Summary
    fcf: StatementLine              # cfo − capex (DERIVED)


@dataclass
class HistoricalStatements:
    ticker: str
    cik: str
    years: list[int]                # up to max_years, sorted ascending
    income_statement: IncomeStatement
    balance_sheet: BalanceSheet
    cash_flow_statement: CashFlowStatement


# ── Builder ───────────────────────────────────────────────────────────────────


def _dedup_facts(
    ticker: str,
    facts: list[FinancialFact],
) -> dict[str, dict[int, MetricPoint]]:
    """Deduplicate FY facts (latest filed_date wins) and apply UNRELIABLE override.

    Returns {metric: {fiscal_year: MetricPoint}}.
    """
    best: dict[tuple[str, int], FinancialFact] = {}
    for f in facts:
        if f.fiscal_period != "FY":
            continue
        key = (f.metric, f.fiscal_year)
        if key not in best or f.filed_date > best[key].filed_date:
            best[key] = f

    lookup: dict[str, dict[int, MetricPoint]] = {}
    for (metric, fy), f in best.items():
        quality = (
            UNRELIABLE
            if (ticker, metric) in UNRELIABLE_METRICS
            else f.coverage_quality
        )
        lookup.setdefault(metric, {})[fy] = MetricPoint(
            value=f.value, coverage_quality=quality
        )
    return lookup


def _add_derived(
    lookup: dict[str, dict[int, MetricPoint]],
    derived: list[DerivedFact],
) -> None:
    """Merge complete DerivedFacts into lookup; direct facts take priority."""
    for d in derived:
        if d.fiscal_period != "FY" or d.coverage_flag != "complete":
            continue
        yr_map = lookup.setdefault(d.metric, {})
        if d.fiscal_year not in yr_map:
            yr_map[d.fiscal_year] = MetricPoint(
                value=d.value, coverage_quality=d.coverage_quality
            )


def _compute_nwc(
    lookup: dict[str, dict[int, MetricPoint]],
    years: list[int],
) -> dict[int, MetricPoint]:
    """Sum WC CFS components; all stored in CFS sign convention (positive = inflow)."""
    components = [
        "change_in_accounts_receivable",
        "change_in_inventory",
        "change_in_accounts_payable",
        "change_in_deferred_revenue",
    ]
    result: dict[int, MetricPoint] = {}
    for yr in years:
        present = [
            lookup[c][yr]
            for c in components
            if c in lookup and yr in lookup[c] and lookup[c][yr].value is not None
        ]
        if not present:
            continue
        total = sum(pt.value for pt in present)  # type: ignore[arg-type]
        qual = (
            PARTIAL
            if any(pt.coverage_quality == PARTIAL for pt in present)
            else DERIVED
        )
        result[yr] = MetricPoint(value=total, coverage_quality=qual)
    return result


def build_statements(
    cik: str,
    ticker: str,
    facts: list[FinancialFact],
    derived: list[DerivedFact],
    max_years: int = 5,
) -> HistoricalStatements:
    """Assemble three-statement model from extracted and derived facts.

    Only FY (annual) periods are included.  max_years controls the lookback
    window — the most recent N fiscal years where any metric has data.
    """
    # Build flat lookup: metric → year → MetricPoint
    lookup = _dedup_facts(ticker, facts)
    _add_derived(lookup, derived)

    # Determine years: union of all available FY years, take last max_years
    all_years = sorted({yr for yr_map in lookup.values() for yr in yr_map})
    years = all_years[-max_years:] if len(all_years) > max_years else all_years

    # Pre-compute freshness and structural gap flags
    stale_metrics = classify_freshness(facts)   # {metric: STALE}
    struct_gaps = STRUCTURAL_GAPS.get(ticker, set())

    def _line(label: str, metric: str) -> StatementLine:
        is_gap = metric in struct_gaps
        is_stale = metric in stale_metrics and not is_gap
        values = {
            yr: lookup[metric][yr]
            for yr in years
            if metric in lookup and yr in lookup[metric]
        }
        return StatementLine(
            label=label,
            metric=metric,
            values=values,
            is_stale=is_stale,
            is_structural_gap=is_gap,
        )

    nwc_values = _compute_nwc(lookup, years)

    is_stmt = IncomeStatement(
        ticker=ticker,
        years=years,
        revenue=_line("Revenue", "revenue"),
        gross_profit=_line("Gross Profit", "gross_profit"),
        ebit=_line("EBIT", "operating_income"),
        da=_line("D&A", "depreciation_amortization"),
        ebitda=_line("EBITDA", "ebitda"),
        interest_expense=_line("Interest Expense", "interest_expense"),
        income_tax_expense=_line("Income Tax Expense", "income_tax_expense"),
        net_income=_line("Net Income", "net_income"),
        eps_diluted=_line("EPS (Diluted)", "eps_diluted"),
        shares_diluted=_line("Shares Diluted", "shares_diluted"),
    )

    bs = BalanceSheet(
        ticker=ticker,
        years=years,
        cash=_line("Cash & Equivalents", "cash"),
        accounts_receivable=_line("Accounts Receivable", "accounts_receivable"),
        inventory=_line("Inventory", "inventory"),
        other_current_assets=_line("Other Current Assets", "prepaid_other_current"),
        total_current_assets=_line("Total Current Assets", "total_current_assets"),
        ppe_net=_line("PP&E, Net", "ppe_net"),
        goodwill=_line("Goodwill", "goodwill"),
        intangibles_net=_line("Intangibles, Net", "intangibles_net"),
        other_noncurrent_assets=_line("Other Non-Current Assets", "other_noncurrent_assets"),
        total_assets=_line("Total Assets", "total_assets"),
        accounts_payable=_line("Accounts Payable", "accounts_payable"),
        accrued_liabilities=_line("Accrued Liabilities", "accrued_liabilities"),
        current_portion_ltd=_line("Current Portion of LTD", "current_portion_ltd"),
        total_current_liabilities=_line("Total Current Liabilities", "total_current_liabilities"),
        long_term_debt=_line("Long-Term Debt", "long_term_debt"),
        total_liabilities=_line("Total Liabilities", "total_liabilities"),
        retained_earnings=_line("Retained Earnings", "retained_earnings"),
        total_equity=_line("Total Equity", "total_equity"),
    )

    cfs = CashFlowStatement(
        ticker=ticker,
        years=years,
        net_income=_line("Net Income", "net_income"),
        da=_line("D&A", "depreciation_amortization"),
        stock_based_compensation=_line("Stock-Based Compensation", "stock_based_compensation"),
        change_in_nwc=StatementLine(
            label="Change in Working Capital",
            metric=None,
            values=nwc_values,
        ),
        cfo=_line("Cash From Operations", "cfo"),
        capex=_line("Capital Expenditures", "capex"),
        acquisitions=_line("Acquisitions", "acquisitions"),
        cfi=_line("Cash From Investing", "cfi"),
        debt_repayment=_line("Debt Repayment", "debt_repayment"),
        dividends_paid=_line("Dividends Paid", "dividends_paid"),
        share_repurchases=_line("Share Repurchases", "share_repurchases"),
        cff=_line("Cash From Financing", "cff"),
        fcf=_line("Free Cash Flow", "fcf"),
    )

    return HistoricalStatements(
        ticker=ticker,
        cik=cik,
        years=years,
        income_statement=is_stmt,
        balance_sheet=bs,
        cash_flow_statement=cfs,
    )
