import fcntl
import logging
import os
import signal
import threading

from backend.settings import DB_PATH
from backend.social import collect_social_once

LOGGER = logging.getLogger(__name__)
SHUTDOWN = threading.Event()


def _request_shutdown(_signum: int, _frame: object) -> None:
    SHUTDOWN.set()


def monitor_interval_seconds() -> int:
    raw_value = os.getenv("SOCIAL_MONITOR_INTERVAL_SECONDS", "300")
    try:
        interval = int(raw_value)
    except ValueError as exc:
        raise ValueError("SOCIAL_MONITOR_INTERVAL_SECONDS must be an integer") from exc
    if not 30 <= interval <= 86400:
        raise ValueError("SOCIAL_MONITOR_INTERVAL_SECONDS must be between 30 and 86400")
    return interval


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    interval = monitor_interval_seconds()
    lock_path = DB_PATH.parent / "social-monitor.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another social monitor process already holds the lock") from exc
        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)
        LOGGER.info("social_monitor_started interval_seconds=%s", interval)
        while not SHUTDOWN.is_set():
            result = collect_social_once()
            LOGGER.info(
                "social_collection_finished status=%s received=%s inserted=%s events=%s",
                result["status"],
                result["posts_received"],
                result["posts_inserted"],
                result["events_created"],
            )
            SHUTDOWN.wait(interval)
        LOGGER.info("social_monitor_stopped")


if __name__ == "__main__":
    run()
