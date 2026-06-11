import argparse
import sys


def _cmd_pull(args: argparse.Namespace) -> int:
    print(f"pull: not implemented yet (ticker={args.ticker})")
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
