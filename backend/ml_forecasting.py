"""
Chương trình Machine Learning Chuyên sâu Dự báo Xu hướng Chuỗi Thời gian Tài chính
Các mô hình: Baseline (Logistic Regression), Random Forest, XGBoost, Deep Learning LSTM (PyTorch)
Bảo đảm không bị rò rỉ dữ liệu (Data Leakage) với TimeSeriesSplit, Walk-forward chuẩn và Backtest tính phí.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import ta
import torch
import torch.nn as nn
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, TensorDataset
from xgboost import XGBClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

# Cố định random seed để kết quả có thể tái lập (Reproducibility)
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)
torch.manual_seed(RANDOM_SEED)


# ==============================================================================
# 1. LOAD DỮ LIỆU (CSV HOẶC SQLITE)
# ==============================================================================
def load_data(
    source_type: str = "csv",
    source_path: str = "data/raw/stock_AAPL.csv",
    symbol: str = "AAPL",
) -> pd.DataFrame:
    """
    Nạp dữ liệu chuỗi thời gian OHLCV từ file CSV hoặc SQLite của dự án.
    Chuẩn hóa tên cột thành: open, high, low, close, volume, timestamp.
    """
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file dữ liệu tại: {source_path}")

    if source_type.lower() == "csv":
        df = pd.read_csv(path)
        # Chuẩn hóa tên cột
        col_map = {
            "Date": "timestamp",
            "Datetime": "timestamp",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "price_usd": "close",
        }
        df = df.rename(columns=col_map)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)
            df.set_index("timestamp", inplace=True)
        # Nếu thiếu high/low/volume (như file crypto rút gọn), tự bù đắp giá trị
        if "high" not in df.columns:
            df["high"] = df["close"]
        if "low" not in df.columns:
            df["low"] = df["close"]
        if "open" not in df.columns:
            df["open"] = df["close"].shift(1).fillna(df["close"])
        if "volume" not in df.columns:
            df["volume"] = 1.0

    elif source_type.lower() == "sqlite":
        with sqlite3.connect(path) as conn:
            query = """
                SELECT timestamp, open, high, low, close, volume
                FROM price_bars
                WHERE symbol = ?
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(query, conn, params=(symbol.upper(),))
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)
            df.set_index("timestamp", inplace=True)
    else:
        raise ValueError(f"Loại nguồn {source_type} không hợp lệ! Dùng 'csv' hoặc 'sqlite'.")

    LOGGER.info("Nạp dữ liệu thành công: %d hàng, từ %s đến %s", len(df), df.index[0], df.index[-1])
    return df


