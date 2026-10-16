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


def send_notification(title, message, priority="default", sequence_id=None):
    server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    topic = os.getenv("NTFY_TOPIC", "").strip()

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

