import subprocess
import sys


def test_cli_parses_pull():
    r = subprocess.run(
        [sys.executable, "-m", "secpull", "pull", "LULU"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "not implemented" in r.stdout.lower()


def test_cli_rejects_unknown_command():
    r = subprocess.run(
        [sys.executable, "-m", "secpull", "frobnicate"],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