# ==============================================================================
# 2. FEATURE ENGINEERING (TÍNH TOÁN CÁC CHỈ BÁO KỸ THUẬT)
# ==============================================================================
def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tính toán các chỉ báo kỹ thuật toàn diện bằng thư viện `ta` và pandas.
    Bảo đảm tại thời điểm t chỉ sử dụng thông tin từ t trở về trước.
    """
    data = df.copy()

    # 1. Lợi suất và biến động (Returns & Volatility)
    data["log_return"] = np.log(data["close"] / data["close"].shift(1))
    data["return_1"] = data["close"].pct_change(1)
    data["return_5"] = data["close"].pct_change(5)
    data["volatility_20"] = data["log_return"].rolling(window=20).std()

    # 2. Đường trung bình động (Trend Indicators - SMA & EMA)
    for period in [10, 20, 50]:
        data[f"sma_{period}"] = ta.trend.sma_indicator(data["close"], window=period)
        data[f"ema_{period}"] = ta.trend.ema_indicator(data["close"], window=period)
        # Tỷ lệ so với giá hiện tại để đưa về trạng thái dừng (Stationary)
        data[f"ratio_sma_{period}"] = data["close"] / data[f"sma_{period}"] - 1.0

    # 3. RSI (14)
    data["rsi_14"] = ta.momentum.rsi(data["close"], window=14)

    # 4. MACD (12, 26, 9)
    macd = ta.trend.MACD(data["close"], window_slow=26, window_fast=12, window_sign=9)
    data["macd"] = macd.macd()
    data["macd_signal"] = macd.macd_signal()
    data["macd_diff"] = macd.macd_diff()

    # 5. Bollinger Bands (20, 2 std)
    bb = ta.volatility.BollingerBands(data["close"], window=20, window_dev=2)
    data["bb_high"] = bb.bollinger_hband()
    data["bb_low"] = bb.bollinger_lband()
    data["bb_mid"] = bb.bollinger_mavg()
    data["bb_width"] = (data["bb_high"] - data["bb_low"]) / data["bb_mid"]
    data["bb_pct_b"] = bb.bollinger_pband()

    # 6. ATR (Average True Range - 14)
    data["atr_14"] = ta.volatility.average_true_range(
        data["high"], data["low"], data["close"], window=14
    )
    data["atr_ratio"] = data["atr_14"] / data["close"]

    # 7. Volume Indicators
    data["volume_sma20"] = data["volume"].rolling(window=20).mean()
    data["volume_ratio"] = data["volume"] / (data["volume_sma20"] + 1e-9)
    data["obv"] = ta.volume.on_balance_volume(data["close"], data["volume"])
    data["obv_sma20"] = data["obv"].rolling(window=20).mean()
    data["obv_ratio"] = data["obv"] / (data["obv_sma20"].abs() + 1e-9)

    LOGGER.info("Đã tạo %d đặc trưng kỹ thuật.", len(data.columns) - len(df.columns))
    return data


# ==============================================================================
# 3. CHUẨN BỊ DATASET & CHIA TRAIN / VAL / TEST (TRÁNH DATA LEAKAGE)
# ==============================================================================
def prepare_features_and_targets(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series, list[str]]:
    """
    Tạo biến mục tiêu Target = 1 (Tăng) nếu giá đóng cửa ngày tiếp theo > hôm nay.
    Dùng shift(-1) để bảo đảm không rò rỉ dữ liệu tương lai.
    """
    data = df.copy()

    # TARGET: 1 nếu close(t+1) > close(t), 0 nếu ngược lại
    # Cực kỳ quan trọng: shift(-1) đưa giá t+1 về làm nhãn cho hàng t
    data["target"] = (data["close"].shift(-1) > data["close"]).astype(int)

    # Bỏ hàng cuối cùng vì target của nó là NaN do shift(-1)
    data = data.iloc[:-1].copy()

    # Bỏ các hàng NaN ban đầu do độ trễ của rolling indicators (SMA 50 cần 50 phiên)
    data = data.dropna().copy()

    feature_cols = [
        col for col in data.columns
        if col not in ["target", "open", "high", "low", "close", "volume", "bb_high", "bb_low", "bb_mid", "obv"]
    ]

    X = data[feature_cols]
    y = data["target"]

    LOGGER.info("Kích thước sau khi làm sạch: %d mẫu, %d đặc trưng.", len(X), len(feature_cols))
    return X, y, feature_cols


def split_train_val_test(
    X: pd.DataFrame, y: pd.Series, train_ratio=0.70, val_ratio=0.15
) -> dict[str, Any]:
    """
    Chia dữ liệu THEO THỨ TỰ THỜI GIAN TUYỆT ĐỐI (Chống rò rỉ):
    - Train: 70%
    - Validation: 15%
    - Test: 15%
    KHÔNG DÙNG SHUFFLE.
    Fit StandardScaler CHỈ TRÊN TẬP TRAIN, rồi transform Val và Test.
    """
    n = len(X)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    X_train_raw, y_train = X.iloc[:train_end], y.iloc[:train_end]
    X_val_raw, y_val = X.iloc[train_end:val_end], y.iloc[train_end:val_end]
    X_test_raw, y_test = X.iloc[val_end:], y.iloc[val_end:]

    # Chuẩn hóa StandardScaler: FIT CHỈ TRÊN TRAIN
    scaler = StandardScaler()
    scaler.fit(X_train_raw)

    X_train = pd.DataFrame(scaler.transform(X_train_raw), index=X_train_raw.index, columns=X.columns)
    X_val = pd.DataFrame(scaler.transform(X_val_raw), index=X_val_raw.index, columns=X.columns)
    X_test = pd.DataFrame(scaler.transform(X_test_raw), index=X_test_raw.index, columns=X.columns)

    LOGGER.info(
        "Phân chia tập dữ liệu: Train=%d (%s -> %s), Val=%d (%s -> %s), Test=%d (%s -> %s)",
        len(X_train), X_train.index[0], X_train.index[-1],
        len(X_val), X_val.index[0], X_val.index[-1],
        len(X_test), X_test.index[0], X_test.index[-1],
    )

    return {
        "X_train": X_train, "y_train": y_train,
        "X_val": X_val, "y_val": y_val,
        "X_test": X_test, "y_test": y_test,
        "scaler": scaler,
    }


# ==============================================================================
# 4. HUẤN LUYỆN CÁC MÔ HÌNH (MODELS)
# ==============================================================================

# --- A. BASELINE: LOGISTIC REGRESSION ---
def train_baseline(X_train: pd.DataFrame, y_train: pd.Series) -> LogisticRegression:
    LOGGER.info("Huấn luyện Baseline (Logistic Regression)...")
    model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=RANDOM_SEED)
    model.fit(X_train, y_train)
    return model


# --- B. RANDOM FOREST (CÓ HYPERPARAMETER TUNING VỚI TIMESERIESSPLIT) ---
def train_random_forest(X_train: pd.DataFrame, y_train: pd.Series) -> RandomForestClassifier:
    LOGGER.info("Huấn luyện Random Forest với TimeSeriesSplit Hyperparameter Tuning...")
    tscv = TimeSeriesSplit(n_splits=3)
    param_grid = {
        "n_estimators": [50, 100],
        "max_depth": [3, 5, 8],
        "min_samples_split": [5, 10],
    }
    rf = RandomForestClassifier(random_state=RANDOM_SEED, class_weight="balanced")
    grid_search = GridSearchCV(rf, param_grid, cv=tscv, scoring="accuracy", n_jobs=-1)
    grid_search.fit(X_train, y_train)
    LOGGER.info("Random Forest Best Params: %s", grid_search.best_params_)
    return grid_search.best_estimator_


# --- C. XGBOOST (CÓ HYPERPARAMETER TUNING VỚI TIMESERIESSPLIT) ---
def train_xgboost(X_train: pd.DataFrame, y_train: pd.Series) -> XGBClassifier:
    LOGGER.info("Huấn luyện XGBoost với TimeSeriesSplit...")
    tscv = TimeSeriesSplit(n_splits=3)
    param_grid = {
        "n_estimators": [50, 100],
        "max_depth": [2, 3, 5],
        "learning_rate": [0.03, 0.08],
        "subsample": [0.8, 1.0],
    }
    xgb = XGBClassifier(
        random_state=RANDOM_SEED,
        eval_metric="logloss",
    )
    grid_search = GridSearchCV(xgb, param_grid, cv=tscv, scoring="accuracy", n_jobs=-1)
    grid_search.fit(X_train, y_train)
    LOGGER.info("XGBoost Best Params: %s", grid_search.best_params_)
    return grid_search.best_estimator_


# --- D. DEEP LEARNING LSTM (PYTORCH) ---
class LSTMClassifier(nn.Module):
    """
    Kiến trúc Deep Learning LSTM 2 tầng + Dropout + Fully Connected layer cho bài toán nhị phân.
    """
    def __init__(self, input_dim: int, hidden_dim: int = 64, num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        last_step = out[:, -1, :]  # Lấy output ở bước thời gian cuối cùng của window
        return self.fc(last_step).squeeze(-1)


def create_sliding_windows(X: np.ndarray, y: np.ndarray, window_size: int = 20) -> Tuple[np.ndarray, np.ndarray]:
    """Tạo cửa sổ trượt (window sequence) cho mô hình chuỗi thời gian LSTM."""
    X_seq, y_seq = [], []
    for i in range(len(X) - window_size):
        X_seq.append(X[i : i + window_size])
        y_seq.append(y[i + window_size])
    return np.array(X_seq, dtype=np.float32), np.array(y_seq, dtype=np.float32)


def train_lstm_pytorch(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    window_size: int = 20,
    epochs: int = 50,
    batch_size: int = 32,
    patience: int = 10,
) -> Tuple[LSTMClassifier, dict[str, list[float]]]:
    LOGGER.info("Huấn luyện PyTorch LSTM (Window=%d, 2 Layers, EarlyStopping)...", window_size)

    # Chuyển đổi thành dạng 3D tensor: (samples, window_size, features)
    X_train_seq, y_train_seq = create_sliding_windows(X_train.values, y_train.values, window_size)
    X_val_seq, y_val_seq = create_sliding_windows(X_val.values, y_val.values, window_size)

    train_dataset = TensorDataset(torch.tensor(X_train_seq), torch.tensor(y_train_seq))
    val_dataset = TensorDataset(torch.tensor(X_val_seq), torch.tensor(y_val_seq))

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=False)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    model = LSTMClassifier(input_dim=X_train.shape[1], hidden_dim=64, num_layers=2, dropout=0.2)
    criterion = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)

    history = {"train_loss": [], "val_loss": []}
    best_val_loss = float("inf")
    best_weights = None
    patience_counter = 0

    for epoch in range(epochs):
        model.train()
        train_loss = 0.0
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            preds = model(batch_x)
            loss = criterion(preds, batch_y)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * len(batch_y)
        train_loss /= len(train_dataset)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                preds = model(batch_x)
                loss = criterion(preds, batch_y)
                val_loss += loss.item() * len(batch_y)
        val_loss /= len(val_dataset)

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)

        # Early Stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_weights = model.state_dict().copy()
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                LOGGER.info("Early stopping tại epoch %d (Best Val Loss: %.4f)", epoch + 1, best_val_loss)
                break

    if best_weights is not None:
        model.load_state_dict(best_weights)
    return model, history


# ==============================================================================
# 5. ĐÁNH GIÁ MÔ HÌNH VÀ BACKTEST CHIẾN LƯỢC
# ==============================================================================
def evaluate_models(
    models: dict[str, Any],
    X_test: pd.DataFrame,
    y_test: pd.Series,
    window_size: int = 20,
) -> Tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Đánh giá toàn diện các chỉ số phân loại: Accuracy, Precision, Recall, F1, ROC-AUC."""
    results = []
    predictions = {}

    for name, model in models.items():
        if "LSTM" in name:
            # LSTM cần dữ liệu dạng window trượt
            X_test_seq, y_test_seq = create_sliding_windows(X_test.values, y_test.values, window_size)
            model.eval()
            with torch.no_grad():
                probs = model(torch.tensor(X_test_seq)).numpy()
            preds = (probs >= 0.5).astype(int)
            y_eval = y_test_seq
        else:
            probs = model.predict_proba(X_test)[:, 1]
            preds = model.predict(X_test)
            y_eval = y_test.values

        predictions[name] = preds

        acc = accuracy_score(y_eval, preds)
        prec = precision_score(y_eval, preds, zero_division=0)
        rec = recall_score(y_eval, preds, zero_division=0)
        f1 = f1_score(y_eval, preds, zero_division=0)
        try:
            auc = roc_auc_score(y_eval, probs)
        except Exception:
            auc = 0.5

        results.append({
            "Mô hình": name,
            "Accuracy": f"{acc * 100:.2f}%",
            "Precision": f"{prec * 100:.2f}%",
            "Recall": f"{rec * 100:.2f}%",
            "F1-Score": f"{f1 * 100:.2f}%",
            "ROC-AUC": f"{auc:.4f}",
        })

    results_df = pd.DataFrame(results)
    return results_df, predictions


