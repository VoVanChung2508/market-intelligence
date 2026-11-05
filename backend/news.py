import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

from backend.database import (
    connect,
    initialize_database,
    store_news_articles,
)
from backend.settings import DB_PATH
from backend.ai_provider import GeminiAIProvider, translate_sentiment
from src.notify import (
    is_telegram_configured,
    is_telegram_enabled,
    send_telegram_message,
)

LOGGER = logging.getLogger(__name__)
MAX_FEEDS = 20
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS_PER_FEED = 200
ARTICLE_CATEGORIES = (
    "earnings",
    "mergers_acquisitions",
    "regulatory",
    "monetary_policy",
    "inflation",
    "employment",
    "geopolitics",
    "product",
    "security",
    "markets",
    "other",
)

CATEGORY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earnings", ("earnings", "quarterly results", "revenue", "profit warning")),
    ("mergers_acquisitions", ("merger", "acquisition", "takeover", "to acquire")),
    ("regulatory", ("regulator", "regulatory", "antitrust", "lawsuit", "sanction")),
    ("monetary_policy", ("interest rate", "central bank", "federal reserve", "rate decision")),
    ("inflation", ("inflation", "consumer price index", "cpi", "pce")),
    ("employment", ("unemployment", "payroll", "jobs report", "employment report")),
    ("geopolitics", ("geopolitical", "war ", "invasion", "trade dispute", "election")),
    ("product", ("launches", "unveils", "new product", "product announcement")),
    ("security", ("cyberattack", "security breach", "data breach", "exploit")),
    ("markets", ("stock market", "cryptocurrency", "bitcoin", "market close")),
)

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}
SENSITIVE_PARAMETERS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "key",
    "password",
    "secret",
    "signature",
    "token",
}


@dataclass(frozen=True)
class NewsFeed:
    name: str
    url: str


class NewsProvider(Protocol):
    def fetch(self, feed: NewsFeed, session: requests.Session) -> bytes: ...


class RSSAtomProvider:
    def fetch(self, feed: NewsFeed, session: requests.Session) -> bytes:
        response = session.get(
            feed.url,
            headers={"Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"},
            timeout=(3, 10),
            allow_redirects=False,
            stream=True,
        )
        try:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ValueError("Feed redirects are not followed")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FEED_BYTES:
                raise ValueError("Feed response exceeds the configured size limit")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > MAX_FEED_BYTES:
                    raise ValueError("Feed response exceeds the configured size limit")
            return bytes(body)
        finally:
            response.close()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _plain_text(value: str | None) -> str | None:
    if not value:
        return None
    extractor = _TextExtractor()
    extractor.feed(html.unescape(value))
    text = " ".join(" ".join(extractor.parts).split())
    return text or None


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _child(element: ET.Element, *names: str) -> ET.Element | None:
    for candidate in element:
        if _local_name(candidate) in names:
            return candidate
    return None


def _element_text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return "".join(element.itertext()).strip() or None


def _entry_link(entry: ET.Element, feed_url: str) -> str | None:
    for element in entry:
        if _local_name(element) != "link":
            continue
        href = element.attrib.get("href")
        rel = element.attrib.get("rel", "alternate")
        if href and rel == "alternate":
            return urljoin(feed_url, href)
        if href and not element.attrib.get("rel"):
            return urljoin(feed_url, href)
        text = _element_text(element)
        if text:
            return urljoin(feed_url, text)
    return None


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Article link must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Article link must not include credentials")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("Article link host must be publicly routable")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Article link host must be publicly routable")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Article link has an invalid port") from exc
    scheme = parsed.scheme.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    netloc = hostname if port is None or port == (443 if scheme == "https" else 80) else f"{hostname}:{port}"
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMETERS
        and not _is_sensitive_parameter(key)
        and not key.lower().startswith("utm_")
    ]
    return urlunsplit(
        (
            scheme,
            netloc,
            parsed.path or "/",
            urlencode(sorted(query)),
            "",
        )
    )


def _published_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sensitive_parameter(name: str) -> bool:
    lowered = name.casefold()
    return lowered in SENSITIVE_PARAMETERS or any(
        marker in lowered for marker in ("token", "key", "secret", "auth", "password", "signature")
    )


def _classify(title: str, summary: str | None, content: str | None) -> str:
    text = f" {_normalized_text(' '.join(part for part in (title, summary, content) if part))} "
    for category, terms in CATEGORY_TERMS:
        if any(term in text for term in terms):
            return category
    return "other"


