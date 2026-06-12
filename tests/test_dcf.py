"""Tests for secpull/dcf.py."""
import json
import math
import pytest

from secpull import config
from secpull.assumptions import build_assumptions_from_profile
from secpull.dcf import DCFInputs, DCFResult, ScenarioDCF, build_dcf
from secpull.derived import compute_derived_metrics
from secpull.extract import extract_metrics
from secpull.forecast import (
    ForecastYear,
    ProjectedStatements,
    ScenarioForecast,
    build_forecast,
)
from secpull.models import FinancialFact
from secpull.profile import build_profile
from secpull.quality import COMPLETE, QualityIssue
from secpull.statements import build_statements

# ── Synthetic helpers ─────────────────────────────────────────────────────────

_CIK = "0001111111"
_TICKER = "TST"


def _f(metric, fy, value, quality=COMPLETE, filed="2026-02-01"):
    return FinancialFact(
        cik=_CIK, metric=metric, tag_used="Tag",
        value=value, unit="USD",
        fiscal_year=fy, fiscal_period="FY",
        form="10-K", end_date=f"{fy + 1}-01-31", filed_date=filed,
        coverage_quality=quality,
    )


def _make_year(n: int, fcff: float, ebitda: float, revenue: float = 100.0) -> ForecastYear:
    """ForecastYear with specified FCFF and EBITDA; other fields are plausible."""
    ebit = ebitda * 0.80
    da = ebitda - ebit
    return ForecastYear(
        year=2025 + n,
        revenue=revenue,
        gross_profit=None,
        ebit=ebit,
        da=da,
        ebitda=ebitda,
        interest_expense=None,
        ebt=None,
        tax_expense=None,
        net_income=ebit * 0.75,
        capex=revenue * 0.06,
        delta_nwc=0.0,
        fcff=fcff,
    )


def _uniform_scenario(
    name: str,
    fcff: float,
    ebitda_final: float,
    n_years: int = 5,
    growth_rate: float = 0.10,
) -> ScenarioForecast:
    """Scenario with constant FCFF each year and a specified final EBITDA."""
    years = tuple(
        _make_year(
            i + 1,
            fcff=fcff,
            ebitda=ebitda_final if i == n_years - 1 else ebitda_final * 0.90,
            revenue=100.0 * (1.10 ** (i + 1)),
        )
        for i in range(n_years)
    )
    return ScenarioForecast(scenario=name, growth_rate=growth_rate, years=years)


def _simple_projected(
    fcff: float = 100.0,
    ebitda_final: float = 250.0,
    n_years: int = 5,
    ticker: str = _TICKER,
    quality_issues: tuple = (),
) -> ProjectedStatements:
    """ProjectedStatements with identical FCFF across all three scenarios."""
    return ProjectedStatements(
        ticker=ticker,
        base_year=2025,
        base_revenue=100.0,
        bear=_uniform_scenario("bear", fcff, ebitda_final, n_years, 0.07),
        base=_uniform_scenario("base", fcff, ebitda_final, n_years, 0.10),
        bull=_uniform_scenario("bull", fcff, ebitda_final, n_years, 0.13),
        quality_issues=quality_issues,
    )


def _std_inputs(**kwargs) -> DCFInputs:
    defaults = dict(
        wacc=0.10,
        terminal_growth_rate=0.025,
        exit_ebitda_multiple=None,
        net_debt=0.0,
        diluted_shares_m=None,
    )
    defaults.update(kwargs)
    return DCFInputs(**defaults)


# ── Shape / structure ─────────────────────────────────────────────────────────

def test_dcf_result_fields():
    ps = _simple_projected()
    result = build_dcf(ps, _std_inputs())
    assert isinstance(result, DCFResult)
    assert result.ticker == _TICKER
    for attr in ("bear", "base", "bull", "inputs", "quality_issues"):
        assert hasattr(result, attr)


def test_scenario_dcf_fields():
    ps = _simple_projected()
    result = build_dcf(ps, _std_inputs())
    s = result.base
    for attr in (
        "scenario", "pv_fcffs", "sum_pv_fcff",
        "terminal_value_gg", "pv_terminal_value_gg",
        "enterprise_value_gg", "equity_value_gg", "price_per_share_gg",
        "terminal_value_exit", "pv_terminal_value_exit",
        "enterprise_value_exit", "equity_value_exit", "price_per_share_exit",
    ):
        assert hasattr(s, attr)


