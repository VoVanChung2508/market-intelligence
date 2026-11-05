import html
import os
import requests
from pathlib import Path
import re
from urllib.parse import quote

from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _notification_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=0,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _is_truthy(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().casefold() in {"1", "true", "yes", "on"}


def is_telegram_enabled() -> bool:
    return _is_truthy(os.getenv("TELEGRAM_ENABLED"), False)


def is_telegram_configured() -> bool:
    return bool(
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_CHAT_ID", "").strip()
    )


def _telegram_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=0,
        status=3,
        backoff_factor=1,
        status_forcelist=(429,),
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def send_telegram_message(
    title: str | None,
    message: str,
    *,
    parse_mode: str = "HTML",
    session: requests.Session | None = None,
) -> tuple[bool, str | None]:
    if not is_telegram_enabled():
        return False, "disabled"
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "not_configured"

    safe_title = html.escape(title.strip()) if title and title.strip() else ""
    safe_text = html.escape(message.strip()) if message and message.strip() else ""
    if safe_title:
        formatted = f"<b>{safe_title}</b>\n{safe_text}"
    else:
        formatted = safe_text

    if len(formatted) > 4000:
        formatted = formatted[:3997] + "..."

    own_session = session is None
    client = session or _telegram_session()
    try:
        response = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": formatted, "parse_mode": parse_mode},
            timeout=(5, 15),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return False, "provider_rejected"
        return True, None
    except requests.RequestException as exc:
        return False, type(exc).__name__
    except ValueError:
        return False, "invalid_provider_response"
    finally:
        if own_session:
            client.close()


def send_notification(title, message, priority="default", sequence_id=None):
    server = (
        os.getenv("NTFY_BASE_URL", "").strip()
        or os.getenv("NTFY_SERVER", "https://ntfy.sh")
    ).rstrip("/")
    topic = os.getenv("NTFY_TOPIC", "").strip()

    if os.getenv("NTFY_ENABLED", "true").strip().casefold() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    if not topic:
        print("Chua cau hinh NTFY_TOPIC trong file .env")
        return False

    if not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", topic):
        print("NTFY_TOPIC khong hop le")
        return False

    endpoint = f"{server}/{topic}"
    if sequence_id is not None:
        if not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", sequence_id):
            print("Ma dedupe ntfy khong hop le")
            return False
        endpoint = f"{endpoint}/{quote(sequence_id, safe='')}"

    try:
        with _notification_session() as session:
            response = session.post(
                endpoint,
                data=message.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": str(priority),
                },
                timeout=(5, 15),
            )
        response.raise_for_status()
        print("Da gui thong bao ntfy thanh cong")
        return True

    except requests.RequestException as exc:
        print(f"Khong gui duoc thong bao ntfy ({type(exc).__name__})")
        return False


if __name__ == "__main__":
    send_notification(
        "Market AI: Kiem tra",
        "He thong thong bao dang hoat dong.",
    )
