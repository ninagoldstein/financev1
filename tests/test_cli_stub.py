import subprocess
import sys


def test_cli_rejects_unknown_command():
    r = subprocess.run(
        [sys.executable, "-m", "secpull", "frobnicate"],
        capture_output=True, text=True,
    )
    assert r.returncode == 2


def test_cli_report_stub():
    r = subprocess.run(
        [sys.executable, "-m", "secpull", "report", "LULU"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "not implemented" in r.stdout.lower()


def test_cli_export_stub():
    r = subprocess.run(
        [sys.executable, "-m", "secpull", "export", "LULU"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "not implemented" in r.stdout.lower()
