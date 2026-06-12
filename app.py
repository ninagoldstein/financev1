"""
SEC Valuation Platform — Streamlit front-end.

Launch:
    streamlit run app.py

All finance/modeling logic lives in the secpull package. This file only
calls existing backend functions and handles UI presentation.
"""
from __future__ import annotations

import pathlib
import tempfile

import pandas as pd
import streamlit as st

import secpull.edgar as _edgar
from secpull.assumptions import build_assumptions_from_profile
from secpull.dcf import DCFInputs, build_dcf
from secpull.derived import compute_derived_metrics
from secpull.excel_export import export_workbook
from secpull.extract import extract_metrics
from secpull.forecast import build_forecast
from secpull.profile import build_profile
from secpull.statements import build_statements

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="SEC Valuation Platform", layout="wide")
st.title("SEC Valuation Platform")
st.markdown(
    "Generate financial statements, forecasts, DCF valuation, and Excel export "
    "from SEC filings."
)

# ── Sidebar inputs ────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Inputs")

    ticker = st.text_input("Ticker", value="LULU")
    wacc_pct = st.number_input(
        "WACC (%)", value=10.0, min_value=1.0, max_value=50.0, step=0.5
    )
    tgr_pct = st.number_input(
        "Terminal Growth Rate (%)", value=2.5, min_value=0.0, max_value=10.0, step=0.5
    )
    exit_multiple = st.number_input(
        "Exit EBITDA Multiple (x)", value=10.0, min_value=0.5, max_value=50.0, step=0.5
    )
    net_debt_raw = st.number_input(
        "Net Debt ($)",
        value=0,
        step=1_000_000,
        format="%d",
        help="Raw USD. Positive = net debt, negative = net cash.",
    )
    diluted_shares_raw = st.number_input(
        "Diluted Shares",
        value=125_000_000,
        min_value=1,
        step=1_000_000,
        format="%d",
        help="Actual share count (e.g. 125000000 for 125M shares).",
    )
    display_unit = st.selectbox(
        "Display Unit",
        options=["USD", "millions"],
        index=0,
        help="USD = raw values; millions = divide by 1,000,000 for display.",
    )
    run = st.button("Run Analysis", type="primary")

# ── Analysis workflow ─────────────────────────────────────────────────────────

if run:
    ticker_up = ticker.strip().upper()
    try:
        with st.spinner(f"Fetching SEC data for {ticker_up}…"):
            company, payload = _edgar.pull_and_cache(ticker_up)

        with st.spinner("Building financial model…"):
            facts   = extract_metrics(company.cik, payload)
            derived = compute_derived_metrics(company.cik, facts)
            stmts   = build_statements(
                company.cik, company.ticker, facts, derived, max_years=5
            )
            profile = build_profile(stmts, company.name)
            asmp    = build_assumptions_from_profile(profile)
            proj    = build_forecast(asmp, profile)
            inputs  = DCFInputs(
                wacc=wacc_pct / 100.0,
                terminal_growth_rate=tgr_pct / 100.0,
                exit_ebitda_multiple=exit_multiple,
                net_debt=float(net_debt_raw),
                diluted_shares=float(diluted_shares_raw),
            )
            dcf = build_dcf(proj, inputs)

        with st.spinner("Generating Excel workbook…"):
            tmp_path = pathlib.Path(tempfile.mktemp(suffix=".xlsx"))
            export_workbook(
                stmts, profile, asmp, proj, dcf,
                output_path=tmp_path,
                display_unit=display_unit,
            )
            excel_bytes = tmp_path.read_bytes()
            tmp_path.unlink(missing_ok=True)

        st.success(f"Analysis complete — {company.ticker}: {company.name}")

        st.session_state.update({
            "profile":      profile,
            "proj":         proj,
            "dcf":          dcf,
            "stmts":        stmts,
            "asmp":         asmp,
            "excel_bytes":  excel_bytes,
            "ticker_up":    company.ticker,
            "display_unit": display_unit,
        })

    except _edgar.TickerNotFound as exc:
        st.error(f"Ticker not found in SEC registry: {exc}")
    except _edgar.EdgarError as exc:
        st.error(f"SEC data fetch failed: {exc}")
    except Exception as exc:
        st.error(f"Analysis failed: {exc}")

# ── Display results ───────────────────────────────────────────────────────────

if "profile" not in st.session_state:
    st.stop()

profile      = st.session_state["profile"]
proj         = st.session_state["proj"]
dcf          = st.session_state["dcf"]
excel_bytes  = st.session_state["excel_bytes"]
ticker_up    = st.session_state["ticker_up"]
display_unit = st.session_state["display_unit"]

st.divider()

# ── A. Company Overview ───────────────────────────────────────────────────────

st.header("Company Overview")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Ticker",            profile.ticker)
c2.metric("Raw Coverage",      f"{profile.raw_coverage_pct:.1f}%")
c3.metric("Adjusted Coverage", f"{profile.adj_coverage_pct:.1f}%")
c4.metric("Quality Issues",    len(profile.quality_issues))

# ── B. Historical Profile ─────────────────────────────────────────────────────

st.header("Historical Profile")
c1, c2, c3, c4, c5 = st.columns(5)


def _pct(ratio) -> str:
    v = ratio.value if ratio is not None else None
    return f"{v:.1%}" if v is not None else "N/A"


def _xv(ratio) -> str:
    v = ratio.value if ratio is not None else None
    return f"{v:.2f}x" if v is not None else "N/A"


c1.metric("Revenue CAGR",      _pct(profile.revenue_cagr))
c2.metric("Avg EBIT Margin",   _pct(profile.avg_ebit_margin))
c3.metric("Avg EBITDA Margin", _pct(profile.avg_ebitda_margin))
c4.metric("Avg Net Margin",    _pct(profile.avg_net_margin))
c5.metric("Net Debt / EBITDA", _xv(profile.avg_net_debt_to_ebitda))

# ── C. DCF Summary ────────────────────────────────────────────────────────────

st.header("DCF Summary (Gordon Growth Model)")


def _usd_fmt(v: float | None) -> str:
    if v is None:
        return "N/A"
    if display_unit == "millions":
        return f"${v / 1_000_000:,.1f}M"
    return f"${v:,.0f}"


dcf_rows = []
for _name, _sdcf in [("Bear", dcf.bear), ("Base", dcf.base), ("Bull", dcf.bull)]:
    dcf_rows.append({
        "Scenario":         _name,
        "Enterprise Value": _usd_fmt(_sdcf.enterprise_value_gg),
        "Equity Value":     _usd_fmt(_sdcf.equity_value_gg),
        "Price Per Share":  (
            f"${_sdcf.price_per_share_gg:,.2f}"
            if _sdcf.price_per_share_gg is not None else "N/A"
        ),
    })

st.dataframe(pd.DataFrame(dcf_rows), use_container_width=True, hide_index=True)

# ── D. Quality Issues ─────────────────────────────────────────────────────────

st.header("Quality Issues")
qi_rows = [
    {"severity": qi.severity, "metric": qi.metric, "message": qi.message}
    for qi in profile.quality_issues
]
if qi_rows:
    st.dataframe(pd.DataFrame(qi_rows), use_container_width=True, hide_index=True)
else:
    st.info("No quality issues detected.")

# ── E. Excel Download ─────────────────────────────────────────────────────────

st.header("Export")
st.download_button(
    label="Download Excel DCF Workbook",
    data=excel_bytes,
    file_name=f"{ticker_up}_DCF.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
