import argparse
import sqlite3
import sys

from secpull import config
from secpull.db import init_db, upsert_company
from secpull.edgar import pull_and_cache, TickerNotFound


def _cmd_pull(args: argparse.Namespace) -> int:
    try:
        company, _ = pull_and_cache(args.ticker)
    except TickerNotFound as e:
        print(str(e), file=sys.stderr)
        return 1

    conn = sqlite3.connect(config.DB_PATH)
    init_db(conn)
    upsert_company(conn, company)
    conn.close()

    print(f"Fetched {company.name} (CIK {company.cik}) — raw data cached.")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    print(f"report: not implemented yet (ticker={args.ticker})")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    print(f"export: not implemented yet (ticker={args.ticker})")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="secpull")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    for cmd, fn in [("pull", _cmd_pull), ("report", _cmd_report), ("export", _cmd_export)]:
        p = sub.add_parser(cmd)
        p.add_argument("ticker", type=str.upper)
        p.set_defaults(func=fn)

    args = parser.parse_args()
    sys.exit(args.func(args))
