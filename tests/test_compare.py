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
             _fy("revenue", 9e9, 2023, "2024-01-28")]   # 2021–22 missing
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
