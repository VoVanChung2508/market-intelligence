import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import joblib

from backend.analysis import MODEL_VERSION, evaluate_baseline
from backend.database import connect
from backend.settings import DB_PATH, ROOT


def load_daily_closes(db_path: Path, symbol: str) -> tuple[list[str], list[float]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT timestamp, close FROM price_bars "
            "WHERE symbol = ? AND interval = '1d' "
            "ORDER BY timestamp ASC",
            (symbol.upper(),),
        ).fetchall()
    finally:
        connection.close()
    return [row["timestamp"] for row in rows], [row["close"] for row in rows]


def train_and_save(symbol: str, db_path: Path = DB_PATH) -> dict[str, object]:
    timestamps, closes = load_daily_closes(db_path, symbol)
    report, model = evaluate_baseline(closes, timestamps)
    report["symbol"] = symbol.upper()
    report["trained_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["source"] = "local historical bars"

    output_dir = ROOT / "data" / "processed" / "models" / MODEL_VERSION
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"{symbol.lower()}.joblib"
    report_path = output_dir / f"{symbol.lower()}.json"
    if model_path.exists() or report_path.exists():
        raise FileExistsError(f"Model artifact already exists: {output_dir}")

    with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".joblib", delete=False) as file:
        model_temp = Path(file.name)
    report_temp = output_dir / f".{symbol.lower()}.json.tmp"
    try:
        joblib.dump(model, model_temp)
        report_temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        os.replace(model_temp, model_path)
        os.replace(report_temp, report_path)
    finally:
        model_temp.unlink(missing_ok=True)
        report_temp.unlink(missing_ok=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", help="Asset symbol with stored daily bars")
    args = parser.parse_args()
    print(json.dumps(train_and_save(args.symbol), indent=2))


if __name__ == "__main__":
    main()