def test_dcf_result_is_frozen():
    ps = _simple_projected()
    result = build_dcf(ps, _std_inputs())
    with pytest.raises((AttributeError, TypeError)):
        result.ticker = "XYZ"  # type: ignore[misc]


def test_pv_fcffs_length_matches_projection_years():
    ps = _simple_projected(n_years=5)
    result = build_dcf(ps, _std_inputs())
    assert len(result.base.pv_fcffs) == 5


# ── Discounting math ──────────────────────────────────────────────────────────

def test_pv_fcff_year1_exact():
    """PV(FCFF_1) = FCFF / (1 + WACC)."""
    fcff = 100.0
    wacc = 0.10
    ps = _simple_projected(fcff=fcff)
    result = build_dcf(ps, _std_inputs(wacc=wacc))
    expected = fcff / (1.0 + wacc)
    assert abs(result.base.pv_fcffs[0] - expected) < 1e-9


def test_pv_fcff_year5_exact():
    """PV(FCFF_5) = FCFF / (1 + WACC)^5."""
    fcff = 100.0
    wacc = 0.10
    ps = _simple_projected(fcff=fcff)
    result = build_dcf(ps, _std_inputs(wacc=wacc))
    expected = fcff / (1.0 + wacc) ** 5
    assert abs(result.base.pv_fcffs[4] - expected) < 1e-9


def test_sum_pv_fcff_equals_sum_of_individual_pvs():
    ps = _simple_projected(fcff=100.0)
    result = build_dcf(ps, _std_inputs(wacc=0.10))
    assert abs(result.base.sum_pv_fcff - sum(result.base.pv_fcffs)) < 1e-9


def test_sum_pv_fcff_annuity_formula():
    """100/yr for 5yr at 10% WACC = 100 × (1 − 1.1^−5) / 0.10 ≈ 379.079."""
    fcff = 100.0
    wacc = 0.10
    n = 5
    expected = fcff * (1 - (1 + wacc) ** -n) / wacc   # annuity PV formula
    ps = _simple_projected(fcff=fcff)
    result = build_dcf(ps, _std_inputs(wacc=wacc))
    assert abs(result.base.sum_pv_fcff - expected) < 1e-6


def test_higher_wacc_gives_lower_pv():
    """A higher discount rate must produce a lower sum PV FCFF."""
    ps = _simple_projected(fcff=100.0)
    lo = build_dcf(ps, _std_inputs(wacc=0.08, terminal_growth_rate=0.025))
    hi = build_dcf(ps, _std_inputs(wacc=0.12, terminal_growth_rate=0.025))
    assert lo.base.sum_pv_fcff > hi.base.sum_pv_fcff


# ── Terminal value — Gordon Growth Model ──────────────────────────────────────

def test_terminal_value_gg_formula():
    """TV_gg = FCFF_final × (1 + g) / (WACC − g)."""
    fcff = 100.0
    wacc = 0.10
    g = 0.025
    ps = _simple_projected(fcff=fcff)
    result = build_dcf(ps, _std_inputs(wacc=wacc, terminal_growth_rate=g))
    expected_tv = fcff * (1 + g) / (wacc - g)
    assert abs(result.base.terminal_value_gg - expected_tv) < 1e-6


def test_pv_terminal_value_gg_discounted():
    """PV(TV_gg) = TV_gg / (1 + WACC)^N."""
    fcff = 100.0
    wacc = 0.10
    g = 0.025
    n = 5
    ps = _simple_projected(fcff=fcff, n_years=n)
    result = build_dcf(ps, _std_inputs(wacc=wacc, terminal_growth_rate=g))
    tv = result.base.terminal_value_gg
    expected_pv = tv / (1 + wacc) ** n
    assert abs(result.base.pv_terminal_value_gg - expected_pv) < 1e-9


def test_terminal_value_gg_exact():
    """100 × 1.025 / (0.10 − 0.025) = 102.5 / 0.075 = 1366.667."""
    ps = _simple_projected(fcff=100.0)
    result = build_dcf(ps, _std_inputs(wacc=0.10, terminal_growth_rate=0.025))
    assert abs(result.base.terminal_value_gg - 102.5 / 0.075) < 1e-6


# ── Enterprise value ──────────────────────────────────────────────────────────

