"""Tests for app.py — Streamlit SEC Valuation Platform."""
import json
import pathlib
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from secpull import config
from secpull.models import Company

# ── Constants ─────────────────────────────────────────────────────────────────

_LULU_CIK = "0001397187"
_APP_PATH  = str(pathlib.Path(__file__).parent.parent / "app.py")


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def lulu_payload() -> dict:
    with open(config.DATA_DIR / "raw" / f"{_LULU_CIK}.json") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def lulu_company() -> Company:
    return Company(cik=_LULU_CIK, ticker="LULU", name="lululemon athletica inc.")


@pytest.fixture(scope="module")
def analyzed_at(lulu_company, lulu_payload):
    """AppTest with a completed LULU analysis (analysis runs once per module)."""
    at = AppTest.from_file(_APP_PATH, default_timeout=60)
    with patch("secpull.edgar.pull_and_cache", return_value=(lulu_company, lulu_payload)):
        at.run()
        at.sidebar.button[0].click().run()
    return at


# ── Basic UI tests (no SEC calls) ─────────────────────────────────────────────

def test_app_loads_without_exception():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert not at.exception


def test_page_title_present():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert any("SEC Valuation Platform" in str(t.value) for t in at.title)


def test_subtitle_present():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    all_text = " ".join(str(m.value) for m in at.markdown)
    assert "SEC filings" in all_text


def test_sidebar_ticker_default():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert at.sidebar.text_input[0].value == "LULU"


def test_sidebar_wacc_default():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert at.sidebar.number_input[0].value == 10.0


def test_sidebar_tgr_default():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert at.sidebar.number_input[1].value == 2.5


def test_sidebar_exit_multiple_default():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert at.sidebar.number_input[2].value == 10.0


def test_sidebar_diluted_shares_default():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert at.sidebar.number_input[4].value == 125_000_000


def test_sidebar_display_unit_options():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    opts = at.sidebar.selectbox[0].options
    assert "USD" in opts
    assert "millions" in opts


def test_sidebar_display_unit_default_usd():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    assert at.sidebar.selectbox[0].value == "USD"


def test_sidebar_run_button_exists():
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    at.run()
    labels = [b.label for b in at.sidebar.button]
    assert any("Run" in lbl or "Analysis" in lbl for lbl in labels)


# ── Analysis flow tests (mocked SEC call) ─────────────────────────────────────

def test_analysis_runs_without_exception(analyzed_at):
    assert not analyzed_at.exception


def test_analysis_shows_success_message(analyzed_at):
    assert len(analyzed_at.success) >= 1
    assert any("LULU" in str(s.value) for s in analyzed_at.success)


def test_company_overview_has_four_metrics(analyzed_at):
    # Ticker, Raw Coverage, Adjusted Coverage, Quality Issues
    assert len(analyzed_at.metric) >= 4


def test_company_overview_ticker_metric(analyzed_at):
    ticker_metrics = [m for m in analyzed_at.metric if m.label == "Ticker"]
    assert len(ticker_metrics) == 1
    assert ticker_metrics[0].value == "LULU"


def test_company_overview_raw_coverage_metric(analyzed_at):
    cov = [m for m in analyzed_at.metric if m.label == "Raw Coverage"]
    assert len(cov) == 1
    assert "%" in str(cov[0].value)


def test_historical_profile_has_five_metrics(analyzed_at):
    # Revenue CAGR + 4 margin/leverage metrics
    assert len(analyzed_at.metric) >= 9  # 4 overview + 5 historical


def test_historical_revenue_cagr_metric(analyzed_at):
    metrics = {m.label: m.value for m in analyzed_at.metric}
    assert "Revenue CAGR" in metrics
    assert "%" in str(metrics["Revenue CAGR"]) or metrics["Revenue CAGR"] == "N/A"


def test_dcf_summary_dataframe_appears(analyzed_at):
    assert len(analyzed_at.dataframe) >= 1


def test_dcf_dataframe_has_three_scenarios(analyzed_at):
    df = analyzed_at.dataframe[0].value
    assert len(df) == 3
    assert list(df["Scenario"]) == ["Bear", "Base", "Bull"]


def test_dcf_dataframe_has_price_per_share_column(analyzed_at):
    df = analyzed_at.dataframe[0].value
    assert "Price Per Share" in df.columns


def test_dcf_price_per_share_looks_like_dollars(analyzed_at):
    df = analyzed_at.dataframe[0].value
    base_price = df.loc[df["Scenario"] == "Base", "Price Per Share"].iloc[0]
    assert "$" in str(base_price)


def test_quality_issues_dataframe_appears(analyzed_at):
    # DCF table + quality issues table
    assert len(analyzed_at.dataframe) >= 2


def test_quality_issues_dataframe_has_severity_column(analyzed_at):
    qi_df = analyzed_at.dataframe[1].value
    assert "severity" in qi_df.columns
    assert "metric" in qi_df.columns
    assert "message" in qi_df.columns


def test_excel_bytes_generated(analyzed_at):
    """Excel workbook bytes are stored in session state after analysis."""
    excel_bytes = analyzed_at.session_state["excel_bytes"]
    assert isinstance(excel_bytes, bytes)
    assert len(excel_bytes) > 0


def test_excel_bytes_valid_xlsx(analyzed_at):
    """Generated bytes start with the OOXML/ZIP magic bytes."""
    excel_bytes = analyzed_at.session_state["excel_bytes"]
    assert excel_bytes[:4] == b"PK\x03\x04"   # ZIP magic — xlsx is a ZIP archive


def test_excel_filename_in_session_state(analyzed_at):
    ticker_up = analyzed_at.session_state["ticker_up"]
    assert ticker_up == "LULU"


# ── Error handling ────────────────────────────────────────────────────────────

def test_error_shown_on_ticker_not_found():
    from secpull.edgar import TickerNotFound
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    with patch("secpull.edgar.pull_and_cache", side_effect=TickerNotFound("XXXX")):
        at.run()
        at.sidebar.text_input[0].set_value("XXXX")
        at.sidebar.button[0].click().run()
    assert not at.exception
    assert len(at.error) >= 1
    assert any("not found" in str(e.value).lower() for e in at.error)


def test_error_shown_on_edgar_error():
    from secpull.edgar import EdgarError
    at = AppTest.from_file(_APP_PATH, default_timeout=10)
    with patch("secpull.edgar.pull_and_cache", side_effect=EdgarError("HTTP 503")):
        at.run()
        at.sidebar.button[0].click().run()
    assert not at.exception
    assert len(at.error) >= 1


# ── Display-unit switching ────────────────────────────────────────────────────

def test_millions_display_unit_runs_without_error(lulu_company, lulu_payload):
    at = AppTest.from_file(_APP_PATH, default_timeout=60)
    with patch("secpull.edgar.pull_and_cache", return_value=(lulu_company, lulu_payload)):
        at.run()
        at.sidebar.selectbox[0].set_value("millions")
        at.sidebar.button[0].click().run()
    assert not at.exception
    assert len(at.success) >= 1


def test_millions_display_shows_m_suffix(lulu_company, lulu_payload):
    at = AppTest.from_file(_APP_PATH, default_timeout=60)
    with patch("secpull.edgar.pull_and_cache", return_value=(lulu_company, lulu_payload)):
        at.run()
        at.sidebar.selectbox[0].set_value("millions")
        at.sidebar.button[0].click().run()
    df = at.dataframe[0].value
    ev_base = df.loc[df["Scenario"] == "Base", "Enterprise Value"].iloc[0]
    assert "M" in str(ev_base)
