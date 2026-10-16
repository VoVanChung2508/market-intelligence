from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import requests
from pathlib import Path
import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def save_incremental(
    new_data: pd.DataFrame,
    output: Path,
    key: str,
) -> None:
    """Gop du lieu moi voi du lieu cu, chuan hoa khoa va loai trung."""

    if new_data.empty:
        raise ValueError(f"Khong co du lieu: {output.name}")

    if key not in new_data.columns:
        raise ValueError(f"Thieu cot khoa {key}: {output.name}")

    new_data = new_data.copy()
    new_data = new_data.dropna(subset=[key])

    # Chuan hoa khoa ngay thang va timestamp.
    if key == "timestamp_ms":
        new_data[key] = pd.to_numeric(
            new_data[key], errors="coerce"
        )
    elif key in ("Date", "Datetime", "index", "timestamp"):
        new_data[key] = pd.to_datetime(
            new_data[key], errors="coerce", utc=True
        )

        # Du lieu co phieu theo ngay: chi giu ngay giao dich.
        if key in ("Date", "Datetime", "index"):
            new_data[key] = new_data[key].dt.strftime("%Y-%m-%d")
        else:
            new_data[key] = new_data[key].dt.strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

    new_data = new_data.dropna(subset=[key])

    if output.exists():
        old_data = pd.read_csv(output)

        if key not in old_data.columns:
            raise ValueError(
                f"File cu thieu cot {key}: {output.name}. "
                "Dung ghi de de tranh mat du lieu."
            )

        # Chuan hoa khoa cua file cu giong file moi.
        if key == "timestamp_ms":
            old_data[key] = pd.to_numeric(
                old_data[key], errors="coerce"
            )
        elif key in ("Date", "Datetime", "index", "timestamp"):
            old_data[key] = pd.to_datetime(
                old_data[key], errors="coerce", utc=True
            )

            if key in ("Date", "Datetime", "index"):
                old_data[key] = old_data[key].dt.strftime("%Y-%m-%d")
            else:
                old_data[key] = old_data[key].dt.strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )

        old_data = old_data.dropna(subset=[key])

        combined = pd.concat(
            [old_data, new_data],
            ignore_index=True,
            sort=False,
        )
    else:
        combined = new_data.copy()

    # Neu trung khoa, uu tien ban ghi thu thap sau.
    combined = combined.drop_duplicates(
        subset=[key],
        keep="last",
    )

    combined = combined.sort_values(
        by=key,
        kind="stable",
    ).reset_index(drop=True)

    # Ghi file tam truoc, chi thay file chinh khi ghi thanh cong.
    temp_output = output.with_suffix(".tmp")
    combined.to_csv(temp_output, index=False)
    temp_output.replace(output)

    print(
        f"[OK] {output.name}: "
        f"{len(new_data)} dong vua xu ly, "
        f"{len(combined)} dong sau khi gop."
    )


def collect_stock(symbol: str) -> None:
    print(f"[STOCK] Dang tai {symbol}...")

    df = yf.download(
        symbol,
        period="2y",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if df.empty:
        raise RuntimeError(f"Yahoo Finance khong co du lieu cho {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()

    # Thường là Date; hỗ trợ cả trường hợp tên cột khác.
    date_column = next(
        (col for col in ("Date", "Datetime", "index") if col in df.columns),
        df.columns[0],
    )

    output = RAW_DIR / f"stock_{symbol.replace('.', '_')}.csv"

    save_incremental(
        new_data=df,
        output=output,
        key=date_column,
    )


def collect_crypto(coin_id: str) -> None:
    print(f"[CRYPTO] Dang tai {coin_id}...")

    url = (
        "https://api.coingecko.com/api/v3/"
        f"coins/{coin_id}/market_chart"
    )

    response = requests.get(
        url,
        params={
            "vs_currency": "usd",
            "days": "30",
        },
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    prices = pd.DataFrame(
        data.get("prices", []),
        columns=["timestamp_ms", "price_usd"],
    )

    if prices.empty:
        raise RuntimeError(f"CoinGecko khong co du lieu cho {coin_id}")

    prices["timestamp"] = pd.to_datetime(
        prices["timestamp_ms"],
        unit="ms",
        utc=True,
    )

    output = RAW_DIR / f"crypto_{coin_id}.csv"

    save_incremental(
        new_data=prices,
        output=output,
        key="timestamp_ms",
    )


def main() -> None:
    print(
        "Market Intelligence AI |",
        datetime.now(timezone.utc).isoformat(),
    )

    failures = []

    try:
        collect_stock("AAPL")
    except Exception as exc:
        print(f"[ERROR] AAPL: {exc}")
        failures.append(f"AAPL: {exc}")

    for coin in ("bitcoin", "ethereum"):
        try:
            collect_crypto(coin)
        except Exception as exc:
            print(f"[ERROR] {coin}: {exc}")
            failures.append(f"{coin}: {exc}")

    if failures:
        raise RuntimeError(
            "Mot hoac nhieu nguon du lieu that bai: "
            + " | ".join(failures)
        )

    print("Hoan tat thu thap du lieu.")


if __name__ == "__main__":
    main()
