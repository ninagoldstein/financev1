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


def test_pick_tag_prefers_more_recent_annual():
    """Tag with newer annual 10-K data wins even when listed second."""
    facts = {
        "OldTag": {"units": {"USD": [_pt("2020-12-31", 1.0e9, 2020, "FY", "10-K", "2021-02-01")]}},
        "NewTag": {"units": {"USD": [_pt("2024-12-31", 2.0e9, 2024, "FY", "10-K", "2025-02-01")]}},
    }
    assert pick_tag(facts, ["OldTag", "NewTag"], "USD") == "NewTag"


def test_pick_tag_order_breaks_tie_when_same_recency():
    """First-listed candidate wins when both share the same latest annual date."""
    facts = {
        "TagA": {"units": {"USD": [_pt("2024-12-31", 1.0e9, 2024, "FY", "10-K", "2025-02-01")]}},
        "TagB": {"units": {"USD": [_pt("2024-12-31", 2.0e9, 2024, "FY", "10-K", "2025-02-01")]}},
    }
    assert pick_tag(facts, ["TagA", "TagB"], "USD") == "TagA"


def test_pick_tag_meta_net_income_pattern():
    """META: first candidate has FY2020 data only; second has FY2025 — second wins."""
    facts = {
        "NetIncomeLossAvailableToCommonStockholdersBasic": {
            "units": {"USD": [_pt("2020-12-31", 29.0e9, 2020, "FY", "10-K", "2021-01-28")]}
        },
        "NetIncomeLoss": {
            "units": {"USD": [_pt("2025-12-31", 60.5e9, 2025, "FY", "10-K", "2026-01-29")]}
        },
    }
    tag = pick_tag(
        facts,
        ["NetIncomeLossAvailableToCommonStockholdersBasic", "NetIncomeLoss"],
        "USD",
    )
    assert tag == "NetIncomeLoss"


def test_pick_tag_ford_net_income_pattern():
    """Ford: first candidate has FY2025 data; second stopped at FY2023 — first wins."""
    facts = {
        "NetIncomeLossAvailableToCommonStockholdersBasic": {
            "units": {"USD": [_pt("2025-12-31", -8.182e9, 2025, "FY", "10-K", "2026-02-01")]}
        },
        "NetIncomeLoss": {
            "units": {"USD": [_pt("2023-12-31", 4.3e9, 2023, "FY", "10-K", "2024-02-06")]}
        },
    }
    tag = pick_tag(
        facts,
        ["NetIncomeLossAvailableToCommonStockholdersBasic", "NetIncomeLoss"],
        "USD",
    )
    assert tag == "NetIncomeLossAvailableToCommonStockholdersBasic"


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


# ---- fiscal_year derivation and cross-filing dedup ----

def test_fiscal_year_derived_from_jan_end():
    # fy=2025 is a comparative label from a later filing; fiscal_year must come from end_date
    payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        _pt("2024-01-28", 1.55e9, 2025, "FY", "10-K", "2026-03-17"),
    ]}}}}}
    facts = extract_metrics("0001397187", payload)
    assert len(facts) == 1
    assert facts[0].fiscal_year == 2023   # month=1 → 2024 - 1


def test_fiscal_year_derived_from_dec_end():
    payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        _pt("2023-12-31", 1.0e9, 2024, "FY", "10-K", "2025-02-01"),
    ]}}}}}
    facts = extract_metrics("0001397187", payload)
    assert len(facts) == 1
    assert facts[0].fiscal_year == 2023   # month=12 → year unchanged


def test_dedupe_collapses_cross_filing_comparatives():
    # Same period appearing with fy=2023 (original) and fy=2024 (comparative) must yield one record
    original    = _pt("2024-01-28", 9.619e9, 2023, "FY", "10-K", "2024-03-21")
    comparative = _pt("2024-01-28", 9.619e9, 2024, "FY", "10-K", "2025-03-27")
    payload = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [original, comparative]}}}}}
    facts = extract_metrics("0001397187", payload)
    rev = [f for f in facts if f.metric == "revenue" and f.end_date == "2024-01-28"]
    assert len(rev) == 1
    assert rev[0].fiscal_year == 2023


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