def backtest_strategy(
    df_raw: pd.DataFrame,
    y_pred: np.ndarray,
    test_index: pd.Index,
    fee: float = 0.001,
) -> pd.DataFrame:
    """
    Backtest chiến lược:
    - Nếu dự đoán ngày mai TĂNG (y_pred = 1): MUA hoặc GIỮ hàng.
    - Nếu dự đoán ngày mai GIẢM (y_pred = 0): BÁN và GIỮ TIỀN MẶT (Cash).
    - Tính phí giao dịch (phí mở/đóng vị thế) = 0.1% (fee = 0.001).
    So sánh đường vốn (Equity Curve) với chiến lược Mua & Nắm Giữ (Buy & Hold).
    """
    sub_df = df_raw.loc[test_index].copy()
    sub_df = sub_df.iloc[-len(y_pred):].copy()
    sub_df["signal"] = y_pred

    # Lợi suất thực tế hàng ngày của tài sản
    sub_df["market_return"] = sub_df["close"].pct_change().fillna(0)

    # Vị thế hôm nay xác định lợi suất ngày mai: position = signal(t-1)
    sub_df["position"] = sub_df["signal"].shift(1).fillna(0)

    # Phí giao dịch phát sinh khi vị thế thay đổi (từ 0 lên 1 hoặc 1 xuống 0)
    sub_df["trade"] = sub_df["position"].diff().abs().fillna(0)
    sub_df["fee_cost"] = sub_df["trade"] * fee

    # Lợi suất chiến lược = (vị thế * lợi suất thị trường) - phí giao dịch
    sub_df["strategy_return"] = (sub_df["position"] * sub_df["market_return"]) - sub_df["fee_cost"]

    # Tích lũy vốn (Bắt đầu từ 1.0)
    sub_df["equity_strategy"] = (1 + sub_df["strategy_return"]).cumprod()
    sub_df["equity_buy_hold"] = (1 + sub_df["market_return"]).cumprod()

    return sub_df


