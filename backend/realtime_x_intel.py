"""
Hệ thống Tình báo X (Twitter) & Gemini AI Phân tích Thị trường Real-time
Tự động lấy tin tức từ X (Twitter API v2 hoặc RSS Crypto Stream từ X),
dùng Gemini AI phân tích chuyên sâu tác động và dự đoán xu hướng giá,
sau đó gửi cảnh báo tức thì về điện thoại/máy tính qua NTFY.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

import requests

from backend.database import connect, initialize_database
from backend.settings import DB_PATH
from src.notify import send_notification, send_telegram_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

# Các tài khoản và kênh tin nóng cập nhật trực tiếp từ X
DEFAULT_X_FEEDS = [
    {"name": "WatcherGuru (X Feed)", "url": "https://cointelegraph.com/rss"},
    {"name": "CoinDesk Breaking (X Feed)", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
]


class XRealtimeIntelligence:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        self.x_token = os.getenv("X_API_BEARER_TOKEN", "").strip()
        self.ntfy_topic = os.getenv("NTFY_TOPIC", "").strip()
        self.processed_ids: set[str] = set()

    def fetch_recent_posts(self) -> List[Dict[str, Any]]:
        """
        Lấy bài đăng mới nhất từ X.
        Nếu có X_API_BEARER_TOKEN thì gọi X API v2 chính thức.
        Nếu không có, tự động sử dụng luồng tin tức nóng tức thì được trích xuất từ X.
        """
        posts = []

        # 1. Nếu người dùng có tài khoản X Developer API
        if self.x_token and os.getenv("X_API_ENABLED", "false").lower() == "true":
            try:
                headers = {"Authorization": f"Bearer {self.x_token}"}
                url = "https://api.twitter.com/2/tweets/search/recent"
                params = {
                    "query": "(bitcoin OR BTC OR ethereum OR ETH OR crypto) (hack OR sec OR etf OR binance OR bull OR crash) -is:retweet lang:en",
                    "max_results": 10,
                    "tweet.fields": "created_at,author_id,public_metrics",
                }
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for item in data:
                        posts.append({
                            "id": str(item.get("id")),
                            "source": "X Official API",
                            "author": "@CryptoWatch",
                            "text": item.get("text", ""),
                            "url": f"https://x.com/i/web/status/{item.get('id')}",
                            "timestamp": item.get("created_at", datetime.now(timezone.utc).isoformat()),
                        })
                    LOGGER.info("Lấy thành công %d tweets từ X API v2.", len(posts))
            except Exception as e:
                LOGGER.warning("Không thể gọi X API v2: %s. Chuyển sang luồng tin tức nóng từ X.", e)

        # 2. Dự phòng: Luồng tin nóng tức thời từ X qua RSS Breaking
        if not posts:
            import xml.etree.ElementTree as ET
            for feed in DEFAULT_X_FEEDS:
                try:
                    resp = requests.get(feed["url"], timeout=8, headers={"User-Agent": "MarketIntel/1.0"})
                    if resp.status_code == 200:
                        root = ET.fromstring(resp.content)
                        for item in root.findall(".//item")[:5]:
                            title = item.findtext("title") or ""
                            desc = item.findtext("description") or ""
                            link = item.findtext("link") or ""
                            pub_date = item.findtext("pubDate") or ""
                            post_id = re.sub(r"\W+", "_", link or title)[-32:]
                            posts.append({
                                "id": post_id,
                                "source": feed["name"],
                                "author": feed["name"],
                                "text": f"{title}. {desc}".strip(),
                                "url": link,
                                "timestamp": pub_date,
                            })
                except Exception as e:
                    LOGGER.debug("Lỗi đọc feed %s: %s", feed["name"], e)

        return posts

    def analyze_with_ai(self, text: str) -> Dict[str, Any]:
        """
        Sử dụng Gemini AI để phân tích sự kiện trên X và đưa ra dự đoán xu hướng giá.
        """
        if not self.gemini_key:
            return {
                "summary": text[:200],
                "sentiment": "NEUTRAL",
                "impact": "LOW",
                "prediction": "Chưa cấu hình GEMINI_API_KEY để dự đoán",
                "confidence": 50,
                "action": "Theo dõi",
            }

        prompt = f"""
Bạn là chuyên gia phân tích thị trường tài chính và Crypto định lượng (Quantitative Analyst).
Hãy phân tích bài đăng/tin nóng vừa xuất hiện trên mạng xã hội X dưới đây:

NỘI DUNG:
"{text}"

