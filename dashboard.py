
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "raw"

st.set_page_config(
    page_title="Market Intelligence AI",
    page_icon="📈",
    layout="wide",
)

st.title("Market Intelligence AI")
st.caption("Stock & Blockchain Research Dashboard")

files = sorted(DATA_DIR.glob("*.csv"))

if not files:
    st.warning("Chua co du lieu. Hay chay python src/collect.py.")
    st.stop()

selected = st.selectbox(
    "Chon tai san",
    files,
    format_func=lambda p: p.stem,
)

df = pd.read_csv(selected)

if "timestamp" in df.columns:
    time_col = "timestamp"
elif "Date" in df.columns:
    time_col = "Date"
elif "Datetime" in df.columns:
    time_col = "Datetime"
else:
    time_col = None

price_col = (
    "price_usd"
    if "price_usd" in df.columns
    else "Close" if "Close" in df.columns else None
)

if price_col is None:
    st.error("Khong tim thay cot gia trong file.")
    st.dataframe(df.tail(20))
    st.stop()

df[price_col] = pd.to_numeric(df[price_col], errors="coerce")
df = df.dropna(subset=[price_col])

if time_col:
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce", utc=True)
    df = df.dropna(subset=[time_col]).sort_values(time_col)

latest = float(df[price_col].iloc[-1])
previous = float(df[price_col].iloc[-2]) if len(df) > 1 else latest
change = (latest / previous - 1) * 100 if previous else 0

c1, c2, c3 = st.columns(3)
c1.metric("Gia gan nhat", f"{latest:,.4f}")
c2.metric("Thay doi so voi diem truoc", f"{change:+.2f}%")
c3.metric("So mau", f"{len(df):,}")

if time_col:
    fig = px.line(
        df,
        x=time_col,
        y=price_col,
        title=f"Lich su gia: {selected.stem}",
    )
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Du lieu gan nhat")
st.dataframe(df.tail(20), use_container_width=True)

st.download_button(
    "Tai CSV",
    df.to_csv(index=False).encode("utf-8"),
    file_name=selected.name,
    mime="text/csv",
)