def test_enterprise_value_gg_equals_sum_pv_plus_pv_tv():
    """EV = Σ PV(FCFF) + PV(TV_gg)."""
    ps = _simple_projected(fcff=100.0)
    result = build_dcf(ps, _std_inputs())
    s = result.base
    expected = s.sum_pv_fcff + s.pv_terminal_value_gg
    assert abs(s.enterprise_value_gg - expected) < 1e-9


def test_enterprise_value_exact():
    """Exact: sum_pv=379.079, pv_tv=848.648, EV≈1227.727."""
    fcff = 100.0
    wacc = 0.10
    g = 0.025
    n = 5
    ps = _simple_projected(fcff=fcff, n_years=n)
    result = build_dcf(ps, _std_inputs(wacc=wacc, terminal_growth_rate=g))
    annuity_pv = fcff * (1 - (1 + wacc) ** -n) / wacc
    tv = fcff * (1 + g) / (wacc - g)
    pv_tv = tv / (1 + wacc) ** n
    expected_ev = annuity_pv + pv_tv
    assert abs(result.base.enterprise_value_gg - expected_ev) < 1e-4


# ── Equity bridge ─────────────────────────────────────────────────────────────

def test_equity_value_equals_ev_minus_net_debt():
    """Equity = EV − Net Debt (GGM path)."""
    net_debt = 200.0
    ps = _simple_projected(fcff=100.0)
    result = build_dcf(ps, _std_inputs(net_debt=net_debt))
    s = result.base
    assert abs(s.equity_value_gg - (s.enterprise_value_gg - net_debt)) < 1e-9


def test_equity_value_positive_net_cash():
    """Negative net_debt (net cash) increases equity above EV."""
    ps = _simple_projected(fcff=100.0)
    no_cash = build_dcf(ps, _std_inputs(net_debt=0.0))
    with_cash = build_dcf(ps, _std_inputs(net_debt=-500.0))   # net cash
    assert with_cash.base.equity_value_gg > no_cash.base.equity_value_gg


def test_equity_value_can_be_negative():
    """When EV < net_debt, equity is negative (distressed scenario)."""
    ps = _simple_projected(fcff=1.0)   # tiny FCFF → small EV
    result = build_dcf(ps, _std_inputs(net_debt=1e9))   # massive debt
    assert result.base.equity_value_gg < 0


# ── Price per share ───────────────────────────────────────────────────────────

def test_price_per_share_formula():
    """Price = equity / (diluted_shares_m × 1,000,000)  →  $/share."""
    ps = _simple_projected(fcff=100.0)
    shares = 50.0
    result = build_dcf(ps, _std_inputs(net_debt=0.0, diluted_shares_m=shares))
    s = result.base
    assert s.price_per_share_gg is not None
    assert abs(s.price_per_share_gg - s.equity_value_gg / (shares * 1_000_000)) < 1e-9


def test_price_per_share_none_when_shares_not_provided():
    ps = _simple_projected(fcff=100.0)
    result = build_dcf(ps, _std_inputs(diluted_shares_m=None))
    assert result.base.price_per_share_gg is None


def test_price_per_share_present_for_all_scenarios():
    ps = _simple_projected(fcff=100.0)
    result = build_dcf(ps, _std_inputs(diluted_shares_m=100.0))
    for scenario in (result.bear, result.base, result.bull):
        assert scenario.price_per_share_gg is not None


# ── Exit multiple terminal value ──────────────────────────────────────────────

def test_exit_multiple_tv_formula():
    """TV_exit = EBITDA_final × multiple."""
    ebitda_final = 250.0
    multiple = 10.0
    ps = _simple_projected(fcff=100.0, ebitda_final=ebitda_final)
    result = build_dcf(ps, _std_inputs(exit_ebitda_multiple=multiple))
    expected_tv = ebitda_final * multiple
    assert abs(result.base.terminal_value_exit - expected_tv) < 1e-6


def test_exit_multiple_pv_discounted():
    """PV(TV_exit) = TV_exit / (1 + WACC)^N."""
    ebitda_final = 250.0
    multiple = 10.0
    wacc = 0.10
    n = 5
    ps = _simple_projected(fcff=100.0, ebitda_final=ebitda_final, n_years=n)
    result = build_dcf(ps, _std_inputs(wacc=wacc, exit_ebitda_multiple=multiple))
    tv = result.base.terminal_value_exit
    expected_pv = tv / (1 + wacc) ** n
    assert abs(result.base.pv_terminal_value_exit - expected_pv) < 1e-9