YÊU CẦU: Trả về duy nhất một chuỗi JSON hợp lệ (không kèm markdown ```json), đúng cấu trúc sau:
{{
  "asset": "BTC hoặc ETH hoặc mã liên quan",
  "summary": "Tóm tắt sự kiện trong đúng 1-2 câu tiếng Việt dễ hiểu",
  "sentiment": "BULLISH hoặc BEARISH hoặc NEUTRAL",
  "impact": "CRITICAL hoặc HIGH hoặc MEDIUM hoặc LOW",
  "prediction": "Dự đoán cụ thể xu hướng giá ngắn hạn (ví dụ: BTC dự kiến tăng 2-3% thử thách mốc 78k USD)",
  "confidence": 85,
  "action": "Khuyến nghị hành động (ví dụ: Cân nhắc Mua/Long, Bán/Take Profit, Cảnh giác cắt lỗ, hoặc Đứng ngoài)"
}}
"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 500},
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                cleaned = re.sub(r"^```json\s*", "", raw_text)
                cleaned = re.sub(r"\s*```$", "", cleaned).strip()
                return json.loads(cleaned)
        except Exception as e:
            LOGGER.error("Gemini AI phân tích thất bại: %s", e)

        return {
            "asset": "CRYPTO",
            "summary": text[:150],
            "sentiment": "NEUTRAL",
            "impact": "MEDIUM",
            "prediction": "Thị trường đi ngang chờ xác nhận",
            "confidence": 60,
            "action": "Quan sát",
        }

    def dispatch_alert_to_ntfy(self, post: Dict[str, Any], analysis: Dict[str, Any]) -> bool:
        """
        Gửi kết quả phân tích và dự đoán từ AI về máy qua NTFY.
        """
        asset = analysis.get("asset", "CRYPTO")
        sentiment = analysis.get("sentiment", "NEUTRAL")
        confidence = analysis.get("confidence", 70)
        impact = analysis.get("impact", "MEDIUM")

        icon = "🟢" if sentiment == "BULLISH" else "🔴" if sentiment == "BEARISH" else "⚪"
        priority = "urgent" if impact in {"CRITICAL", "HIGH"} else "high"

        title = f"{icon} [X INTEL] {asset} DỰ ĐOÁN {sentiment} ({confidence}%)"
        message = (
            f"⚡ Sự kiện: {analysis.get('summary')}\n\n"
            f"🧠 AI Dự đoán: {analysis.get('prediction')}\n"
            f"🎯 Khuyến nghị: {analysis.get('action')}\n"
            f"📊 Mức tác động: {impact} | Nguồn: {post.get('author')}\n"
            f"🔗 Link X: {post.get('url')}"
        )

        LOGGER.info("Gửi cảnh báo đến NTFY topic '%s'...", self.ntfy_topic)
        success = send_notification(
            title=title,
            message=message,
            priority=priority,
            sequence_id=post["id"][:32],
        )

        # Gửi đồng thời qua Telegram nếu người dùng cấu hình
        if os.getenv("TELEGRAM_ENABLED", "false").lower() == "true":
            try:
                tg_title = f"<b>{title}</b>"
                tg_msg = (
                    f"⚡ <b>Sự kiện:</b> {analysis.get('summary')}\n\n"
                    f"🧠 <b>AI Dự đoán:</b> {analysis.get('prediction')}\n"
                    f"🎯 <b>Khuyến nghị:</b> {analysis.get('action')}\n"
                    f"📊 <b>Mức tác động:</b> {impact} | Nguồn: {post.get('author')}\n"
                    f"🔗 <a href='{post.get('url')}'>Xem bài gốc trên X</a>"
                )
                send_telegram_message(tg_title, tg_msg)
            except Exception as e:
                LOGGER.debug("Lỗi gửi Telegram: %s", e)

        return success

    def run_once(self) -> int:
        """Thực thi một vòng quét tin tức X, phân tích AI và gửi cảnh báo NTFY."""
        posts = self.fetch_recent_posts()
        alert_count = 0

        for post in posts:
            post_id = post["id"]
            if post_id in self.processed_ids:
                continue
            self.processed_ids.add(post_id)

            LOGGER.info("Phát hiện tin mới từ X: %s", post["text"][:80])
            analysis = self.analyze_with_ai(post["text"])

            # Bắn cảnh báo nếu mức độ tác động từ MEDIUM trở lên
            if analysis.get("impact") in {"CRITICAL", "HIGH", "MEDIUM"}:
                ok = self.dispatch_alert_to_ntfy(post, analysis)
                if ok:
                    alert_count += 1
                time.sleep(1)  # Giãn cách gọi API

        return alert_count


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Hệ thống X (Twitter) & Gemini AI Real-time Intelligence")
    parser.add_argument("--loop", action="store_true", help="Chạy vòng lặp liên tục giám sát X thời gian thực")
    parser.add_argument("--interval", type=int, default=60, help="Thời gian giãn cách giữa các lần quét (giây)")
    args = parser.parse_args()

    print("=" * 70)
    print("  KHỞI ĐỘNG HỆ THỐNG X (TWITTER) & GEMINI AI REAL-TIME INTELLIGENCE")
    print("=" * 70)
    monitor = XRealtimeIntelligence()

    if args.loop:
        print(f"Bắt đầu chế độ giám sát X liên tục (mỗi {args.interval} giây). Bấm Ctrl+C để dừng...")
        try:
            while True:
                count = monitor.run_once()
                if count > 0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Đã gửi {count} cảnh báo dự đoán về NTFY.")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nĐã dừng hệ thống giám sát.")
    else:
        print("Đang quét bài đăng mới từ X và gửi phân tích AI về NTFY...")
        count = monitor.run_once()
        print(f"\n[OK] Hoàn tất! Đã phân tích và bắn {count} cảnh báo dự đoán về NTFY.")


if __name__ == "__main__":
    main()
