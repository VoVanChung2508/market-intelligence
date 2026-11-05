import argparse
import html
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from backend.ai_provider import translate_sentiment
from backend.database import (
    connect,
    get_asset_summaries,
    get_collection_runs,
    get_news_articles,
)
from backend.settings import DB_PATH
from src.notify import (
    is_telegram_configured,
    is_telegram_enabled,
    send_telegram_message,
)

LOGGER = logging.getLogger(__name__)
SHUTDOWN = threading.Event()


def _request_shutdown(_signum: int, _frame: object) -> None:
    LOGGER.info("telegram_bot_shutdown_requested")
    SHUTDOWN.set()


def is_authorized_chat(chat_id: int | str) -> bool:
    configured_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not configured_chat:
        return False
    return str(chat_id).strip() == configured_chat


def handle_status_command(db_path: Path) -> str:
    db_ok = False
    try:
        connection = connect(db_path)
        try:
            connection.execute("SELECT 1").fetchone()
            db_ok = True
        finally:
            connection.close()
    except Exception:
        db_ok = False

    assets = get_asset_summaries(db_path) if db_ok else []
    freshness_set = {str(a.get("freshness")) for a in assets} if assets else set()
    if not assets:
        data_status = "unavailable"
    elif freshness_set == {"fresh"}:
        data_status = "fresh (Tươi mới)"
    else:
        data_status = "stale (Cũ / Đang cập nhật)"

    runs = get_collection_runs(db_path, limit=3) if db_ok else []

    lines = [
        "📊 <b>Tình trạng Hệ thống (System Status)</b>",
        f"• Database: {'✅ OK (SQLite WAL)' if db_ok else '❌ Error'}",
        f"• Dữ liệu thị trường: {data_status}",
        "",
        "<b>Lịch sử Thu thập Gần nhất:</b>",
    ]
    if runs:
        for r in runs:
            status_icon = "✅" if r.get("status") == "success" else ("⏸️" if r.get("status") == "skipped" else "⚠️")
            completed = r.get("completed_at") or r.get("started_at") or "N/A"
            lines.append(f"{status_icon} <b>{r.get('symbol')}</b> ({r.get('provider')}): {r.get('status')} [{completed}]")
    else:
        lines.append("<i>Chưa có lượt chạy thu thập nào được ghi nhận.</i>")

    return "\n".join(lines)


def handle_overview_command(db_path: Path) -> str:
    try:
        assets = get_asset_summaries(db_path)
    except Exception as exc:
        LOGGER.warning("get_asset_summaries_failed error=%s", type(exc).__name__)
        return "⚠️ Không thể đọc dữ liệu tổng quan từ cơ sở dữ liệu."

    if not assets:
        return "ℹ️ Danh mục hiện chưa có dữ liệu giá. Hãy chạy worker thu thập trước."

    lines = ["📈 <b>Tổng quan Thị trường (Market Overview)</b>\n"]
    for a in assets:
        symbol = a.get("symbol", "N/A")
        price = a.get("price")
        change = a.get("change_pct")
        freshness = a.get("freshness", "unknown")

        if price is not None:
            price_str = f"${float(price):,.2f}"
            change_str = f"{float(change):+.2f}%" if change is not None else "0.00%"
            direction_icon = "🟢" if (change or 0) >= 0 else "🔴"
            lines.append(f"{direction_icon} <b>{symbol}</b>: {price_str} (<b>{change_str}</b>) [{freshness}]")
        else:
            lines.append(f"⚪ <b>{symbol}</b>: N/A [{freshness}]")

    lines.append("\n<i>Dữ liệu nghiên cứu thị trường, không phải lời khuyên đầu tư.</i>")
    return "\n".join(lines)


def handle_news_command(db_path: Path, args: list[str]) -> str:
    symbol = args[0].strip().upper() if args else None
    try:
        articles, total = get_news_articles(db_path, asset=symbol, limit=3)
    except Exception as exc:
        LOGGER.warning("get_news_articles_failed error=%s", type(exc).__name__)
        return "⚠️ Lỗi khi truy vấn tin tức."

    target_desc = f" ({symbol})" if symbol else ""
    if not articles:
        return f"ℹ️ Không có tin tức mới{target_desc} trong hệ thống."

    lines = [f"📰 <b>Tin tức Thị trường Mới nhất{target_desc}</b> ({total} tin)\n"]
    for idx, art in enumerate(articles, 1):
        title = html.escape(str(art.get("title", "")))
        source = html.escape(str(art.get("source", "Unknown")))
        url = str(art.get("url", ""))
        category = art.get("category", "other")
        published = art.get("published_at") or art.get("collected_at") or ""
        summary = str(art.get("summary") or "").strip()
        if len(summary) > 200:
            summary = summary[:197] + "..."

        lines.append(f"<b>{idx}. {title}</b>")
        lines.append(f"Nguồn: {source} | Danh mục: {category} | {published[:16]}")
        if summary:
            lines.append(f"<i>{html.escape(summary)}</i>")
        if url:
            lines.append(f"🔗 <a href=\"{html.escape(url)}\">Đọc bài viết gốc</a>")
        lines.append("")

    return "\n".join(lines)


