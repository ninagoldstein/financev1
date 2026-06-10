# SECPull — Phase D: Reports, Missing-Data Detection, and Excel Export

## What This Phase Builds

The output layer. `secpull report LULU` prints a clean terminal table of metrics by
period with explicit `N/A` markers and a Missing Data section. `secpull export LULU`
writes `data/exports/LULU.xlsx` with one formatted sheet of the same data — the
foundation Nina can later build bull/bear/base model tabs on top of. This phase also
deletes the Phase A CLI stub test.

## Depends On

Phases A–C (facts are in SQLite).

## Files Touched

- `secpull/report.py` — created
- `secpull/export.py` — created
- `secpull/cli.py` — modified (`report` and `export` implemented)
- `tests/test_report.py` — created
- `tests/test_cli_stub.py` — deleted

## Key Definitions (restated)

`FinancialFact` fields used here: `metric`, `value`, `unit`, `fiscal_year`,
`fiscal_period` ("FY", "Q1"–"Q4"), `end_date`. Canonical metric order for display:

```python
DISPLAY_ORDER = ["revenue", "gross_profit", "operating_income", "net_income",
                 "eps_diluted", "total_assets", "cash"]
```

## Public Interface

```python
# secpull/report.py
from secpull.models import FinancialFact

def build_grid(facts: list[FinancialFact],
               periods: str = "FY") -> tuple[list[str], list[list[str]]]:
    """Pure function. periods is "FY" (annual only) or "Q" (quarterly only).
    Returns (column_headers, rows).
    - Columns: ["Metric", <period labels sorted ascending>], where an annual
      label is "FY2023 (end 2024-01-28)" and a quarterly label is "2024 Q1".
    - One row per metric in DISPLAY_ORDER that has at least one value anywhere.
    - Cell formatting: USD values >= 1e6 rendered as "$9,619M" (value/1e6,
      thousands separators, no decimals); "USD/shares" rendered as "$12.20"
      (two decimals); absent values rendered exactly as "N/A".
    """

def find_missing(facts: list[FinancialFact],
                 periods: str = "FY") -> list[tuple[str, str]]:
    """Pure function. Returns (metric, period_label) pairs that are N/A in the
    grid — i.e., the metric has data for some periods but not this one.
    Sorted by metric then label."""

def render_table(headers: list[str], rows: list[list[str]]) -> str:
    """Plain-text table with aligned columns. No external dependencies."""
```

```python
# secpull/export.py
def export_xlsx(ticker: str, headers: list[str], rows: list[list[str]],
                missing: list[tuple[str, str]], out_path) -> None:
    """Write one workbook: sheet "Financials" containing the grid starting at
    A1, bold header row, frozen top row; below the grid, two blank rows, then
    a "Missing Data" header and one (metric, period) per row. Uses openpyxl."""
```

## Test Cases (Write First)

```python
# tests/test_report.py
from secpull.models import FinancialFact
from secpull.report import build_grid, find_missing

def _fact(metric, value, fy, fp, end, unit="USD"):
    return FinancialFact(cik="0001397187", metric=metric, tag_used="x",
                         value=value, unit=unit, fiscal_year=fy,
                         fiscal_period=fp, form="10-K", end_date=end,
                         filed_date="2024-03-21")

FACTS = [
    _fact("revenue",    9.619e9, 2023, "FY", "2024-01-28"),
    _fact("revenue",    8.111e9, 2022, "FY", "2023-01-29"),
    _fact("net_income", 1.550e9, 2023, "FY", "2024-01-28"),
    # note: net_income missing for FY2022 on purpose
    _fact("eps_diluted", 12.20,  2023, "FY", "2024-01-28", unit="USD/shares"),
]

def test_grid_headers_sorted_ascending():
    headers, _ = build_grid(FACTS, periods="FY")
    assert headers == ["Metric", "FY2022 (end 2023-01-29)",
                       "FY2023 (end 2024-01-28)"]

def test_grid_formats_usd_millions():
    _, rows = build_grid(FACTS, periods="FY")
    revenue_row = next(r for r in rows if r[0] == "revenue")
    assert revenue_row[2] == "$9,619M"

def test_grid_formats_eps_two_decimals():
    _, rows = build_grid(FACTS, periods="FY")
    eps_row = next(r for r in rows if r[0] == "eps_diluted")
    assert eps_row[2] == "$12.20"

def test_grid_absent_value_is_na_never_a_number():
    _, rows = build_grid(FACTS, periods="FY")
    ni_row = next(r for r in rows if r[0] == "net_income")
    assert ni_row[1] == "N/A"

def test_find_missing_lists_gaps():
    missing = find_missing(FACTS, periods="FY")
    assert ("net_income", "FY2022 (end 2023-01-29)") in missing
    assert ("eps_diluted", "FY2022 (end 2023-01-29)") in missing

def test_metric_with_no_data_excluded_entirely():
    _, rows = build_grid(FACTS, periods="FY")
    assert not any(r[0] == "cash" for r in rows)
```

```python
# tests/test_report.py (continued) — export smoke test
from secpull.export import export_xlsx
from secpull.report import build_grid, find_missing
from openpyxl import load_workbook

def test_export_xlsx_roundtrip(tmp_path):
    headers, rows = build_grid(FACTS, periods="FY")
    out = tmp_path / "LULU.xlsx"
    export_xlsx("LULU", headers, rows, find_missing(FACTS), out)
    wb = load_workbook(out)
    ws = wb["Financials"]
    assert ws["A1"].value == "Metric"
    assert any(c.value == "Missing Data"
               for row in ws.iter_rows() for c in row)
```

## Implementation Tasks

1. Implement `build_grid`, `find_missing`, `render_table` as pure functions per
   the docstrings. Period labels: annual = `f"FY{fy} (end {end_date})"`,
   quarterly = `f"{fy} {fp}"`. Sort labels by `end_date` where available,
   else by (fy, fp).
2. Implement `export_xlsx` per docstring (bold header via `Font(bold=True)`,
   `freeze_panes = "A2"`).
3. Wire `report` in `cli.py`: read facts via `db.get_facts`, support a
   `--quarterly` flag (periods="Q"), print the table, then a `Missing Data:`
   section (or `Missing Data: none` if empty). If the ticker has never been
   pulled, print `No data for <TICKER>. Run: secpull pull <TICKER>` and exit 1.
4. Wire `export` in `cli.py`: build grid + missing, write to
   `data/exports/<TICKER>.xlsx` (create dir), print the path.
5. Delete `tests/test_cli_stub.py`. Run full suite, then a real end-to-end:
   `pull LULU`, `report LULU`, `export LULU`, open the xlsx and eyeball it. Commit.

## Deliverable Checklist

- [ ] `report LULU` prints aligned table; absent values show exactly `N/A`
- [ ] Missing Data section lists every (metric, period) gap
- [ ] `--quarterly` flag switches to Q1–Q4 view
- [ ] Un-pulled ticker → helpful message, exit code 1
- [ ] `export LULU` produces an openable xlsx with Financials sheet + Missing Data
- [ ] No number is ever printed for a period EDGAR didn't report
- [ ] All tests pass; stub test deleted; git commit made