def _related_assets(text: str, assets: dict[str, str]) -> list[str]:
    matches: list[str] = []
    for symbol, name in assets.items():
        symbol_pattern = rf"(?<![\w]){re.escape(symbol)}(?![\w])"
        name_pattern = rf"(?<![\w]){re.escape(name)}(?![\w])" if name else None
        if re.search(symbol_pattern, text, flags=re.IGNORECASE) or (
            name_pattern and re.search(name_pattern, text, flags=re.IGNORECASE)
        ):
            matches.append(symbol)
    return sorted(matches)


def _validated_feed(name: object, url: object) -> NewsFeed:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("Each news feed must have a name of 1 to 80 characters")
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Each news feed must have a valid HTTPS URL")
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("News feed URLs must be HTTPS and must not include credentials or custom ports")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("News feed host must be publicly routable")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("News feed host must be publicly routable")
    return NewsFeed(name.strip(), url.strip())


def configured_news_feeds(raw_value: str | None = None) -> list[NewsFeed]:
    raw = os.getenv("MARKET_NEWS_FEEDS", "") if raw_value is None else raw_value
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("MARKET_NEWS_FEEDS must contain a JSON array") from exc
    if not isinstance(payload, list) or len(payload) > MAX_FEEDS:
        raise ValueError(f"MARKET_NEWS_FEEDS must be an array of at most {MAX_FEEDS} feeds")
    feeds: list[NewsFeed] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each news feed must be an object with name and url fields")
        feeds.append(_validated_feed(item.get("name"), item.get("url")))
    if len({feed.name.casefold() for feed in feeds}) != len(feeds):
        raise ValueError("News feed names must be unique")
    return feeds


def parse_news_feed(
    document: bytes,
    feed: NewsFeed,
    assets: dict[str, str],
) -> list[dict[str, object]]:
    if len(document) > MAX_FEED_BYTES:
        raise ValueError("Feed response exceeds the configured size limit")
    if b"<!DOCTYPE" in document.upper():
        raise ValueError("Feed document types are not accepted")
    root = ET.fromstring(document)
    root_name = _local_name(root)
    channel = _child(root, "channel") if root_name == "rss" else root
    if channel is None or root_name not in {"rss", "feed"}:
        raise ValueError("Document is not an RSS or Atom feed")
    entries = [
        element for element in channel
        if _local_name(element) in {"item", "entry"}
    ][:MAX_ITEMS_PER_FEED]
    feed_language = root.attrib.get("{http://www.w3.org/XML/1998/namespace}lang")
    articles: list[dict[str, object]] = []
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for entry in entries:
        title = _plain_text(_element_text(_child(entry, "title")))
        if not title:
            continue
        link = _entry_link(entry, feed.url)
        if not link:
            continue
        try:
            canonical_url = _normalize_url(link)
        except ValueError:
            continue

        summary = _plain_text(_element_text(_child(entry, "summary", "description")))
        content = _plain_text(_element_text(_child(entry, "content", "encoded")))
        published_value = _element_text(
            _child(entry, "published", "pubdate", "date")
        ) or _element_text(_child(entry, "updated"))
        published_at = _published_at(published_value)
        language = (
            entry.attrib.get("{http://www.w3.org/XML/1998/namespace}lang")
            or feed_language
        )
        language = language.strip().lower()[:16] if language else None
        text = " ".join(part for part in (title, summary, content) if part)
        title_fingerprint = _fingerprint(_normalized_text(title))
        date_key = published_at or ""
        content_fingerprint = _fingerprint(
            "\n".join(
                (date_key, _normalized_text(title), _normalized_text(summary or ""), _normalized_text(content or ""))
            )
        )
        articles.append(
            {
                "canonical_url": canonical_url,
                "title": title,
                "summary": summary or (content[:500] if content else None),
                "content": content,
                "published_at": published_at,
                "source": feed.name,
                "source_url": _normalize_url(feed.url),
                "language": language,
                "category": _classify(title, summary, content),
                "title_fingerprint": title_fingerprint,
                "content_fingerprint": content_fingerprint,
                "collected_at": collected_at,
                "related_assets": _related_assets(text, assets),
            }
        )
    return articles


