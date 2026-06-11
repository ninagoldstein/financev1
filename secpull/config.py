import os
from pathlib import Path

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "secpull.db"
RAW_DIR = DATA_DIR / "raw"

def get_user_agent() -> str:
    val = os.environ.get("SECPULL_USER_AGENT")
    if not val:
        raise EnvironmentError(
            "Set SECPULL_USER_AGENT to 'AppName (your-email)' — the SEC requires it."
        )
    return val


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
