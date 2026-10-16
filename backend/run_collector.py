import logging

from backend.collector import collect_once


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    collect_once()


if __name__ == "__main__":
    main()