def test_exit_multiple_ev_formula():
    """EV_exit = Σ PV(FCFF) + PV(TV_exit)."""
    ps = _simple_projected(fcff=100.0, ebitda_final=250.0)
    result = build_dcf(ps, _std_inputs(exit_ebitda_multiple=10.0))
    s = result.base
    assert s.enterprise_value_exit is not None
    expected = s.sum_pv_fcff + s.pv_terminal_value_exit
    assert abs(s.enterprise_value_exit - expected) < 1e-9


def test_exit_fields_none_when_no_multiple():
    """All exit-multiple fields are None when exit_ebitda_multiple not provided."""
    ps = _simple_projected()
    result = build_dcf(ps, _std_inputs(exit_ebitda_multiple=None))
    for scenario in (result.bear, result.base, result.bull):
        assert scenario.terminal_value_exit is None
        assert scenario.pv_terminal_value_exit is None
        assert scenario.enterprise_value_exit is None
        assert scenario.equity_value_exit is None
        assert scenario.price_per_share_exit is None


def test_exit_equity_bridge_consistent():
    """exit equity = exit EV − net_debt."""
    net_debt = 100.0
    ps = _simple_projected(fcff=100.0, ebitda_final=250.0)
    result = build_dcf(ps, _std_inputs(exit_ebitda_multiple=10.0, net_debt=net_debt))
    s = result.base
    assert abs(s.equity_value_exit - (s.enterprise_value_exit - net_debt)) < 1e-9


def test_exit_price_per_share():
    ps = _simple_projected(fcff=100.0, ebitda_final=250.0)
    result = build_dcf(
        ps,
        _std_inputs(exit_ebitda_multiple=10.0, net_debt=0.0, diluted_shares_m=50.0),
    )
    s = result.base
    assert s.price_per_share_exit is not None
    assert abs(s.price_per_share_exit - s.equity_value_exit / (50.0 * 1_000_000)) < 1e-9


# ── Bear / base / bull ordering ───────────────────────────────────────────────

def test_bear_base_bull_ev_ordering():
    """Higher growth → higher EV (positive FCFF, same WACC/g)."""
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived, max_years=5)
    profile = build_profile(stmts, "lululemon")
    asmp = build_assumptions_from_profile(profile)
    ps = build_forecast(asmp, profile)
    result = build_dcf(ps, _std_inputs(wacc=0.10, terminal_growth_rate=0.025))
    assert result.bear.enterprise_value_gg < result.base.enterprise_value_gg
    assert result.base.enterprise_value_gg < result.bull.enterprise_value_gg


def test_bear_base_bull_scenario_names():
    ps = _simple_projected()
    result = build_dcf(ps, _std_inputs())
    assert result.bear.scenario == "bear"
    assert result.base.scenario == "base"
    assert result.bull.scenario == "bull"


# ── Input validation ──────────────────────────────────────────────────────────

def test_raises_when_wacc_equals_g():
    """WACC == g → Gordon Growth denominator = 0 → ValueError."""
    ps = _simple_projected()
    with pytest.raises(ValueError, match="WACC"):
        build_dcf(ps, _std_inputs(wacc=0.025, terminal_growth_rate=0.025))


def test_raises_when_wacc_less_than_g():
    """WACC < g → Gordon Growth TV is negative → ValueError."""
    ps = _simple_projected()
    with pytest.raises(ValueError, match="WACC"):
        build_dcf(ps, _std_inputs(wacc=0.02, terminal_growth_rate=0.025))


def test_raises_when_shares_zero():
    ps = _simple_projected()
    with pytest.raises(ValueError, match="diluted_shares_m"):
        build_dcf(ps, _std_inputs(diluted_shares_m=0.0))


def test_raises_when_shares_negative():
    ps = _simple_projected()
    with pytest.raises(ValueError, match="diluted_shares_m"):
        build_dcf(ps, _std_inputs(diluted_shares_m=-10.0))


def test_raises_when_exit_multiple_zero():
    ps = _simple_projected()
    with pytest.raises(ValueError, match="exit_ebitda_multiple"):
        build_dcf(ps, _std_inputs(exit_ebitda_multiple=0.0))


def test_raises_when_exit_multiple_negative():
    ps = _simple_projected()
    with pytest.raises(ValueError, match="exit_ebitda_multiple"):
        build_dcf(ps, _std_inputs(exit_ebitda_multiple=-5.0))


