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
    "interest_expense": ["InterestExpense", "InterestAndDebtExpense"],
    "income_tax_expense": ["IncomeTaxExpenseBenefit", "IncomeTaxExpense"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],

    # ── Balance Sheet — Assets ────────────────────────────────────────────────
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "accounts_receivable": [
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
    ],
    "inventory": ["InventoryNet", "InventoryGross"],
    "prepaid_other_current": [
        "PrepaidExpenseAndOtherAssetsCurrent",
        "OtherAssetsCurrent",
    ],
    "total_current_assets": ["AssetsCurrent"],
    "ppe_net": [
        "PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
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
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "cfi": ["NetCashProvidedByUsedInInvestingActivities"],
    "cff": ["NetCashProvidedByUsedInFinancingActivities"],
    "debt_repayment": ["RepaymentsOfLongTermDebt"],
    "share_repurchases": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
}
