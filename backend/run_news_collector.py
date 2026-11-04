import json
import logging

from backend.news import collect_news


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    result = collect_news()
    print(json.dumps(result, indent=2))
    if result["sources_configured"] == 0:
        raise SystemExit("Configure MARKET_NEWS_FEEDS before collecting news")
    if result["sources_failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
