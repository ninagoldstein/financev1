# SECPull — Phase C: Metric Extraction Logic

## What This Phase Builds

The heart of the project: pure functions that turn a raw EDGAR companyfacts payload
into clean `FinancialFact` records, handling inconsistent XBRL tags, duplicate
restatements, and non-calendar fiscal years. After this phase, `secpull pull LULU`
stores real financials in SQLite, and the worked-example test (LULU FY revenue
between $9.0B and $10.5B) passes.

## Depends On

Phases A (models, db) and B (client, fixture).

## Files Touched

- `secpull/extract.py` — created
- `secpull/cli.py` — modified (`pull` now extracts and stores after caching)
- `tests/test_extract.py` — created

## EDGAR Payload Shape (what extract.py consumes)

```json
{
  "cik": 1397187,
  "entityName": "lululemon athletica inc.",
  "facts": {
    "us-gaap": {
      "Revenues": {
        "units": {
          "USD": [
            {"end": "2024-01-28", "val": 9619000000, "fy": 2023,
             "fp": "FY", "form": "10-K", "filed": "2024-03-21",
             "start": "2023-01-30", "frame": "CY2023"}
          ]
        }
      }
    }
  }
}
```

Key facts about this shape:
- A metric may appear under several tags; use `METRIC_TAGS` fallback order
  (restated below) and take the **first tag that has any USD data**.
- The same (fy, fp, form, end) can appear multiple times when restated in later
  filings — keep only the one with the **latest `filed` date**.
- Annual data points have `fp == "FY"`; quarterly are `Q1`–`Q4`.
- Some data points lack `frame` — that's fine; we don't use it.
- EPS uses unit `"USD/shares"`, not `"USD"`.

```python
METRIC_TAGS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "total_assets": ["Assets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}
ACCEPTED_UNITS = {"eps_diluted": "USD/shares"}  # default "USD" for everything else
```

## Public Interface

```python
# secpull/extract.py
from secpull.models import FinancialFact

def pick_tag(facts_usgaap: dict, candidates: list[str], unit: str) -> str | None:
    """Return the first candidate tag that exists and has data under `unit`,
    else None. Pure function."""

def dedupe_latest_filed(points: list[dict]) -> list[dict]:
    """Given raw data points, keep one per (fy, fp, form, end) — the one with
    the latest `filed` date. Pure function."""

def extract_metrics(cik: str, payload: dict) -> list[FinancialFact]:
    """For every canonical metric in METRIC_TAGS, pick a tag, dedupe, and emit
    FinancialFact records. Skips data points missing any of: val, fy, fp, form,
    end, filed. Restricts forms to {"10-K", "10-Q"}. Never raises on missing
    metrics — a metric with no tag simply produces no records."""
```

## Test Cases (Write First)

```python
# tests/test_extract.py
from secpull.extract import pick_tag, dedupe_latest_filed, extract_metrics

def _pt(end, val, fy, fp, form, filed):
    return {"end": end, "val": val, "fy": fy, "fp": fp,
            "form": form, "filed": filed}

def test_pick_tag_fallback_order():
    facts = {"Revenues": {"units": {"USD": [_pt("2024-01-28", 1, 2023, "FY",
                                               "10-K", "2024-03-21")]}}}
    tags = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]
    assert pick_tag(facts, tags, "USD") == "Revenues"

def test_pick_tag_none_when_absent():
    assert pick_tag({}, ["Revenues"], "USD") is None

def test_dedupe_keeps_latest_filed():
    a = _pt("2024-01-28", 9.0e9, 2023, "FY", "10-K", "2024-03-21")
    b = _pt("2024-01-28", 9.6e9, 2023, "FY", "10-K", "2025-03-20")  # restated
    out = dedupe_latest_filed([a, b])
    assert out == [b]

def test_extract_skips_incomplete_points():
    payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"end": "2024-01-28", "val": 1.55e9}  # missing fy/fp/form/filed
    ]}}}}}
    assert extract_metrics("0001397187", payload) == []

def test_extract_restricts_forms():
    payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        _pt("2024-01-28", 1.55e9, 2023, "FY", "10-K/A", "2024-05-01"),
    ]}}}}}
    assert extract_metrics("0001397187", payload) == []

# ---- Worked example (primary validation for the whole project) ----
def test_lulu_fy_revenue_in_range(lulu_companyfacts):
    facts = extract_metrics("0001397187", lulu_companyfacts)
    fy_rev = [f for f in facts
              if f.metric == "revenue" and f.fiscal_period == "FY"
              and f.end_date == "2024-01-28"]
    assert len(fy_rev) == 1
    assert 9.0e9 <= fy_rev[0].value <= 1.05e10
    assert fy_rev[0].unit == "USD"
    assert fy_rev[0].form == "10-K"

def test_lulu_eps_uses_usd_per_share(lulu_companyfacts):
    facts = extract_metrics("0001397187", lulu_companyfacts)
    eps = [f for f in facts if f.metric == "eps_diluted"]
    assert eps and all(f.unit == "USD/shares" for f in eps)
```

## Implementation Tasks

1. Implement `pick_tag` exactly per the docstring. No network, no DB.
2. Implement `dedupe_latest_filed` using a dict keyed on `(fy, fp, form, end)`,
   replacing entries when a later `filed` is seen. Compare `filed` as ISO strings
   (lexicographic comparison is safe for ISO dates).
3. Implement `extract_metrics`:
   - For each metric: unit = `ACCEPTED_UNITS.get(metric, "USD")`; tag = `pick_tag(...)`;
     skip metric if tag is None.
   - Filter points: form in `{"10-K", "10-Q"}`, and all of
     `val/fy/fp/form/end/filed` present.
   - Dedupe, then build `FinancialFact` records (`tag_used` = the chosen tag,
     `fiscal_year` = `fy` as int, `value` = `val` as float).
4. Wire `pull` in `cli.py`: after caching (Phase B), call `extract_metrics`, then
   `db.insert_facts`. Print `Stored N financial facts for <TICKER> (M new).` where
   N is total extracted and M is the insert count.
5. Run the full suite. Then run a real `pull LULU` and spot-check the DB:
   `sqlite3 data/secpull.db "SELECT metric, fiscal_year, fiscal_period, value
   FROM financials ORDER BY metric, fiscal_year"` — verify FY revenue looks right
   against Lululemon's actual reported figures. Commit.

## Deliverable Checklist

- [ ] `pick_tag` honors fallback order and returns None when nothing matches
- [ ] Restated figures resolve to the latest-filed value
- [ ] Points missing required fields are skipped silently (never guessed)
- [ ] Only 10-K and 10-Q forms are ingested
- [ ] Worked-example test passes (LULU FY revenue in $9.0B–$10.5B range)
- [ ] `pull LULU` against real EDGAR stores rows in `financials`
- [ ] All tests pass; git commit made
