import subprocess
import sys
from datetime import datetime
from pathlib import Path

from notify import send_notification

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "src" / "collect.py"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


def log(message):
    timestamp = datetime.now().astimezone().isoformat()
    text = f"[{timestamp}] {message}"

    print(text, flush=True)

    with (LOG_DIR / "collector.log").open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def main():
    log("Bat dau thu thap du lieu")

    try:
        result = subprocess.run(
            [sys.executable, str(COLLECTOR)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.stdout:
            log(result.stdout.strip())

        if result.returncode != 0:
            detail = (result.stderr or "Loi khong ro")[-2000:]
            log(f"THU THAP THAT BAI: {detail}")

            send_notification(
                "Market AI: Loi thu thap",
                detail,
                priority="high",
            )

            raise SystemExit(result.returncode)

        log("Thu thap hoan tat")

    except subprocess.TimeoutExpired:
        log("THU THAP THAT BAI: Qua thoi gian 600 giay")

        send_notification(
            "Market AI: Qua thoi gian cho phep",
            "Tac vu thu thap da vuot qua 600 giay.",
            priority="high",
        )

        raise SystemExit(1)

    except Exception as exc:
        log(f"LOI HE THONG: {exc}")

        send_notification(
            "Market AI: Loi he thong",
            str(exc)[:1500],
            priority="high",
        )

        raise SystemExit(1)


if __name__ == "__main__":
    main()