# ==============================================================================
# 6. TRỰC QUAN HÓA KẾT QUẢ (PLOTS)
# ==============================================================================
def plot_results(
    history_lstm: dict[str, list[float]],
    rf_model: RandomForestClassifier,
    xgb_model: XGBClassifier,
    feature_cols: list[str],
    backtest_df: pd.DataFrame,
    best_model_name: str,
    output_path: str = "model_evaluation_report.png",
) -> None:
    """Vẽ 3 biểu đồ chuẩn kỹ thuật: Learning Curve, Feature Importance, Backtest Equity."""
    fig, axes = plt.subplots(2, 2, figsize=(16, 11))
    plt.subplots_adjust(hspace=0.35, wspace=0.25)

    # 1. Learning Curve của LSTM
    axes[0, 0].plot(history_lstm["train_loss"], label="Train Loss", color="royalblue", lw=2)
    axes[0, 0].plot(history_lstm["val_loss"], label="Val Loss", color="orange", lw=2)
    axes[0, 0].set_title("LSTM Learning Curve (Loss qua các Epochs)", fontsize=12, fontweight="bold")
    axes[0, 0].set_xlabel("Epochs")
    axes[0, 0].set_ylabel("Binary Cross-Entropy Loss")
    axes[0, 0].grid(True, linestyle="--", alpha=0.6)
    axes[0, 0].legend()

    # 2. Feature Importance - Random Forest
    rf_importances = pd.Series(rf_model.feature_importances_, index=feature_cols).sort_values(ascending=False)[:10]
    rf_importances.sort_values().plot(kind="barh", ax=axes[0, 1], color="#2ca02c")
    axes[0, 1].set_title("Top 10 Đặc Trưng Quan Trọng (Random Forest)", fontsize=12, fontweight="bold")
    axes[0, 1].set_xlabel("Importance Score")
    axes[0, 1].grid(True, linestyle="--", alpha=0.6)

    # 3. Feature Importance - XGBoost
    xgb_importances = pd.Series(xgb_model.feature_importances_, index=feature_cols).sort_values(ascending=False)[:10]
    xgb_importances.sort_values().plot(kind="barh", ax=axes[1, 0], color="#d62728")
    axes[1, 0].set_title("Top 10 Đặc Trưng Quan Trọng (XGBoost)", fontsize=12, fontweight="bold")
    axes[1, 0].set_xlabel("F-Score Importance")
    axes[1, 0].grid(True, linestyle="--", alpha=0.6)

    # 4. Backtest Equity Curve
    axes[1, 1].plot(backtest_df.index, backtest_df["equity_strategy"], label=f"Chiến lược AI ({best_model_name})", color="forestgreen", lw=2)
    axes[1, 1].plot(backtest_df.index, backtest_df["equity_buy_hold"], label="Buy & Hold (Mua & Giữ)", color="grey", linestyle="--", lw=1.5)
    axes[1, 1].set_title("Hiệu Suất Vốn Chiến Lược vs Buy & Hold (Phí 0.1%)", fontsize=12, fontweight="bold")
    axes[1, 1].set_xlabel("Thời gian")
    axes[1, 1].set_ylabel("Hệ số vốn (Bắt đầu = 1.0)")
    axes[1, 1].grid(True, linestyle="--", alpha=0.6)
    axes[1, 1].legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    LOGGER.info("Đã lưu biểu đồ phân tích tại: %s", output_path)


