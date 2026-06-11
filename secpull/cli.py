import argparse
import sqlite3
import sys

from secpull import config
from secpull.db import init_db, upsert_company, insert_facts, get_facts
from secpull.edgar import pull_and_cache, TickerNotFound
from secpull.export import export_xlsx
from secpull.extract import extract_metrics
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

    args = parser.parse_args()
    sys.exit(args.func(args))
