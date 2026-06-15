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
from secpull.market_calibration import calibrate as _calibrate
from secpull.normalize import MODES
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
        value=0,
        min_value=0,
        step=1_000_000,
        format="%d",
        help="Actual share count. Leave 0 to auto-detect from SEC filings.",
    )
    market_price_input = st.number_input(
        "Current Share Price ($)",
        value=0.0,
        min_value=0.0,
        step=1.0,
        format="%.2f",
        help="Enter current market price to enable Market Calibration. Leave 0 to skip.",
    )
    display_unit = st.selectbox(
        "Display Unit",
        options=["USD", "millions"],
        index=0,
        help="USD = raw values; millions = divide by 1,000,000 for display.",
    )

    with st.expander("Assumption Modes (Advanced)"):
        st.caption(
            "Override how each driver is selected from its historical series. "
            "Default is 'normalized' (outlier-adjusted average)."
        )
        _mode_options = list(MODES)
        mode_rg  = st.selectbox("Revenue Growth Mode",  options=_mode_options, index=0, key="mode_rg")
        mode_em  = st.selectbox("EBIT Margin Mode",     options=_mode_options, index=0, key="mode_em")
        mode_da  = st.selectbox("D&A % Revenue Mode",   options=_mode_options, index=0, key="mode_da")
        mode_cx  = st.selectbox("Capex % Revenue Mode", options=_mode_options, index=0, key="mode_cx")
        mode_tr  = st.selectbox("Tax Rate Mode",         options=_mode_options, index=0, key="mode_tr")
        mode_nwc = st.selectbox("NWC % Revenue Mode",   options=_mode_options, index=0, key="mode_nwc")
        capex_manual_pct = st.number_input(
            "Capex Manual Value (%)",
            value=15.5,
            min_value=0.0,
            max_value=30.0,
            step=0.5,
            help="Used only when Capex % Revenue Mode = manual_override.",
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

            # Build assumption modes dict from sidebar selectors
            _assumption_modes: dict[str, tuple[str, float | None]] = {}
            for _driver, _mode in [
                ("revenue_growth",     mode_rg),
                ("ebit_margin",        mode_em),
                ("da_pct_revenue",     mode_da),
                ("capex_pct_revenue",  mode_cx),
                ("effective_tax_rate", mode_tr),
                ("nwc_pct_revenue",    mode_nwc),
            ]:
                if _mode != "normalized":
                    _manual = (capex_manual_pct / 100.0
                               if _driver == "capex_pct_revenue" and _mode == "manual_override"
                               else None)
                    _assumption_modes[_driver] = (_mode, _manual)

            asmp = build_assumptions_from_profile(
                profile,
                modes=_assumption_modes or None,
            )
            proj    = build_forecast(asmp, profile)

            # Resolve diluted shares: use sidebar override if set, else auto-detect
            # from the most recent FY shares_diluted fact extracted from SEC filings.
            if diluted_shares_raw != 0:
                resolved_shares = float(diluted_shares_raw)
            else:
                _sf = sorted(
                    [f for f in facts
                     if f.metric == "shares_diluted" and f.fiscal_period == "FY"],
                    key=lambda f: f.fiscal_year, reverse=True,
                )
                resolved_shares = float(_sf[0].value) if _sf else None

            inputs  = DCFInputs(
                wacc=wacc_pct / 100.0,
                terminal_growth_rate=tgr_pct / 100.0,
                exit_ebitda_multiple=exit_multiple,
                net_debt=float(net_debt_raw),
                diluted_shares=resolved_shares,
            )
            dcf = build_dcf(proj, inputs)

            # Market calibration (optional — requires a non-zero market price)
            market_cal = None
            if market_price_input > 0 and resolved_shares and resolved_shares > 0:
                try:
                    market_cal = _calibrate(
                        proj, inputs, dcf,
                        market_price_per_share=market_price_input,
                    )
                except Exception:
                    market_cal = None  # non-fatal

        with st.spinner("Generating Excel workbook…"):
            tmp_path = pathlib.Path(tempfile.mktemp(suffix=".xlsx"))
            export_workbook(
                stmts, profile, asmp, proj, dcf,
                output_path=tmp_path,
                display_unit=display_unit,
                market_cal=market_cal,
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
            "market_cal":   market_cal,
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

# ── E. Market Calibration ─────────────────────────────────────────────────────

market_cal = st.session_state.get("market_cal")

if market_cal is not None:
    st.header("Market Calibration")

    _mcol1, _mcol2, _mcol3 = st.columns(3)
    _base_pps = dcf.base.price_per_share_gg
    _market_pps = market_cal.market_equity_value / dcf.inputs.diluted_shares if (
        dcf.inputs.diluted_shares
    ) else None

    def _pps_str(v) -> str:
        return f"${v:,.2f}" if v is not None else "N/A"

    _mcol1.metric("Model Price / Share", _pps_str(_base_pps))
    _mcol2.metric("Market Price / Share", _pps_str(_market_pps))
    if _base_pps and _market_pps and _base_pps > 0:
        _updown = (_market_pps / _base_pps - 1)
        _mcol3.metric("Market vs Model", f"{_updown:+.1%}",
                       delta=f"{_updown:+.1%}", delta_color="normal")

    st.markdown(
        f"**Model EV:** {_usd_fmt(market_cal.model_enterprise_value)}  |  "
        f"**Market EV:** {_usd_fmt(market_cal.market_enterprise_value)}  |  "
        f"**Gap:** {_usd_fmt(market_cal.ev_gap)} "
        f"({market_cal.ev_gap_pct:+.1%})"
    )

    _implied_rows = [
        {"Assumption": "WACC",
         "Model Value": f"{dcf.inputs.wacc:.2%}",
         "Implied by Market": (f"{market_cal.implied_wacc:.2%}"
                               if market_cal.implied_wacc is not None
                               else "No solution in range")},
        {"Assumption": "Terminal Growth Rate",
         "Model Value": f"{dcf.inputs.terminal_growth_rate:.2%}",
         "Implied by Market": (f"{market_cal.implied_terminal_growth_rate:.2%}"
                               if market_cal.implied_terminal_growth_rate is not None
                               else "No solution in range")},
        {"Assumption": "Exit EBITDA Multiple",
         "Model Value": (f"{dcf.inputs.exit_ebitda_multiple:.1f}x"
                         if dcf.inputs.exit_ebitda_multiple else "N/A"),
         "Implied by Market": (f"{market_cal.implied_exit_ebitda_multiple:.1f}x"
                               if market_cal.implied_exit_ebitda_multiple is not None
                               else "No solution in range")},
        {"Assumption": "Capex % Revenue",
         "Model Value": (f"{st.session_state['asmp'].capex_pct_revenue:.1%}"
                         if st.session_state.get("asmp") else "N/A"),
         "Implied by Market": (f"{market_cal.implied_capex_pct_revenue:.1%}"
                               if market_cal.implied_capex_pct_revenue is not None
                               else "No solution in range")},
    ]
    st.dataframe(pd.DataFrame(_implied_rows), use_container_width=True, hide_index=True)

    if market_cal.notes:
        with st.expander("Solver Notes"):
            for _note in market_cal.notes:
                st.caption(_note)
elif market_price_input == 0:
    st.info("Enter a Current Share Price in the sidebar to enable Market Calibration.")

# ── G. Assumption Audit ───────────────────────────────────────────────────────

st.header("Assumption Audit")
asmp = st.session_state["asmp"]

_audit_drivers = [
    ("Revenue Growth",  asmp.revenue_growth_detail),
    ("EBIT Margin",     asmp.ebit_margin_detail),
    ("D&A % Revenue",   asmp.da_pct_detail),
    ("Capex % Revenue", asmp.capex_pct_detail),
    ("Tax Rate",        asmp.tax_rate_detail),
    ("NWC % Revenue",   asmp.nwc_pct_detail),
]

def _pf(v) -> str:
    return f"{v:.1%}" if v is not None else "N/A"


_audit_rows = []
for _label, _detail in _audit_drivers:
    if _detail is None:
        continue
    _audit_rows.append({
        "Driver":         _label,
        "Mode":           _detail.mode,
        "Selected":       _pf(_detail.selected_value),
        "5yr":            _pf(_detail.historical_5y),
        "3yr":            _pf(_detail.historical_3y),
        "2yr":            _pf(_detail.historical_2y),
        "Most Recent":    _pf(_detail.most_recent),
        "Normalized":     _pf(_detail.normalized),
        "Outlier Years":  (", ".join(f"FY{y}" for y in _detail.outlier_years)
                           if _detail.outlier_years else "—"),
        "Manual Value":   _pf(_detail.manual_value) if _detail.manual_value is not None else "—",
        "Rationale":      _detail.rationale,
    })

if _audit_rows:
    st.dataframe(pd.DataFrame(_audit_rows), use_container_width=True, hide_index=True)

# ── H. Excel Download ─────────────────────────────────────────────────────────

st.header("Export")
st.download_button(
    label="Download Excel DCF Workbook",
    data=excel_bytes,
    file_name=f"{ticker_up}_DCF.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