# ── Quality propagation ───────────────────────────────────────────────────────

def test_quality_issues_propagated_from_projected():
    qi = QualityIssue(metric="revenue", severity="INFO", message="test")
    ps = _simple_projected(quality_issues=(qi,))
    result = build_dcf(ps, _std_inputs())
    assert isinstance(result.quality_issues, tuple)
    assert len(result.quality_issues) == 1
    assert result.quality_issues[0] is qi


def test_quality_issues_empty_when_none_in_projected():
    ps = _simple_projected(quality_issues=())
    result = build_dcf(ps, _std_inputs())
    assert result.quality_issues == ()


def test_quality_issues_is_tuple():
    ps = _simple_projected(quality_issues=(
        QualityIssue(metric="a", severity="INFO", message="x"),
        QualityIssue(metric="b", severity="WARNING", message="y"),
    ))
    result = build_dcf(ps, _std_inputs())
    assert type(result.quality_issues) is tuple


# ── Integration tests ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,cik,name,wacc,net_debt,shares_m", [
    # net_debt is raw USD (same units as FCFF stream from forecast)
    ("LULU", "0001397187", "lululemon", 0.10,  -1_000e6,  125.0),   # -$1B net cash
    ("F",    "0000037996", "Ford Motor", 0.12,  20_000e6, 4_000.0),  # $20B net debt
    ("VZ",   "0000732712", "Verizon",    0.09, 120_000e6, 4_200.0),  # $120B net debt
])
def test_integration_dcf_builds_without_error(ticker, cik, name, wacc, net_debt, shares_m):
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, ticker, facts, derived, max_years=5)
    profile = build_profile(stmts, name)
    asmp = build_assumptions_from_profile(profile)
    ps = build_forecast(asmp, profile)
    inputs = DCFInputs(
        wacc=wacc,
        terminal_growth_rate=0.025,
        exit_ebitda_multiple=8.0,
        net_debt=net_debt,
        diluted_shares_m=shares_m,
    )
    result = build_dcf(ps, inputs)

    assert result.ticker == ticker
    assert result.base.enterprise_value_gg > 0
    assert result.base.price_per_share_gg is not None
    assert result.base.terminal_value_exit is not None
    assert len(result.quality_issues) == len(asmp.quality_issues)


def test_integration_full_pipeline_identities():
    """All three DCF identities hold end-to-end for every scenario."""
    cik = "0001397187"
    with open(config.DATA_DIR / "raw" / f"{cik}.json") as f:
        payload = json.load(f)
    facts = extract_metrics(cik, payload)
    derived = compute_derived_metrics(cik, facts)
    stmts = build_statements(cik, "LULU", facts, derived, max_years=5)
    profile = build_profile(stmts, "lululemon")
    asmp = build_assumptions_from_profile(profile)
    ps = build_forecast(asmp, profile)
    inputs = DCFInputs(wacc=0.10, terminal_growth_rate=0.025,
                       exit_ebitda_multiple=10.0, net_debt=-1_000e6,  # -$1B net cash
                       diluted_shares_m=125.0)
    result = build_dcf(ps, inputs)

    wacc = inputs.wacc
    N = len(ps.base.years)

    for scen_forecast, scen_dcf in [
        (ps.bear, result.bear), (ps.base, result.base), (ps.bull, result.bull)
    ]:
        # PV identity
        assert abs(scen_dcf.sum_pv_fcff - sum(scen_dcf.pv_fcffs)) < 1e-6
        # GGM EV identity
        ev_expected = scen_dcf.sum_pv_fcff + scen_dcf.pv_terminal_value_gg
        assert abs(scen_dcf.enterprise_value_gg - ev_expected) < 1e-6
        # Exit EV identity
        assert abs(scen_dcf.enterprise_value_exit -
                   (scen_dcf.sum_pv_fcff + scen_dcf.pv_terminal_value_exit)) < 1e-6
        # Equity bridge
        assert abs(scen_dcf.equity_value_gg -
                   (scen_dcf.enterprise_value_gg - inputs.net_debt)) < 1e-6
        # Price / share: divide by actual share count (shares_m × 1,000,000)
        assert abs(scen_dcf.price_per_share_gg -
                   scen_dcf.equity_value_gg / (inputs.diluted_shares_m * 1_000_000)) < 1e-9
