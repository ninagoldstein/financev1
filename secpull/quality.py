"""
Coverage quality constants and rules for the extraction layer.

coverage_quality values stored in financials and derived_financials tables:
  COMPLETE       — extracted from current filing, economically clean
  PARTIAL        — extracted but economically imperfect (e.g. includes items
                   beyond the canonical definition of the metric)
  DERIVED        — computed from other extracted metrics, not directly filed
  STRUCTURAL_GAP — company does not report this metric (not applicable or
                   combined with another line in their filings)
  STALE          — latest available period is older than FY2024; tag stopped
                   being used by the company
  UNRELIABLE     — technically extracted/derived but economically misleading
                   (e.g. Ford consolidated debt captured as ~$0.3B vs ~$100B actual)

STRUCTURAL_GAP, STALE, and UNRELIABLE are not stored per-row; they are computed
at audit/scorecard time from the rules below.
"""

COMPLETE       = "COMPLETE"
PARTIAL        = "PARTIAL"
DERIVED        = "DERIVED"
STRUCTURAL_GAP = "STRUCTURAL_GAP"
STALE          = "STALE"
UNRELIABLE     = "UNRELIABLE"

# Tags that return data covering the metric definition but with known
# economic limitations.  When pick_tag selects one of these, coverage_quality
# is set to PARTIAL instead of COMPLETE.
PARTIAL_QUALITY_TAGS: dict[str, str] = {
    # Includes restricted cash (~$400-500M for Ford/VZ, typically small for others).
    # Adopted by many companies post-ASC 230 (2018) as their primary cash tag.
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": PARTIAL,
    # VZ's primary AR tag post-FY2021: includes device installment plan notes
    # (~$15B of $27B), which are consumer-financing receivables, not trade AR.
    "AccountsNotesAndLoansReceivableNetCurrent": PARTIAL,
    # Non-operating interest only; misses operating segment interest for
    # financial-services companies (Ford: $1.25B vs ~$7-8B consolidated).
    "InterestExpenseNonoperating": PARTIAL,
}

# ── Audit-time rules (not stored per-row) ────────────────────────────────────

# Metrics that are structurally absent for a company — the company either has
# no such obligation/item or folds it into a combined line.
STRUCTURAL_GAPS: dict[str, set[str]] = {
    "LULU": {
        "interest_expense",      # debt-free
        "long_term_debt",        # debt-free
        "current_portion_ltd",   # debt-free
        "debt_repayment",        # debt-free
        "dividends_paid",        # no dividend programme
        "treasury_stock",        # retires shares, no treasury balance
    },
    "F": {
        "gross_profit",          # reports COGS, no GP subtotal in XBRL
        "accounts_payable",      # combined with accrued in one tag
        "accrued_liabilities",   # combined with AP in one tag
        "change_in_accounts_receivable",  # Ford Credit receivables make tag too broad
        "change_in_deferred_revenue",     # embedded in Other CF adjustments
        "acquisitions",          # no standard XBRL tag used
    },
    "VZ": {
        "gross_profit",          # reports COGS, no GP subtotal
        "stock_based_compensation",  # no separate XBRL tag in recent filings
        "change_in_deferred_revenue",  # embedded in Other CF adjustments
        "share_repurchases",     # VZ returns capital via dividends; no recent buybacks
    },
}

# Metrics that are technically captured but economically misleading.
# Key: (ticker, metric) → explanation
UNRELIABLE_METRICS: dict[tuple[str, str], str] = {
    ("F", "long_term_debt"): (
        "Captures automotive parent ~$0.3B; Ford Motor Credit carries ~$100B+ "
        "consolidated debt not separately XBRL-tagged."
    ),
    ("F", "interest_expense"): (
        "InterestExpenseNonoperating is $1.25B FY2025; consolidated Ford interest "
        "was historically $7-8B. Ford Credit interest is embedded in segment P&L."
    ),
    ("F", "fcf"): (
        "CFO ($21.3B) includes Ford Motor Credit funding flows; "
        "automotive-only FCF is approximately $4-6B."
    ),
    ("F", "ebitda"): (
        "D&A ($16B) includes Ford Credit operating-lease fleet depreciation (~$5-6B). "
        "Operating income includes ~$17B warranty accruals in FY2025."
    ),
}
