import argparse
import sqlite3
import sys

from secpull import config
from secpull.compare import comparison_rows, yoy_growth
from secpull.db import init_db, upsert_company, insert_facts, get_facts
from secpull.edgar import pull_and_cache, TickerNotFound
from secpull.export import export_xlsx
from secpull.extract import extract_metrics
from secpull.income_model import build_margin_assumptions, historical_metrics, project_income_statement
from secpull.income_model_export import add_income_sheet
from secpull.model import avg_growth, build_assumptions, historical_revenue, project_revenue, yoy_growth_rates
from secpull.model_export import export_model_xlsx
from secpull.models import METRIC_TAGS
from secpull.report import build_grid, find_missing, render_table


def _get_cik(conn: sqlite3.Connection, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT cik FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row[0] if row else None


def _cmd_pull(args: argparse.Namespace) -> int:
    try:
        company, payload = pull_and_cache(args.ticker)
    except TickerNotFound as e:
        print(str(e), file=sys.stderr)
        return 1

    conn = sqlite3.connect(config.DB_PATH)
    init_db(conn)
    upsert_company(conn, company)

    facts = extract_metrics(company.cik, payload)
    inserted = insert_facts(conn, facts)
    conn.close()

    print(f"Fetched {company.name} (CIK {company.cik}) — raw data cached.")
    print(f"Stored {len(facts)} financial facts for {company.ticker} ({inserted} new).")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(config.DB_PATH)
    init_db(conn)
    cik = _get_cik(conn, args.ticker)
    if cik is None:
        print(f"No data for {args.ticker}. Run: secpull pull {args.ticker}",
              file=sys.stderr)
        conn.close()
        return 1

    facts = get_facts(conn, cik=cik)
    conn.close()

    periods = "Q" if args.quarterly else "FY"
    headers, rows = build_grid(facts, periods)
    missing = find_missing(facts, periods)

    print(render_table(headers, rows))
    print()
    if missing:
        print("Missing Data:")
        for metric, period in missing:
            print(f"  {metric}: {period}")
    else:
        print("Missing Data: none")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(config.DB_PATH)
    init_db(conn)
    cik = _get_cik(conn, args.ticker)
    if cik is None:
        print(f"No data for {args.ticker}. Run: secpull pull {args.ticker}",
              file=sys.stderr)
        conn.close()
        return 1

    facts = get_facts(conn, cik=cik)
    conn.close()

    headers, rows = build_grid(facts, periods="FY")
    missing = find_missing(facts, periods="FY")

    exports_dir = config.DATA_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    out_path = exports_dir / f"{args.ticker}.xlsx"

    export_xlsx(args.ticker, headers, rows, missing, out_path)
    print(f"Exported: {out_path}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    if args.metric not in METRIC_TAGS:
        valid = ", ".join(METRIC_TAGS)
        print(f"Unknown metric '{args.metric}'. Valid metrics: {valid}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(config.DB_PATH)
    init_db(conn)

    per_ticker: dict[str, list] = {}
    for ticker in args.tickers:
        cik = _get_cik(conn, ticker)
        if cik is None:
            print(f"Pulling {ticker}...")
            try:
                company, payload = pull_and_cache(ticker)
            except TickerNotFound as e:
                print(str(e), file=sys.stderr)
                conn.close()
                return 1
            upsert_company(conn, company)
            all_facts = extract_metrics(company.cik, payload)
            insert_facts(conn, all_facts)
            cik = company.cik
            print(f"  Stored {len(all_facts)} facts for {ticker}.")

        facts = get_facts(conn, cik=cik, metric=args.metric)
        per_ticker[ticker] = [f for f in facts if f.fiscal_period == "FY"]

    conn.close()

    headers, rows = comparison_rows(per_ticker, args.metric)

    missing = [
        (row[0], headers[i + 1])
        for row in rows if "YoY" not in row[0]
        for i, cell in enumerate(row[1:])
        if cell == "N/A"
    ]

    print(render_table(headers, rows))
    print()
    if missing:
        print("Missing Data:")
        for ticker, year in missing:
            print(f"  {ticker}: {year}")
    else:
        print("Missing Data: none")
    return 0


def _cmd_model(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(config.DB_PATH)
    init_db(conn)
    cik = _get_cik(conn, args.ticker)
    if cik is None:
        print(f"No data for {args.ticker}. Run: secpull pull {args.ticker}", file=sys.stderr)
        conn.close()
        return 1

    all_facts = get_facts(conn, cik=cik)
    conn.close()

    rev_series = historical_revenue(all_facts)
    if len(rev_series) < 2:
        print(
            f"Not enough annual revenue data for {args.ticker} "
            "(need at least 2 consecutive fiscal years).",
            file=sys.stderr,
        )
        return 1

    rates = yoy_growth_rates(rev_series)
    try:
        base = avg_growth(rates)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    n_used = min(5, sum(1 for _, r in rates if r is not None))
    assumptions = build_assumptions(base, n_years_used=n_used)
    last_yr, last_rev = rev_series[-1]
    projections = project_revenue(last_rev, last_yr, assumptions, years=3)

    exports_dir = config.DATA_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    out_path = exports_dir / f"{args.ticker}_model.xlsx"
    export_model_xlsx(args.ticker, rev_series, assumptions, projections, out_path)

    # Income statement sheet
    margin_assumptions = build_margin_assumptions(all_facts, rev_series)
    income_projections = project_income_statement(projections, margin_assumptions)
    metric_series = {
        m: historical_metrics(all_facts, m)
        for m in ("gross_profit", "operating_income", "net_income", "eps_diluted")
    }
    proj_years = [yr for yr, _ in projections["base"]]
    from openpyxl import load_workbook as _load_wb
    wb = _load_wb(out_path)
    add_income_sheet(wb, args.ticker, rev_series, metric_series, margin_assumptions, assumptions, proj_years)
    wb.save(out_path)

    # Terminal summary
    print(f"Revenue model for {args.ticker}")
    print(
        f"  Historical range : FY{rev_series[0][0]}–FY{rev_series[-1][0]} "
        f"({len(rev_series)} years)"
    )
    print(
        f"  Base assumption  : {assumptions.base_growth * 100:.1f}% "
        f"(avg of last {assumptions.n_years_used} years)"
    )
    print(
        f"  Scenarios        : "
        f"Bear {assumptions.bear_growth * 100:.1f}%  "
        f"Base {assumptions.base_growth * 100:.1f}%  "
        f"Bull {assumptions.bull_growth * 100:.1f}%"
    )
    print()

    proj_years = [yr for yr, _ in projections["base"]]
    col_w = 12
    header = f"{'Scenario':<10}" + "".join(f"{'FY' + str(y):>{col_w}}" for y in proj_years)
    print(header)
    print("-" * len(header))
    for scenario in ("bear", "base", "bull"):
        row = f"{scenario.capitalize():<10}"
        for _, val in projections[scenario]:
            row += f"{'$' + f'{val / 1e9:.2f}B':>{col_w}}"
        print(row)

    print()
    def _fmt_pct(v): return f"{v * 100:.1f}%" if v is not None else "N/A"
    def _fmt_shares(v): return f"{v:.1f}M" if v is not None else "N/A"
    print("Margin assumptions (historical average):")
    print(f"  Gross margin    : {_fmt_pct(margin_assumptions.gross_margin)}")
    print(f"  Operating margin: {_fmt_pct(margin_assumptions.operating_margin)}")
    print(f"  Net margin      : {_fmt_pct(margin_assumptions.net_margin)}")
    print(f"  Diluted shares  : {_fmt_shares(margin_assumptions.diluted_shares_m)}")
    print()

    base_proj = income_projections["base"]
    proj_yrs = [yr for yr, _ in base_proj["revenue"]]
    col_w = 12
    hdr = f"{'Metric':<22}" + "".join(f"{'FY' + str(y):>{col_w}}" for y in proj_yrs)
    print("Income Statement Projections (Base):")
    print(hdr)
    print("-" * len(hdr))
    for metric, label in (
        ("revenue", "Revenue"),
        ("gross_profit", "Gross Profit"),
        ("operating_income", "Op. Income"),
        ("net_income", "Net Income"),
        ("eps_diluted", "EPS"),
    ):
        row = f"{label:<22}"
        for yr, val in base_proj[metric]:
            if val is None:
                row += f"{'N/A':>{col_w}}"
            elif metric == "eps_diluted":
                row += f"{'$' + f'{val:.2f}':>{col_w}}"
            else:
                row += f"{'$' + f'{val / 1e9:.2f}B':>{col_w}}"
        print(row)

    print(f"\nExported: {out_path}  (2 sheets: Revenue Model, Income Statement)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="secpull")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    p_pull = sub.add_parser("pull")
    p_pull.add_argument("ticker", type=str.upper)
    p_pull.set_defaults(func=_cmd_pull)

    p_report = sub.add_parser("report")
    p_report.add_argument("ticker", type=str.upper)
    p_report.add_argument("--quarterly", action="store_true")
    p_report.set_defaults(func=_cmd_report)

    p_export = sub.add_parser("export")
    p_export.add_argument("ticker", type=str.upper)
    p_export.set_defaults(func=_cmd_export)

    p_compare = sub.add_parser(
        "compare",
        description=(
            "Side-by-side annual comparison of one metric across tickers. "
            "Note: fiscal years are NOT aligned calendars across companies "
            "(e.g. LULU FY2023 ends Jan 2024; NKE FY2023 ends May 2023; "
            "AAPL FY2023 ends Sep 2023). Labels reflect each company's own "
            "fiscal year, which is standard but worth knowing when eyeballing comps."
        ),
    )
    p_compare.add_argument("tickers", nargs="+", type=str.upper,
                           metavar="TICKER")
    p_compare.add_argument(
        "--metric", default="revenue",
        help="Metric to compare. One of: " + ", ".join(METRIC_TAGS),
    )
    p_compare.set_defaults(func=_cmd_compare)

    p_model = sub.add_parser(
        "model",
        description="Build a 3-year bear/base/bull revenue projection from historical SEC data.",
    )
    p_model.add_argument("ticker", type=str.upper)
    p_model.set_defaults(func=_cmd_model)

    args = parser.parse_args()
    sys.exit(args.func(args))
