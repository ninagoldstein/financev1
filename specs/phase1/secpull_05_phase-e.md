# SECPull — Phase E (Stretch): Peer Comparison

## What This Phase Builds

A `compare` command that pulls multiple tickers and produces a side-by-side annual
comparison of a single metric, with year-over-year growth rates — the seed of Nina's
"comps" idea. Only attempt this after Phases A–D are fully done; it's a stretch goal,
not required for the internship deliverable.

## Depends On

Phases A–D.

## Files Touched

- `secpull/compare.py` — created
- `secpull/cli.py` — modified (adds `compare` subcommand)
- `tests/test_compare.py` — created

## Key Definitions (restated)

`FinancialFact` fields used: `metric`, `value`, `fiscal_year`, `fiscal_period`
("FY" only here), `end_date`. Facts come from `db.get_facts(conn, cik, metric)`.
Growth is computed only between **consecutive** fiscal years; any gap yields `N/A`.

## Public Interface

```python
# secpull/compare.py
from secpull.models import FinancialFact

def yoy_growth(facts: list[FinancialFact]) -> dict[int, float | None]:
    """Pure function. Input: one company's FY facts for ONE metric.
    Output: {fiscal_year: growth_vs_prior_year} where growth is
    (curr - prior) / prior. The earliest year and any year whose prior year
    is absent map to None. Never extrapolates across gaps."""

def comparison_rows(per_ticker: dict[str, list[FinancialFact]],
                    metric: str) -> tuple[list[str], list[list[str]]]:
    """Pure function. per_ticker maps ticker -> that company's FY facts for
    `metric`. Returns (headers, rows):
    headers = ["Ticker", <union of fiscal years ascending as "FY2022"...>]
    Each company gets two rows:
      ["LULU",        "$8,111M", "$9,619M", ...]
      ["LULU  YoY",   "N/A",     "+18.6%",  ...]
    Formatting: USD as "$9,619M" (value/1e6, comma separators); growth as
    signed percent with one decimal ("+18.6%", "-3.2%"); absent = "N/A"."""
```

## Test Cases (Write First)

```python
# tests/test_compare.py
from secpull.compare import yoy_growth, comparison_rows
from secpull.models import FinancialFact

def _fy(metric, value, fy, end):
    return FinancialFact(cik="0001397187", metric=metric, tag_used="x",
                         value=value, unit="USD", fiscal_year=fy,
                         fiscal_period="FY", form="10-K", end_date=end,
                         filed_date="2024-03-21")

LULU = [_fy("revenue", 8.111e9, 2022, "2023-01-29"),
        _fy("revenue", 9.619e9, 2023, "2024-01-28")]

def test_yoy_growth_basic():
    g = yoy_growth(LULU)
    assert g[2022] is None
    assert abs(g[2023] - 0.1859) < 0.001

def test_yoy_growth_gap_year_is_none():
    facts = [_fy("revenue", 5e9, 2020, "2021-01-31"),
             _fy("revenue", 9e9, 2023, "2024-01-28")]  # 2021–22 missing
    g = yoy_growth(facts)
    assert g[2023] is None   # prior year absent → never computed across gap

def test_comparison_rows_shapes_and_formats():
    headers, rows = comparison_rows({"LULU": LULU}, "revenue")
    assert headers == ["Ticker", "FY2022", "FY2023"]
    assert rows[0] == ["LULU", "$8,111M", "$9,619M"]
    assert rows[1][0] == "LULU  YoY"
    assert rows[1][1] == "N/A"
    assert rows[1][2] == "+18.6%"

def test_comparison_rows_union_of_years():
    other = [_fy("revenue", 4e9, 2023, "2023-12-31"),
             _fy("revenue", 5e9, 2024, "2024-12-31")]
    headers, _ = comparison_rows({"LULU": LULU, "XYZ": other}, "revenue")
    assert headers == ["Ticker", "FY2022", "FY2023", "FY2024"]
```

## Implementation Tasks

1. Implement `yoy_growth` and `comparison_rows` per docstrings (pure, tested first).
2. Add `compare` subcommand:
   `secpull compare LULU NKE GPS --metric revenue`
   - For each ticker not yet in the DB, run the full pull pipeline first
     (resolve → fetch/cache → extract → store), printing progress per ticker.
   - Then load FY facts for the metric, build rows, render with the existing
     `report.render_table`, and print the same Missing Data treatment.
   - Default `--metric revenue`; reject metrics not in `METRIC_TAGS` with exit 2.
3. Note the caveat in the CLI help text: fiscal years are **not aligned calendars**
   across companies (LULU's FY2023 ends Jan 2024; Nike's FY ends May). The table
   labels by fiscal year as reported, which is standard but worth knowing when
   eyeballing comps.
4. Full suite green; real run with 2–3 tickers; commit.

## Deliverable Checklist

- [ ] `yoy_growth` returns None for first year and across gaps — never interpolates
- [ ] `compare LULU NKE --metric revenue` pulls missing tickers automatically
- [ ] Growth formatted as signed percent, one decimal
- [ ] Unknown metric → exit code 2 with the valid metric list
- [ ] Fiscal-year caveat present in `--help`
- [ ] All tests pass; git commit made
