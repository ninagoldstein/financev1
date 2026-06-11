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