# ==============================================================================
# 7. CHƯƠNG TRÌNH CHÍNH (MAIN PIPELINE)
# ==============================================================================
def main():
    print("=" * 75)
    print("  HỆ THỐNG MACHINE LEARNING DỰ BÁO XU HƯỚNG TÀI CHÍNH CHUYÊN SÂU")
    print("=" * 75)

    # Chọn nguồn dữ liệu (ở đây ưu tiên file CSV có đủ OHLCV của dự án)
    source_csv = "data/raw/stock_AAPL.csv"
    if not os.path.exists(source_csv):
        source_csv = "data/raw/crypto_bitcoin.csv"

    # Bước 1 & 2: Load dữ liệu và tính toán chỉ báo kỹ thuật
    raw_df = load_data(source_type="csv", source_path=source_csv)
    df_with_indicators = add_technical_indicators(raw_df)

    # Bước 3: Tạo đặc trưng và biến mục tiêu, chia Train/Val/Test
    X, y, feature_cols = prepare_features_and_targets(df_with_indicators)
    split_data = split_train_val_test(X, y, train_ratio=0.70, val_ratio=0.15)

    X_train, y_train = split_data["X_train"], split_data["y_train"]
    X_val, y_val = split_data["X_val"], split_data["y_val"]
    X_test, y_test = split_data["X_test"], split_data["y_test"]

    # Bước 4: Huấn luyện các mô hình
    baseline_model = train_baseline(X_train, y_train)
    rf_model = train_random_forest(X_train, y_train)
    xgb_model = train_xgboost(X_train, y_train)
    lstm_model, history_lstm = train_lstm_pytorch(X_train, y_train, X_val, y_val, window_size=20, epochs=40)

    # Bước 5: Đánh giá tất cả mô hình trên tập Test độc lập
    models_dict = {
        "Baseline (Logistic)": baseline_model,
        "Random Forest": rf_model,
        "XGBoost": xgb_model,
        "LSTM (PyTorch)": lstm_model,
    }

    eval_table, predictions = evaluate_models(models_dict, X_test, y_test, window_size=20)
    print("\n" + "=" * 65)
    print("  BẢNG SO SÁNH HIỆU SUẤT CÁC MÔ HÌNH TRÊN TẬP TEST ĐỘC LẬP")
    print("=" * 65)
    print(eval_table.to_string(index=False))

    # Bước 6: Backtest mô hình tốt nhất
    best_model_name = "XGBoost"  # hoặc chọn mô hình có accuracy cao nhất
    best_preds = predictions[best_model_name]
    backtest_res = backtest_strategy(raw_df, best_preds, test_index=X_test.index, fee=0.001)

    initial_capital = 1.0
    strategy_final = backtest_res["equity_strategy"].iloc[-1]
    buy_hold_final = backtest_res["equity_buy_hold"].iloc[-1]
    strategy_roi = (strategy_final - initial_capital) * 100
    buy_hold_roi = (buy_hold_final - initial_capital) * 100

    print("\n" + "=" * 65)
    print(f"  KẾT QUẢ BACKTEST CHIẾN LƯỢC DỰ BÁO ({best_model_name}) VS BUY & HOLD")
    print("=" * 65)
    print(f"  - Lợi nhuận Chiến lược AI ({best_model_name}) : {strategy_roi:+.2f}%")
    print(f"  - Lợi nhuận Buy & Hold                    : {buy_hold_roi:+.2f}%")
    print(f"  - Phí giao dịch trừ mỗi lệnh              : 0.10%")

    # Bước 7: Xuất biểu đồ phân tích
    plot_results(
        history_lstm=history_lstm,
        rf_model=rf_model,
        xgb_model=xgb_model,
        feature_cols=feature_cols,
        backtest_df=backtest_res,
        best_model_name=best_model_name,
        output_path="model_evaluation_report.png",
    )
    print("\n[OK] Đã hoàn tất! Biểu đồ phân tích và backtest được lưu tại: model_evaluation_report.png")


if __name__ == "__main__":
    main()
