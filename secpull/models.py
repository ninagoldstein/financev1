from dataclasses import dataclass, field
from typing import Optional


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
    coverage_quality: str = "COMPLETE"   # COMPLETE | PARTIAL


@dataclass(frozen=True)
class DerivedFact:
    cik: str
    metric: str
    source: str                  # always "derived"
    formula_used: str
    source_metrics_used: str     # comma-separated list of input metric names
    value: Optional[float]       # None when inputs were missing or div-by-zero
    unit: str
    fiscal_year: int
    fiscal_period: str
    form: str
    end_date: str
    coverage_flag: str           # "complete" | "partial" | "missing" (input completeness)
    coverage_quality: str = "DERIVED"    # DERIVED | PARTIAL | UNRELIABLE


METRIC_TAGS: dict[str, list[str]] = {
    # ── Income Statement ──────────────────────────────────────────────────────
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "interest_expense": [
        "InterestExpense",
        "InterestAndDebtExpense",
        # PARTIAL: non-operating interest only; misses Ford Credit and similar
        "InterestExpenseNonoperating",
    ],
    "income_tax_expense": ["IncomeTaxExpenseBenefit", "IncomeTaxExpense"],
    # NetIncomeLossAvailableToCommonStockholdersBasic first: Ford stopped tagging
    # NetIncomeLoss after FY2024; FY2025 loss (-$8.182B) only in the Available tag.
    # LULU and VZ have no data in this tag → safe fallthrough to NetIncomeLoss.
    "net_income": [
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLoss",
    ],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],

    # ── Balance Sheet — Assets ────────────────────────────────────────────────
    # PARTIAL: includes restricted cash (~$400-500M for Ford/VZ); adopted by many
    # companies post-ASC 230 as their primary cash tag.  LULU stopped tagging
    # CashAndCashEquivalentsAtCarryingValue after FY2019.
    "cash": [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValue",
    ],
    # PARTIAL: AccountsNotesAndLoansReceivableNetCurrent includes device
    # installment plan notes (~$15B of VZ's $27B); not pure trade AR.
    "accounts_receivable": [
        "AccountsNotesAndLoansReceivableNetCurrent",
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
    ],
    "inventory": ["InventoryNet", "InventoryGross"],
    "prepaid_other_current": [
        "PrepaidExpenseAndOtherAssetsCurrent",
        "OtherAssetsCurrent",
    ],
    "total_current_assets": ["AssetsCurrent"],
    # Finance-lease ROU tag first: Ford switched to it in FY2025; LULU and VZ
    # have no data in this tag → safe fallthrough to PropertyPlantAndEquipmentNet.
    "ppe_net": [
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "PropertyPlantAndEquipmentNet",
    ],
    "goodwill": ["Goodwill"],
    "intangibles_net": [
        "IntangibleAssetsNetExcludingGoodwill",
        "FiniteLivedIntangibleAssetsNet",
    ],
    "other_noncurrent_assets": ["OtherAssetsNoncurrent"],
    "total_assets": ["Assets"],

    # ── Balance Sheet — Liabilities ───────────────────────────────────────────
    "accounts_payable": ["AccountsPayableCurrent"],
    "accrued_liabilities": [
        "AccruedLiabilitiesCurrent",
        "EmployeeRelatedLiabilitiesCurrent",
    ],
    "deferred_revenue_current": [
        "ContractWithCustomerLiabilityCurrent",
        "DeferredRevenueCurrent",
    ],
    "current_portion_ltd": [
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
    ],
    "short_term_debt": ["ShortTermBorrowings", "CommercialPaper"],
    "total_current_liabilities": ["LiabilitiesCurrent"],
    "long_term_debt": [
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermNotesPayable",
    ],
    "deferred_tax_liability": [
        "DeferredIncomeTaxLiabilitiesNet",
        "DeferredTaxLiabilitiesNoncurrent",
    ],
    "other_noncurrent_liabilities": ["OtherLiabilitiesNoncurrent"],
    "total_liabilities": ["Liabilities"],

    # ── Balance Sheet — Equity ────────────────────────────────────────────────
    "additional_paid_in_capital": [
        "AdditionalPaidInCapitalCommonStock",
        "AdditionalPaidInCapital",
    ],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "treasury_stock": ["TreasuryStockValue"],
    "total_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],

    # ── Cash Flow Statement ───────────────────────────────────────────────────
    "stock_based_compensation": [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ],
    "change_in_accounts_receivable": ["IncreaseDecreaseInAccountsReceivable"],
    "change_in_inventory": ["IncreaseDecreaseInInventories"],
    "change_in_accounts_payable": [
        "IncreaseDecreaseInAccountsPayable",
        "IncreaseDecreaseInAccountsPayableAndAccruedLiabilities",
        "IncreaseDecreaseInAccountsPayableAndOtherOperatingLiabilities",
    ],
    "change_in_deferred_revenue": [
        "IncreaseDecreaseInDeferredRevenue",
        "IncreaseDecreaseInContractWithCustomerLiability",
    ],
    # PaymentsToAcquireOtherProductiveAssets first: VZ migrated to this tag
    # after FY2018; LULU and Ford have no data → safe fallthrough.
    "capex": [
        "PaymentsToAcquireOtherProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "cfi": ["NetCashProvidedByUsedInInvestingActivities"],
    "cff": ["NetCashProvidedByUsedInFinancingActivities"],
    # RepaymentsOfDebt first: VZ migrated to this tag after FY2011;
    # LULU and Ford have no data in this tag → safe fallthrough.
    "debt_repayment": [
        "RepaymentsOfDebt",
        "RepaymentsOfLongTermDebt",
    ],
    "share_repurchases": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
}
