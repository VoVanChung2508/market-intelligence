import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)


def _configured_path(variable: str, default: Path) -> Path:
    configured = os.getenv(variable)
    if not configured:
        return default
    path = Path(configured).expanduser()
    return path if path.is_absolute() else ROOT / path


DATA_DIR = _configured_path("MARKET_DATA_DIR", ROOT / "data" / "raw")
DB_PATH = _configured_path(
    "MARKET_DB_PATH",
    ROOT / "data" / "processed" / "market_intelligence.sqlite3",
)
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()