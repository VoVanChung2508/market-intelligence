try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]
import logging

from backend.collector import collect_once
from backend.settings import DB_PATH


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    lock_path = DB_PATH.parent / "market-collector.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                raise RuntimeError("Another market collector already holds the lock") from exc
        collect_once()


if __name__ == "__main__":
    main()