def _asset_names(db_path: Path) -> dict[str, str]:
    connection = connect(db_path)
    try:
        return {
            row["symbol"]: row["name"]
            for row in connection.execute(
                "SELECT symbol, name FROM assets ORDER BY symbol"
            ).fetchall()
        }
    finally:
        connection.close()


def format_news_telegram_message(
    article: dict[str, object],
    ai_provider: Any = None,
) -> tuple[str, str]:
    title = str(article.get("title", "")).strip()
    source = str(article.get("source", "")).strip()
    category = str(article.get("category", "")).strip()
    assets_list = article.get("related_assets") or []
    assets = ", ".join(assets_list)  # type: ignore[arg-type]
    url = str(article.get("canonical_url", "")).strip()

    header = f"News: {title}" if title else "Market News"
    lines = []
    if source:
        lines.append(f"Source: {source}")
    if category:
        lines.append(f"Category: {category}")
    if assets:
        lines.append(f"Assets: {assets}")

    provider = ai_provider or GeminiAIProvider()
    try:
        analysis = provider.analyze_news(
            title,
            summary=str(article.get("summary") or ""),
            content=str(article.get("content") or ""),
            related_assets=assets_list,  # type: ignore[arg-type]
        )
        ai_summary = analysis.get("summary")
        sentiment = analysis.get("sentiment", "neutral")
        model = analysis.get("model", "keyword-heuristic")
        if ai_summary:
            lines.append(f"AI Summary: {ai_summary}")
        lines.append(f"Impact: {translate_sentiment(sentiment)} [{model}]")
    except Exception as exc:
        LOGGER.warning("news_ai_enrichment_failed error=%s", type(exc).__name__)
        summary = str(article.get("summary") or "").strip()
        if summary:
            if len(summary) > 300:
                summary = summary[:297] + "..."
            lines.append(f"Summary: {summary}")

    if url:
        lines.append(f"Link: {url}")

    return header, "\n".join(lines)


def send_news_telegram_notification(article: dict[str, object]) -> tuple[bool, str | None]:
    if not is_telegram_enabled():
        return False, "disabled"
    if not is_telegram_configured():
        return False, "not_configured"
    if not article.get("related_assets"):
        return False, "no_matched_assets"

    header, body = format_news_telegram_message(article)
    return send_telegram_message(header, body)


def collect_news(
    feeds: list[NewsFeed] | None = None,
    db_path: Path = DB_PATH,
    session: requests.Session | None = None,
    provider: NewsProvider | None = None,
    notify_telegram: bool = True,
) -> dict[str, int]:
    selected_feeds = configured_news_feeds() if feeds is None else feeds
    initialize_database(db_path)
    if not selected_feeds:
        LOGGER.warning("news_collection_skipped reason=no_feeds_configured")
        return {
            "sources_configured": 0,
            "sources_succeeded": 0,
            "sources_failed": 0,
            "articles_received": 0,
            "articles_inserted": 0,
        }

    own_session = session is None
    client = session or requests.Session()
    feed_provider = provider or RSSAtomProvider()
    names = _asset_names(db_path)
    result = {
        "sources_configured": len(selected_feeds),
        "sources_succeeded": 0,
        "sources_failed": 0,
        "articles_received": 0,
        "articles_inserted": 0,
    }
    try:
        for feed in selected_feeds:
            try:
                document = feed_provider.fetch(feed, client)
                articles = parse_news_feed(document, feed, names)
            except (requests.RequestException, ValueError, ET.ParseError) as exc:
                result["sources_failed"] += 1
                LOGGER.warning(
                    "news_feed_failed source=%s error=%s",
                    feed.name,
                    type(exc).__name__,
                )
                continue
            result["sources_succeeded"] += 1
            result["articles_received"] += len(articles)
            new_articles: list[dict[str, object]] = []
            inserted_count = store_news_articles(db_path, articles, inserted_articles=new_articles)
            result["articles_inserted"] += inserted_count
            if notify_telegram and is_telegram_enabled() and is_telegram_configured():
                for article in new_articles:
                    if article.get("related_assets"):
                        try:
                            send_news_telegram_notification(article)
                        except Exception as exc:
                            LOGGER.warning(
                                "news_telegram_notification_failed error=%s",
                                type(exc).__name__,
                            )
            LOGGER.info(
                "news_feed_collected source=%s received=%s",
                feed.name,
                len(articles),
            )
    finally:
        if own_session:
            client.close()
    return result