def build_daily_briefing(db_path: Path = DB_PATH) -> tuple[str, str]:
    today_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    title = f"🌅 Bản tin Thị trường Buổi sáng ({today_str})"

    # Market overview
    overview_text = handle_overview_command(db_path)

    # Top 3 latest news
    news_text = handle_news_command(db_path, [])

    message = (
        f"<b>{title}</b>\n\n"
        f"{overview_text}\n\n"
        f"{news_text}\n"
        "Chúc bạn một ngày giao dịch và nghiên cứu hiệu quả!"
    )
    return title, message


def send_daily_briefing(
    db_path: Path = DB_PATH,
    session: requests.Session | None = None,
) -> tuple[bool, str | None]:
    title, message = build_daily_briefing(db_path)
    return send_telegram_message(title, message, session=session)


def handle_command(text: str, chat_id: int | str, db_path: Path = DB_PATH) -> str | None:
    if not is_authorized_chat(chat_id):
        LOGGER.warning("unauthorized_chat_command_ignored chat_id=%s", chat_id)
        return None

    cleaned = text.strip()
    if not cleaned:
        return None

    parts = cleaned.split()
    cmd = parts[0].lower().split("@")[0]  # strip bot username if present, e.g. /status@MyBot
    args = parts[1:]

    if cmd in {"/start", "/help"}:
        return (
            "🤖 <b>Market Intelligence Telegram Assistant</b>\n\n"
            "Các lệnh hỗ trợ:\n"
            "• <code>/status</code>: Kiểm tra tình trạng hệ thống, độ tươi dữ liệu & job chạy\n"
            "• <code>/overview</code>: Giá hiện tại và % thay đổi của danh mục\n"
            "• <code>/news [mã]</code>: 3 tin tức mới nhất (VD: <code>/news BTC</code> hoặc <code>/news</code>)\n"
            "• <code>/briefing</code>: Nhận ngay bản tin tổng hợp thị trường hôm nay\n"
        )
    elif cmd == "/status":
        return handle_status_command(db_path)
    elif cmd == "/overview":
        return handle_overview_command(db_path)
    elif cmd == "/news":
        return handle_news_command(db_path, args)
    elif cmd == "/briefing":
        _t, msg = build_daily_briefing(db_path)
        return msg
    else:
        return (
            "❓ Lệnh không hợp lệ. Hãy gõ <code>/help</code> để xem danh sách các lệnh hỗ trợ."
        )


def poll_updates_once(
    offset: int,
    session: requests.Session,
    db_path: Path = DB_PATH,
) -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return offset

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": 5}

    try:
        resp = session.get(url, params=params, timeout=(5, 15))
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            return offset

        updates = payload.get("result", [])
        for update in updates:
            update_id = update["update_id"]
            offset = max(offset, update_id + 1)
            msg = update.get("message")
            if not msg:
                continue

            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            if not chat_id or not text:
                continue

            reply_text = handle_command(text, chat_id, db_path)
            if reply_text:
                send_telegram_message(None, reply_text, session=session)

    except Exception as exc:
        LOGGER.warning("telegram_poll_updates_error error=%s", type(exc).__name__)

    return offset


def run_bot(db_path: Path = DB_PATH) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not is_telegram_enabled() or not is_telegram_configured():
        LOGGER.warning("telegram_bot_disabled reason=not_enabled_or_configured")
        return

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    LOGGER.info("telegram_bot_started polling_active=true")
    session = requests.Session()
    offset = 0
    try:
        while not SHUTDOWN.is_set():
            offset = poll_updates_once(offset, session, db_path)
            SHUTDOWN.wait(2.0)
    finally:
        session.close()
        LOGGER.info("telegram_bot_stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Market Intelligence Telegram Bot & Daily Digest")
    parser.add_argument("--briefing", action="store_true", help="Send daily market briefing and exit")
    args = parser.parse_args()

    if args.briefing:
        logging.basicConfig(level=logging.INFO)
        ok, err = send_daily_briefing()
        if ok:
            print("Daily briefing sent successfully via Telegram.")
        else:
            print(f"Failed to send daily briefing: {err}")
            sys.exit(1)
    else:
        run_bot()


if __name__ == "__main__":
    main()

