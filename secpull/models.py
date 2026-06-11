from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    cik: str
    ticker: str
    name: str


@dataclass(frozen=True)
class FinancialFact:
    cik: str
    metric: str
    tag_used: str
    value: float
    unit: str
    fiscal_year: int
    fiscal_period: str
    form: str
    end_date: str
    filed_date: str


METRIC_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "total_assets": ["Assets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}
