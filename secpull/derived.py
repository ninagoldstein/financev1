from secpull.models import DerivedFact, FinancialFact

# Registry of Tier 1 derived metrics.
# Each entry: formula string, ordered list of required raw metric names, output unit,
# and a callable that receives a dict {metric_name: float} and returns float | None.
TIER1_FORMULAS: dict[str, dict] = {
    "ebitda": {
        "formula": "operating_income + depreciation_amortization",
        "source_metrics": ["operating_income", "depreciation_amortization"],
        "unit": "USD",
        "compute": lambda m: m["operating_income"] + m["depreciation_amortization"],
    },
    "ebitda_margin": {
        "formula": "(operating_income + depreciation_amortization) / revenue",
        "source_metrics": ["operating_income", "depreciation_amortization", "revenue"],
        "unit": "ratio",
        "compute": lambda m: (
            (m["operating_income"] + m["depreciation_amortization"]) / m["revenue"]
            if m["revenue"] else None
        ),
    },
    "ebit_margin": {
        "formula": "operating_income / revenue",
        "source_metrics": ["operating_income", "revenue"],
        "unit": "ratio",
        "compute": lambda m: m["operating_income"] / m["revenue"] if m["revenue"] else None,
    },
    "net_margin": {
        "formula": "net_income / revenue",
        "source_metrics": ["net_income", "revenue"],
        "unit": "ratio",
        "compute": lambda m: m["net_income"] / m["revenue"] if m["revenue"] else None,
    },
    "working_capital": {
        "formula": "total_current_assets - total_current_liabilities",
        "source_metrics": ["total_current_assets", "total_current_liabilities"],
        "unit": "USD",
        "compute": lambda m: m["total_current_assets"] - m["total_current_liabilities"],
    },
    "fcf": {
        "formula": "cfo - capex",
        "source_metrics": ["cfo", "capex"],
        "unit": "USD",
        # capex is stored as a positive number (PaymentsToAcquirePropertyPlantAndEquipment)
        "compute": lambda m: m["cfo"] - m["capex"],
    },
    "total_liabilities": {
        "formula": "total_assets - total_equity",
        "source_metrics": ["total_assets", "total_equity"],
        "unit": "USD",
        "compute": lambda m: m["total_assets"] - m["total_equity"],
    },
}


def compute_derived_metrics(cik: str, facts: list[FinancialFact]) -> list[DerivedFact]:
    """Compute all Tier 1 derived metrics for every period present in facts.

    A period is the tuple (fiscal_year, fiscal_period, form, end_date).
    For each period, each formula is attempted. The result carries a coverage_flag:
      "complete"  — all inputs found and a numeric result was produced
      "partial"   — some but not all inputs found (or compute returned None)
      "missing"   — none of the required inputs found for this period
    """
    # Build a flat lookup: (metric, fy, fp, form, end_date) -> value
    lookup: dict[tuple, float] = {}
    for f in facts:
        lookup[(f.metric, f.fiscal_year, f.fiscal_period, f.form, f.end_date)] = f.value

    periods: set[tuple] = {
        (f.fiscal_year, f.fiscal_period, f.form, f.end_date)
        for f in facts
    }

    results: list[DerivedFact] = []
    for (fy, fp, form, end_date) in periods:
        for metric_name, spec in TIER1_FORMULAS.items():
            inputs = {
                src: lookup.get((src, fy, fp, form, end_date))
                for src in spec["source_metrics"]
            }
            n_present = sum(1 for v in inputs.values() if v is not None)
            n_total = len(inputs)

            if n_present == n_total:
                raw = spec["compute"](inputs)
                value = raw
                coverage_flag = "complete" if raw is not None else "partial"
            elif n_present > 0:
                value = None
                coverage_flag = "partial"
            else:
                value = None
                coverage_flag = "missing"

            results.append(DerivedFact(
                cik=cik,
                metric=metric_name,
                source="derived",
                formula_used=spec["formula"],
                source_metrics_used=",".join(spec["source_metrics"]),
                value=value,
                unit=spec["unit"],
                fiscal_year=fy,
                fiscal_period=fp,
                form=form,
                end_date=end_date,
                coverage_flag=coverage_flag,
            ))

    return results
