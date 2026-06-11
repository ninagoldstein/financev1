import os
from pathlib import Path

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "secpull.db"
RAW_DIR = DATA_DIR / "raw"

_user_agent: str | None = os.environ.get("SECPULL_USER_AGENT")


def get_user_agent() -> str:
    if not _user_agent:
        raise EnvironmentError(
            "Set SECPULL_USER_AGENT to 'AppName (your-email)' — the SEC requires it."
        )
    return _user_agent


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
