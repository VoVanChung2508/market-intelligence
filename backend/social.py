import hashlib
import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.database import connect, get_crypto_watchlist, initialize_database
from backend.settings import DB_PATH
from src.notify import send_notification

LOGGER = logging.getLogger(__name__)
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
X_MAX_RESULTS = 100
SOCIAL_MAX_PAGES = 5
SOCIAL_EVENT_CATEGORIES = (
    "EXCHANGE_LISTING",
    "EXCHANGE_DELISTING",
    "TOKEN_LAUNCH",
    "TOKEN_UNLOCK",
    "TOKEN_BURN",
    "WHALE_ACTIVITY",
    "PARTNERSHIP",
    "NETWORK_UPGRADE",
    "MAINNET_LAUNCH",
    "ETF_NEWS",
    "REGULATORY_NEWS",
    "SECURITY_HACK",
    "EXPLOIT",
    "SCAM_WARNING",
    "AIRDROP",
    "STAKING",
    "GOVERNANCE",
    "EXCHANGE_OUTAGE",
    "MARKET_CRASH",
    "MARKET_RALLY",
    "INFLUENCER_STATEMENT",
    "GENERAL_CRYPTO_NEWS",
)
SOURCE_CATEGORIES = (
    "official_project",
    "official_exchange",
    "regulator",
    "news",
    "analyst",
    "influencer",
    "community",
    "unknown",
)
SOCIAL_SENTIMENTS = ("positive", "negative", "neutral", "mixed")
SOCIAL_PRIORITIES = ("critical", "high", "medium", "low")

EVENT_RULES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("EXCHANGE_DELISTING", ("delist", "will remove trading", "trading pair removal"), 82),
    ("EXCHANGE_LISTING", ("will list", "listing on", "now available for trading"), 78),
    ("TOKEN_UNLOCK", ("token unlock", "vesting unlock", "unlock event"), 70),
    ("TOKEN_BURN", ("token burn", "burned tokens", "burning tokens"), 68),
    ("TOKEN_LAUNCH", ("token launch", "launches token", "new token launch"), 68),
    ("MAINNET_LAUNCH", ("mainnet launch", "mainnet is live", "launched its mainnet"), 76),
    ("NETWORK_UPGRADE", ("network upgrade", "hard fork", "protocol upgrade"), 68),
    ("SECURITY_HACK", ("was hacked", "security breach", "private keys compromised"), 94),
    ("EXPLOIT", ("exploit", "drained funds", "vulnerability exploited"), 94),
    ("SCAM_WARNING", ("confirmed scam", "scam warning", "phishing scam"), 80),
    ("EXCHANGE_OUTAGE", ("exchange outage", "withdrawals suspended", "trading halted"), 88),
    ("ETF_NEWS", ("spot etf", "etf approval", "etf filing"), 80),
    ("REGULATORY_NEWS", ("sec approved", "regulatory action", "court ruling", "regulator"), 82),
    ("PARTNERSHIP", ("strategic partnership", "partnership with", "partners with"), 68),
    ("AIRDROP", ("airdrop", "token distribution"), 56),
    ("STAKING", ("staking", "validator rewards"), 50),
    ("GOVERNANCE", ("governance proposal", "governance vote", "dao vote"), 56),
    ("WHALE_ACTIVITY", ("whale moved", "whale transfers", "large transfer"), 58),
    ("MARKET_CRASH", ("market crash", "plunges", "crashes by"), 82),
    ("MARKET_RALLY", ("market rally", "surges", "rallies by"), 70),
)

UNVERIFIED_PATTERN = re.compile(
    r"\b(rumou?red|rumor|unconfirmed|allegedly|reportedly|claims?|speculation|"
    r"not confirmed|unverified)\b",
    re.IGNORECASE,
)
POSITIVE_TERMS = {
    "approval", "approved", "bullish", "growth", "launch", "partnership",
    "rally", "record", "surge", "upgrade", "success", "adoption",
}
NEGATIVE_TERMS = {
    "attack", "ban", "bearish", "breach", "crash", "decline", "delist",
    "exploit", "hack", "lawsuit", "loss", "plunge", "scam", "suspend",
    "outage", "drained",
}
CRITICAL_CATEGORIES = {
    "SECURITY_HACK",
    "EXPLOIT",
    "EXCHANGE_OUTAGE",
    "REGULATORY_NEWS",
}
RELEVANT_TERMS = {
    "bitcoin", "ethereum", "crypto", "cryptocurrency", "blockchain", "token",
    "solana", "binance", "ripple",
}
MENTION_STOPWORDS = {"USD", "CEO", "ETF", "SEC", "USA", "FBI", "API", "ATH", "TVL"}


class SocialProviderError(Exception):
    pass


class SocialProvider(Protocol):
    def search(
        self,
        query: str,
        *,
        since_id: str | None,
        pagination_token: str | None,
    ) -> dict[str, Any]: ...


class SocialAIAnalysis(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    related_assets: list[str] = Field(max_length=50)
    event_category: str
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    impact_score: float = Field(ge=0, le=100)
    confidence_score: float = Field(ge=0, le=100)
    urgency: Literal["critical", "high", "medium", "low"]
    potential_market_implications: str = Field(max_length=1000)
    verification_status: Literal["verified", "unverified", "speculative"]
    original_source_url: str


class SocialAIProvider(Protocol):
    def analyze(self, post: dict[str, Any]) -> dict[str, Any]: ...


def analyze_with_provider(
    post: dict[str, Any],
    provider: SocialAIProvider,
) -> SocialAIAnalysis:
    result = provider.analyze(post)
    if not isinstance(result, dict):
        raise ValueError("Social AI provider must return an object")
    return SocialAIAnalysis.model_validate(
        {
            **result,
            "original_source_url": post["post_url"],
        }
    )


@dataclass(frozen=True)
class SocialSource:
    account_id: str
    username: str
    display_name: str
    verified: bool = False


def _enabled(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _ai_status() -> str:
    return (
        "not_configured"
        if _enabled(os.getenv("SOCIAL_AI_ENABLED"), True)
        else "disabled"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc(value: str | None) -> str:
    if not value:
        return _now()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _http_session(*, retry_post: bool = False) -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=0 if retry_post else 2,
        status=3,
        backoff_factor=1,
        status_forcelist=(429,) if retry_post else (429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}) if retry_post else frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def build_search_query(assets: list[dict[str, str]]) -> str:
    expressions: list[str] = []
    for asset in assets:
        symbol = asset["symbol"].upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}", symbol) or symbol in MENTION_STOPWORDS:
            continue
        terms = [symbol, f"${symbol}"]
        name = asset["name"].strip()
        if name and len(name) <= 48:
            terms.append(f'"{name}"')
        expressions.append(f"({' OR '.join(terms)})")
    if not expressions:
        expressions = ["(bitcoin OR BTC OR $BTC)", "(ethereum OR ETH OR $ETH)"]
    query = f"({' OR '.join(expressions)}) -is:retweet"
    return query[:512]


class XApiProvider:
    def __init__(
        self,
        bearer_token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.bearer_token = (bearer_token or os.getenv("X_API_BEARER_TOKEN", "")).strip()
        self.session = session or _http_session()
        self.owns_session = session is None

    def search(
        self,
        query: str,
        *,
        since_id: str | None,
        pagination_token: str | None,
    ) -> dict[str, Any]:
        if not self.bearer_token:
            raise SocialProviderError("X API is not configured")
        params: dict[str, str | int] = {
            "query": query,
            "max_results": X_MAX_RESULTS,
            "tweet.fields": "created_at,lang,referenced_tweets,public_metrics",
            "expansions": "author_id",
            "user.fields": "name,username,verified",
        }
        if since_id:
            params["since_id"] = since_id
        if pagination_token:
            params["next_token"] = pagination_token
        try:
            response = self.session.get(
                X_RECENT_SEARCH_URL,
                headers={"Authorization": f"Bearer {self.bearer_token}"},
                params=params,
                timeout=(5, 20),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise SocialProviderError(type(exc).__name__) from exc
        except ValueError as exc:
            raise SocialProviderError("Invalid X API response") from exc
        if not isinstance(payload, dict):
            raise SocialProviderError("Invalid X API response")
        return payload

    def close(self) -> None:
        if self.owns_session:
            self.session.close()


def _load_assets(db_path: Path) -> list[dict[str, str]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT a.symbol, a.name FROM assets a "
            "JOIN crypto_watchlist w ON w.symbol = a.symbol "
            "ORDER BY a.symbol"
        ).fetchall()
        return [{"symbol": row["symbol"], "name": row["name"]} for row in rows]
    finally:
        connection.close()


def _source_profile(db_path: Path, source_id: int | None) -> tuple[float, str]:
    if source_id is None:
        return 50.0, "unknown"
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT reputation_score, category FROM social_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        return (
            (float(row["reputation_score"]), str(row["category"]))
            if row
            else (50.0, "unknown")
        )
    finally:
        connection.close()


def _get_or_create_source(db_path: Path, source: SocialSource) -> int:
    now = _now()
    connection = connect(db_path)
    try:
        connection.execute(
            "INSERT INTO social_sources "
            "(account_id, username, display_name, category, verified, "
            "reputation_score, supported_assets, active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'unknown', ?, 50, '[]', 1, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET "
            "username = excluded.username, display_name = excluded.display_name, "
            "verified = excluded.verified, updated_at = excluded.updated_at",
            (
                source.account_id,
                source.username,
                source.display_name,
                int(source.verified),
                now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT id FROM social_sources WHERE account_id = ?",
            (source.account_id,),
        ).fetchone()
        return int(row["id"])
    finally:
        connection.close()


def _match_assets(text: str, assets: list[dict[str, str]]) -> list[str]:
    matches: list[str] = []
    for asset in assets:
        symbol = asset["symbol"].upper()
        if symbol in MENTION_STOPWORDS:
            continue
        token_pattern = rf"(?<![\w])(?:\$)?{re.escape(symbol)}(?![\w])"
        name = asset["name"].strip()
        name_match = bool(
            name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
        )
        if re.search(token_pattern, text, re.IGNORECASE) or name_match:
            matches.append(symbol)
    return sorted(set(matches))


def _sentiment(text: str) -> str:
    tokens = set(re.findall(r"[a-z]+", text.casefold()))
    positive = len(tokens & POSITIVE_TERMS)
    negative = len(tokens & NEGATIVE_TERMS)
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "neutral"


def _classify_events(text: str) -> list[tuple[str, float]]:
    lowered = text.casefold()
    found = [
        (category, impact)
        for category, terms, impact in EVENT_RULES
        if any(term in lowered for term in terms)
    ]
    return found or [("GENERAL_CRYPTO_NEWS", 40.0)]


def analyze_social_post(
    text: str,
    published_at: str,
    assets: list[dict[str, str]],
    source_reputation: float = 50.0,
    source_category: str = "unknown",
) -> dict[str, Any] | None:
    matched_assets = _match_assets(text, assets)
    relevant = bool(matched_assets) or any(term in text.casefold() for term in RELEVANT_TERMS)
    if not relevant:
        return None

    categories = _classify_events(text)
    if source_category == "influencer" and categories == [("GENERAL_CRYPTO_NEWS", 40.0)]:
        categories = [("INFLUENCER_STATEMENT", 35.0)]
    sentiment = _sentiment(text)
    parsed_date = datetime.fromisoformat(_utc(published_at))
    age_hours = max(
        0.0,
        (datetime.now(timezone.utc) - parsed_date).total_seconds() / 3600,
    )
    freshness_score = max(0.0, 10.0 * (1 - age_hours / 24))
    confidence = min(
        95.0,
        45.0 + (20.0 if matched_assets else 0.0) + (15.0 if len(text.strip()) >= 40 else 0.0),
    )
    events: list[dict[str, Any]] = []
    for category, base_impact in categories:
        unverified = bool(UNVERIFIED_PATTERN.search(text)) or category == "WHALE_ACTIVITY"
        impact = base_impact
        if category == "GENERAL_CRYPTO_NEWS" and any(term in text.casefold() for term in ("official", "announced", "confirms")):
            impact += 10
        priority_score = min(
            100.0,
            0.35 * impact
            + 0.25 * source_reputation
            + 0.25 * confidence
            + (15.0 if matched_assets else 0.0)
            + freshness_score,
        )
        if unverified:
            priority_score = min(priority_score, 79.0)
        if (
            not unverified
            and category in CRITICAL_CATEGORIES
            and impact >= 88
            and priority_score >= 85
        ):
            priority = "critical"
        elif priority_score >= 75:
            priority = "high"
        elif priority_score >= 55:
            priority = "medium"
        else:
            priority = "low"
        events.append(
            {
                "category": category,
                "impact_score": round(impact, 2),
                "confidence_score": round(confidence, 2),
                "priority_score": round(priority_score, 2),
                "priority": priority,
                "sentiment": sentiment,
                "unverified_claim": unverified,
                "symbols": matched_assets,
            }
        )
    return {
        "relevant": True,
        "ai_status": _ai_status(),
        "summary": None,
        "events": events,
    }


def _is_repost(post: dict[str, Any]) -> bool:
    return any(
        isinstance(reference, dict) and reference.get("type") == "retweeted"
        for reference in post.get("referenced_tweets", [])
    )


def _save_post_and_events(
    db_path: Path,
    post: dict[str, Any],
    user: dict[str, Any],
    assets: list[dict[str, str]],
) -> tuple[int, int]:
    post_id = str(post.get("id", ""))
    text = post.get("text")
    if not post_id or not isinstance(text, str) or not text.strip():
        return 0, 0
    account_id = str(user.get("id", post.get("author_id", "")))
    username = str(user.get("username", "unknown"))
    display_name = str(user.get("name", username))[:120]
    verified = bool(user.get("verified", False))
    source_id = (
        _get_or_create_source(
            db_path,
            SocialSource(account_id, username[:80], display_name, verified),
        )
        if account_id
        else None
    )
    published_at = _utc(post.get("created_at"))
    post_url = f"https://x.com/{quote(username, safe='')}/status/{quote(post_id, safe='')}"
    now = _now()
    connection = connect(db_path)
    try:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO social_posts "
            "(provider_post_id, source_id, text, post_url, published_at, language, "
            "is_repost, raw_payload, processing_status, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                post_id,
                source_id,
                text,
                post_url,
                published_at,
                post.get("lang"),
                int(_is_repost(post)),
                json.dumps(post, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        if cursor.rowcount == 0:
            connection.commit()
            return 0, 0
        stored_post_id = int(cursor.lastrowid)
        connection.commit()
    finally:
        connection.close()

    symbols = _match_assets(text, assets)
    reputation, source_category = _source_profile(db_path, source_id)
    analysis = analyze_social_post(
        text,
        published_at,
        assets,
        reputation,
        source_category,
    )
    connection = connect(db_path)
    event_count = 0
    try:
        if analysis is None:
            connection.execute(
                "UPDATE social_posts SET processing_status = 'irrelevant' WHERE id = ?",
                (stored_post_id,),
            )
            connection.commit()
            return 1, 0
        for event in analysis["events"]:
            related_symbols = event["symbols"] or [""]
            for symbol in related_symbols:
                dt = datetime.fromisoformat(published_at)
                cluster_key = f"{symbol or 'CRYPTO'}:{event['category']}:{dt:%Y%m%d%H}"
                event_key = f"{post_id}:{event['category']}:{symbol or 'CRYPTO'}"
                connection.execute(
                    "INSERT INTO social_event_clusters "
                    "(cluster_key, category, symbol, first_seen_at, last_seen_at, post_count) "
                    "VALUES (?, ?, ?, ?, ?, 1) "
                    "ON CONFLICT(cluster_key) DO UPDATE SET "
                    "last_seen_at = excluded.last_seen_at, "
                    "post_count = social_event_clusters.post_count + 1",
                    (
                        cluster_key,
                        event["category"],
                        symbol,
                        published_at,
                        published_at,
                    ),
                )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO social_events "
                    "(event_key, post_id, cluster_key, symbol, category, sentiment, "
                    "impact_score, confidence_score, source_reputation, priority, "
                    "priority_score, unverified_claim, analysis_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_key,
                        stored_post_id,
                        cluster_key,
                        symbol or None,
                        event["category"],
                        event["sentiment"],
                        event["impact_score"],
                        event["confidence_score"],
                        reputation,
                        event["priority"],
                        event["priority_score"],
                        int(event["unverified_claim"]),
                        json.dumps(
                            {
                                **event,
                                "summary": None,
                                "ai_status": analysis["ai_status"],
                                "interpretation": None,
                                "verification_status": "unverified",
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                event_count += cursor.rowcount
                if symbol:
                    connection.execute(
                        "INSERT OR IGNORE INTO social_post_assets (post_id, symbol) "
                        "VALUES (?, ?)",
                        (stored_post_id, symbol),
                    )
        connection.execute(
            "UPDATE social_posts SET processing_status = 'processed' WHERE id = ?",
            (stored_post_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        connection.execute(
            "UPDATE social_posts SET processing_status = 'failed' WHERE id = ?",
            (stored_post_id,),
        )
        connection.commit()
        raise
    finally:
        connection.close()

    _dispatch_post_events(db_path, stored_post_id)
    return 1, event_count


def _x_users(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    includes = payload.get("includes")
    if not isinstance(includes, dict):
        return {}
    users = includes.get("users")
    if not isinstance(users, list):
        return {}
    return {
        str(user["id"]): user
        for user in users
        if isinstance(user, dict) and user.get("id") is not None
    }


def _collection_state(db_path: Path, **values: Any) -> None:
    defaults: dict[str, Any] = {
        "since_id": None,
        "next_token": None,
        "status": "idle",
        "last_started_at": None,
        "last_success_at": None,
        "last_error": None,
    }
    defaults.update(values)
    now = _now()
    connection = connect(db_path)
    try:
        existing = connection.execute(
            "SELECT since_id, next_token, last_success_at FROM social_collection_state "
            "WHERE provider = 'x'",
        ).fetchone()
        if existing:
            for key in ("since_id", "next_token", "last_success_at"):
                if key not in values:
                    defaults[key] = existing[key]
        connection.execute(
            "INSERT INTO social_collection_state "
            "(provider, since_id, next_token, status, last_started_at, "
            "last_success_at, last_error, updated_at) "
            "VALUES ('x', ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET "
            "since_id = excluded.since_id, next_token = excluded.next_token, "
            "status = excluded.status, last_started_at = excluded.last_started_at, "
            "last_success_at = excluded.last_success_at, last_error = excluded.last_error, "
            "updated_at = excluded.updated_at",
            (
                defaults["since_id"],
                defaults["next_token"],
                defaults["status"],
                defaults["last_started_at"],
                defaults["last_success_at"],
                defaults["last_error"],
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def collect_social_once(
    db_path: Path = DB_PATH,
    provider: SocialProvider | None = None,
    *,
    enabled: bool | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    initialize_database(db_path)
    retry_failed_deliveries(db_path)
    configured_enabled = _enabled(os.getenv("X_API_ENABLED"), False)
    if enabled is False or (enabled is None and not configured_enabled):
        _collection_state(db_path, status="disabled", last_error=None)
        return {"status": "disabled", "posts_received": 0, "posts_inserted": 0, "events_created": 0}
    token = os.getenv("X_API_BEARER_TOKEN", "").strip()
    if provider is None and not token:
        _collection_state(db_path, status="not_configured", last_error=None)
        return {"status": "not_configured", "posts_received": 0, "posts_inserted": 0, "events_created": 0}
    if max_pages is None:
        try:
            max_pages = int(os.getenv("SOCIAL_MAX_PAGES", str(SOCIAL_MAX_PAGES)))
        except ValueError as exc:
            raise ValueError("SOCIAL_MAX_PAGES must be an integer") from exc
    if max_pages < 1 or max_pages > 20:
        raise ValueError("max_pages must be between 1 and 20")
    client = provider or XApiProvider(token)
    now = _now()
    _collection_state(db_path, status="running", last_started_at=now, last_error=None)
    connection = connect(db_path)
    try:
        cursor = connection.execute(
            "SELECT since_id, next_token FROM social_collection_state WHERE provider = 'x'"
        ).fetchone()
        since_id = cursor["since_id"] if cursor else None
        pagination_token = cursor["next_token"] if cursor else None
    finally:
        connection.close()

    assets = _load_assets(db_path)
    query = build_search_query(assets)
    received = inserted = event_count = 0
    max_post_id = since_id
    starting_since_id = since_id
    page_limit_reached = True
    try:
        for _ in range(max_pages):
            response = client.search(
                query,
                since_id=since_id,
                pagination_token=pagination_token,
            )
            posts = response.get("data", [])
            if not isinstance(posts, list):
                raise SocialProviderError("Invalid X API post collection")
            received += len(posts)
            users = _x_users(response)
            for post in posts:
                if not isinstance(post, dict):
                    continue
                author = users.get(str(post.get("author_id", "")), {})
                post_inserted, post_events = _save_post_and_events(
                    db_path,
                    post,
                    author,
                    assets,
                )
                inserted += post_inserted
                event_count += post_events
                post_id = str(post.get("id", ""))
                if post_id.isdigit() and (max_post_id is None or int(post_id) > int(max_post_id)):
                    max_post_id = post_id
            meta = response.get("meta", {})
            pagination_token = meta.get("next_token") if isinstance(meta, dict) else None
            if not pagination_token:
                page_limit_reached = False
                break
        if page_limit_reached and pagination_token:
            committed_since_id = starting_since_id
            committed_next_token = pagination_token
        else:
            committed_since_id = max_post_id
            committed_next_token = None
        _collection_state(
            db_path,
            since_id=committed_since_id,
            next_token=committed_next_token,
            status="success",
            last_started_at=now,
            last_success_at=_now(),
            last_error=None,
        )
        return {
            "status": "success",
            "posts_received": received,
            "posts_inserted": inserted,
            "events_created": event_count,
            "next_page_available": bool(committed_next_token),
        }
    except Exception as exc:
        _collection_state(
            db_path,
            status="failed",
            last_started_at=now,
            last_error=type(exc).__name__,
        )
        LOGGER.warning("social_collection_failed provider=x error=%s", type(exc).__name__)
        return {
            "status": "failed",
            "posts_received": received,
            "posts_inserted": inserted,
            "events_created": event_count,
            "error": type(exc).__name__,
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _rows_with_json(rows: list[Any], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    results = []
    for row in rows:
        item = dict(row)
        for field in fields:
            try:
                item[field] = json.loads(item[field])
            except (TypeError, json.JSONDecodeError):
                item[field] = []
        results.append(item)
    return results


def get_social_sources(db_path: Path, limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    connection = connect(db_path)
    try:
        count = connection.execute("SELECT COUNT(*) FROM social_sources").fetchone()[0]
        rows = connection.execute(
            "SELECT id, account_id, username, display_name, category, verified, "
            "reputation_score, supported_assets, active, created_at, updated_at "
            "FROM social_sources ORDER BY active DESC, reputation_score DESC, username "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return _rows_with_json(rows, ("supported_assets",)), count
    finally:
        connection.close()


def create_social_source(db_path: Path, values: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    connection = connect(db_path)
    try:
        cursor = connection.execute(
            "INSERT INTO social_sources "
            "(account_id, username, display_name, category, verified, reputation_score, "
            "supported_assets, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                values["account_id"],
                values["username"],
                values["display_name"],
                values["category"],
                int(values.get("verified", False)),
                values.get("reputation_score", 50),
                json.dumps(values.get("supported_assets", [])),
                int(values.get("active", True)),
                now,
                now,
            ),
        )
        connection.commit()
        source_id = int(cursor.lastrowid)
    finally:
        connection.close()
    return get_social_source(db_path, source_id)


def get_social_source(db_path: Path, source_id: int) -> dict[str, Any] | None:
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT id, account_id, username, display_name, category, verified, "
            "reputation_score, supported_assets, active, created_at, updated_at "
            "FROM social_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        return _rows_with_json([row], ("supported_assets",))[0] if row else None
    finally:
        connection.close()


def update_social_source(
    db_path: Path,
    source_id: int,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    if not values:
        return get_social_source(db_path, source_id)
    columns = {
        "username": "username",
        "display_name": "display_name",
        "category": "category",
        "verified": "verified",
        "reputation_score": "reputation_score",
        "supported_assets": "supported_assets",
        "active": "active",
    }
    assignments = []
    params: list[Any] = []
    for key, column in columns.items():
        if key not in values:
            continue
        value = values[key]
        if key in {"verified", "active"}:
            value = int(value)
        elif key == "supported_assets":
            value = json.dumps(value)
        assignments.append(f"{column} = ?")
        params.append(value)
    assignments.append("updated_at = ?")
    params.extend([_now(), source_id])
    connection = connect(db_path)
    try:
        cursor = connection.execute(
            f"UPDATE social_sources SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        connection.commit()
        if cursor.rowcount == 0:
            return None
    finally:
        connection.close()
    return get_social_source(db_path, source_id)


def deactivate_social_source(db_path: Path, source_id: int) -> bool:
    connection = connect(db_path)
    try:
        cursor = connection.execute(
            "UPDATE social_sources SET active = 0, updated_at = ? WHERE id = ?",
            (_now(), source_id),
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def get_social_posts(
    db_path: Path,
    *,
    asset: str | None = None,
    source_type: str | None = None,
    category: str | None = None,
    sentiment: str | None = None,
    priority: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    filters: list[str] = []
    params: list[Any] = []
    if asset:
        filters.append(
            "EXISTS (SELECT 1 FROM social_post_assets pa "
            "WHERE pa.post_id = p.id AND pa.symbol = ?)"
        )
        params.append(asset.upper())
    if source_type:
        filters.append("EXISTS (SELECT 1 FROM social_sources s WHERE s.id = p.source_id AND s.category = ?)")
        params.append(source_type)
    if category:
        filters.append("EXISTS (SELECT 1 FROM social_events e WHERE e.post_id = p.id AND e.category = ?)")
        params.append(category)
    if sentiment:
        filters.append("EXISTS (SELECT 1 FROM social_events e WHERE e.post_id = p.id AND e.sentiment = ?)")
        params.append(sentiment)
    if priority:
        filters.append("EXISTS (SELECT 1 FROM social_events e WHERE e.post_id = p.id AND e.priority = ?)")
        params.append(priority)
    if since:
        filters.append("p.published_at >= ?")
        params.append(_utc(since))
    if until:
        filters.append("p.published_at <= ?")
        params.append(_utc(until))
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    connection = connect(db_path)
    try:
        count = connection.execute(
            f"SELECT COUNT(*) FROM social_posts p{where}",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT p.id, p.provider_post_id, p.text, p.post_url, p.published_at, "
            "p.language, p.is_repost, p.processing_status, p.collected_at, "
            "s.username, s.display_name, s.category AS source_category, "
            "s.verified, s.reputation_score "
            f"FROM social_posts p LEFT JOIN social_sources s ON s.id = p.source_id{where} "
            "ORDER BY p.published_at DESC, p.id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["verification_status"] = "unverified"
            item["assets"] = [
                asset["symbol"]
                for asset in connection.execute(
                    "SELECT symbol FROM social_post_assets WHERE post_id = ? ORDER BY symbol",
                    (row["id"],),
                ).fetchall()
            ]
            item["events"] = [
                dict(event)
                for event in connection.execute(
                    "SELECT category, sentiment, impact_score, confidence_score, "
                    "priority, priority_score, unverified_claim, analysis_json "
                    "FROM social_events WHERE post_id = ? ORDER BY priority_score DESC",
                    (row["id"],),
                ).fetchall()
            ]
            for event in item["events"]:
                event["analysis"] = json.loads(event.pop("analysis_json"))
            items.append(item)
        return items, count
    finally:
        connection.close()


def get_social_events(
    db_path: Path,
    *,
    asset: str | None = None,
    category: str | None = None,
    sentiment: str | None = None,
    priority: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    filters: list[str] = []
    params: list[Any] = []
    for key, column, value in (
        ("asset", "e.symbol", asset.upper() if asset else None),
        ("category", "e.category", category),
        ("sentiment", "e.sentiment", sentiment),
        ("priority", "e.priority", priority),
    ):
        if value:
            filters.append(f"{column} = ?")
            params.append(value)
    if since:
        filters.append("e.created_at >= ?")
        params.append(_utc(since))
    if until:
        filters.append("e.created_at <= ?")
        params.append(_utc(until))
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    connection = connect(db_path)
    try:
        count = connection.execute(
            f"SELECT COUNT(*) FROM social_events e{where}",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT e.id, e.event_key, e.symbol, e.category, e.sentiment, "
            "e.impact_score, e.confidence_score, e.source_reputation, e.priority, "
            "e.priority_score, e.unverified_claim, e.created_at, e.analysis_json, "
            "p.text, p.post_url, p.published_at, s.username, s.display_name, "
            "s.category AS source_category, s.verified "
            "FROM social_events e JOIN social_posts p ON p.id = e.post_id "
            "LEFT JOIN social_sources s ON s.id = p.source_id"
            f"{where} ORDER BY e.created_at DESC, e.id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["analysis"] = json.loads(item.pop("analysis_json"))
            item["verification_status"] = "unverified"
            items.append(item)
        return items, count
    finally:
        connection.close()


def get_social_alerts(db_path: Path, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    connection = connect(db_path)
    try:
        count = connection.execute("SELECT COUNT(*) FROM social_alert_history").fetchone()[0]
        rows = connection.execute(
            "SELECT a.id, a.alert_key, a.status, a.created_at, a.acknowledged_at, "
            "e.symbol, e.category, e.sentiment, e.priority, e.priority_score, "
            "e.unverified_claim, p.post_url, s.username, "
            "(SELECT json_group_array(json_object('channel', d.channel, 'status', d.status, "
            "'attempts', d.attempts, 'error_code', d.error_code, 'delivered_at', d.delivered_at)) "
            "FROM notification_deliveries d WHERE d.alert_id = a.id) AS deliveries "
            "FROM social_alert_history a JOIN social_events e ON e.id = a.event_id "
            "JOIN social_posts p ON p.id = e.post_id "
            "LEFT JOIN social_sources s ON s.id = p.source_id "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return _rows_with_json(rows, ("deliveries",)), count
    finally:
        connection.close()


def get_collection_status(db_path: Path) -> dict[str, Any]:
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT provider, status, last_started_at, last_success_at, last_error, updated_at "
            "FROM social_collection_state WHERE provider = 'x'"
        ).fetchone()
        if row:
            state = dict(row)
        else:
            enabled = _enabled(os.getenv("X_API_ENABLED"), False)
            configured = bool(os.getenv("X_API_BEARER_TOKEN", "").strip())
            state = {
                "provider": "x",
                "status": "idle" if enabled and configured else "disabled" if not enabled else "not_configured",
                "last_started_at": None,
                "last_success_at": None,
                "last_error": None,
                "updated_at": None,
            }
        state["enabled"] = _enabled(os.getenv("X_API_ENABLED"), False)
        state["configured"] = bool(os.getenv("X_API_BEARER_TOKEN", "").strip())
        state["recent_search_window_days"] = 7
        state["ai_status"] = _ai_status()
        return state
    finally:
        connection.close()


def get_social_watchlist(db_path: Path) -> list[dict[str, str]]:
    return get_crypto_watchlist(db_path)


def create_social_alert_rule(db_path: Path, values: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    connection = connect(db_path)
    try:
        cursor = connection.execute(
            "INSERT INTO social_alert_rules "
            "(name, enabled, minimum_score, assets, categories, cooldown_seconds, "
            "quiet_hours_start, quiet_hours_end, timezone, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                values["name"],
                int(values.get("enabled", True)),
                values.get("minimum_score", 60),
                json.dumps(values.get("assets", [])),
                json.dumps(values.get("categories", [])),
                values.get("cooldown_seconds", 900),
                values.get("quiet_hours_start"),
                values.get("quiet_hours_end"),
                values.get("timezone", "UTC"),
                now,
                now,
            ),
        )
        connection.commit()
        rule_id = int(cursor.lastrowid)
        return get_social_alert_rule(db_path, rule_id)
    finally:
        connection.close()


def get_social_alert_rule(db_path: Path, rule_id: int) -> dict[str, Any] | None:
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT id, name, enabled, minimum_score, assets, categories, "
            "cooldown_seconds, quiet_hours_start, quiet_hours_end, timezone, "
            "created_at, updated_at FROM social_alert_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        return _rows_with_json([row], ("assets", "categories"))[0] if row else None
    finally:
        connection.close()


def get_social_alert_rules(db_path: Path) -> list[dict[str, Any]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT id, name, enabled, minimum_score, assets, categories, "
            "cooldown_seconds, quiet_hours_start, quiet_hours_end, timezone, "
            "created_at, updated_at FROM social_alert_rules ORDER BY id",
        ).fetchall()
        return _rows_with_json(rows, ("assets", "categories"))
    finally:
        connection.close()


def _inside_quiet_hours(rule: dict[str, Any], now: datetime) -> bool:
    start = rule.get("quiet_hours_start")
    end = rule.get("quiet_hours_end")
    if not start or not end:
        return False
    try:
        local_now = now.astimezone(ZoneInfo(rule["timezone"]))
        start_time = datetime.strptime(start, "%H:%M").time()
        end_time = datetime.strptime(end, "%H:%M").time()
    except (ValueError, ZoneInfoNotFoundError):
        return False
    local_time = local_now.timetz().replace(tzinfo=None)
    if start_time <= end_time:
        return start_time <= local_time < end_time
    return local_time >= start_time or local_time < end_time


def _create_alert_for_event(db_path: Path, event: dict[str, Any]) -> None:
    rules = [rule for rule in get_social_alert_rules(db_path) if rule["enabled"]]
    for rule in rules:
        if event["priority_score"] < rule["minimum_score"]:
            continue
        if rule["assets"] and event["symbol"] not in rule["assets"]:
            continue
        if rule["categories"] and event["category"] not in rule["categories"]:
            continue
        now = datetime.now(timezone.utc)
        if _inside_quiet_hours(rule, now):
            status = "suppressed"
        else:
            connection = connect(db_path)
            try:
                prior = connection.execute(
                    "SELECT 1 FROM social_alert_history h "
                    "JOIN social_events e ON e.id = h.event_id "
                    "WHERE h.rule_id = ? AND e.symbol IS ? AND e.category = ? "
                    "AND h.created_at >= ? AND h.status != 'suppressed' LIMIT 1",
                    (
                        rule["id"],
                        event["symbol"],
                        event["category"],
                        (now - timedelta(seconds=rule["cooldown_seconds"])).isoformat(
                            timespec="seconds"
                        ),
                    ),
                ).fetchone()
            finally:
                connection.close()
            if prior:
                status = "suppressed"
            else:
                status = "queued"
        alert_key = f"{event['event_key']}:{rule['id']}"
        connection = connect(db_path)
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO social_alert_history "
                "(event_id, rule_id, alert_key, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (event["id"], rule["id"], alert_key, status, _now()),
            )
            connection.commit()
            if cursor.rowcount == 0:
                continue
            alert_id = int(cursor.lastrowid)
        finally:
            connection.close()
        if status == "queued":
            _dispatch_alert(db_path, alert_id, event)


def _social_message(event: dict[str, Any]) -> tuple[str, str]:
    source = event.get("username") or "unknown source"
    summary = event.get("text", "").strip()
    if len(summary) > 800:
        summary = summary[:797] + "..."
    ticker = event.get("symbol") or "CRYPTO"
    title = f"{event['priority'].upper()} {ticker} {event['category']}"
    content = (
        f"{ticker} | {event['category']} | {event['sentiment']} | "
        f"priority {event['priority'].upper()} ({event['priority_score']:.0f}/100)\n"
        f"Source: @{source}\n"
        f"Unverified claim: {'yes' if event['unverified_claim'] else 'not detected'}\n"
        f"Post: {summary}\n"
        f"{event['post_url']}\n"
        "Research information only; not a trading instruction."
    )
    return title[:120], content[:3500]


def send_telegram_notification(event: dict[str, Any]) -> tuple[bool, str | None]:
    if not _enabled(os.getenv("TELEGRAM_ENABLED"), False):
        return False, "disabled"
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "not_configured"
    title, content = _social_message(event)
    safe_title = html.escape(title)
    safe_text = html.escape(content)
    message = f"<b>{safe_title}</b>\n{safe_text}"
    if len(message) > 4000:
        message = message[:3997] + "..."
    session = _http_session(retry_post=True)
    try:
        response = session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
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
        session.close()


def _dispatch_alert(
    db_path: Path,
    alert_id: int,
    event: dict[str, Any],
    *,
    retry_failed: bool = False,
    retry_channels: set[str] | None = None,
) -> None:
    title, message = _social_message(event)
    channels = {
        "ntfy": (
            _enabled(os.getenv("NTFY_ENABLED"), True),
            bool(os.getenv("NTFY_TOPIC", "").strip()),
        ),
        "telegram": (
            _enabled(os.getenv("TELEGRAM_ENABLED"), False),
            bool(
                os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
                and os.getenv("TELEGRAM_CHAT_ID", "").strip()
            ),
        ),
    }
    connection = connect(db_path)
    try:
        for channel in channels:
            connection.execute(
                "INSERT OR IGNORE INTO notification_deliveries "
                "(alert_id, channel, status, updated_at) VALUES (?, ?, 'queued', ?)",
                (alert_id, channel, _now()),
            )
        connection.commit()
    finally:
        connection.close()

    for channel, (enabled, configured) in channels.items():
        if retry_channels is not None and channel not in retry_channels:
            continue
        connection = connect(db_path)
        try:
            delivery = connection.execute(
                "SELECT status, attempts FROM notification_deliveries "
                "WHERE alert_id = ? AND channel = ?",
                (alert_id, channel),
            ).fetchone()
        finally:
            connection.close()
        previous_status = delivery["status"]
        if previous_status in {"sent", "not_configured", "disabled"}:
            continue
        if previous_status == "failed" and not retry_failed:
            continue
        attempts = int(delivery["attempts"])
        error_code = None
        delivered = False
        if not enabled:
            status = "disabled"
        elif not configured:
            status = "not_configured"
        else:
            attempts += 1
            try:
                if channel == "ntfy":
                    ntfy_priority = {
                        "critical": "urgent",
                        "high": "high",
                        "medium": "default",
                        "low": "low",
                    }[event["priority"]]
                    delivered = send_notification(
                        title,
                        message,
                        priority=ntfy_priority,
                        sequence_id=hashlib.sha256(
                            str(event["event_key"]).encode("utf-8")
                        ).hexdigest()[:32],
                    )
                    error_code = None if delivered else "delivery_failed"
                else:
                    delivered, error_code = send_telegram_notification(event)
                status = "sent" if delivered else "failed"
            except Exception as exc:
                status = "failed"
                error_code = type(exc).__name__
                LOGGER.warning(
                    "social_notification_failed channel=%s error=%s",
                    channel,
                    error_code,
                )
        connection = connect(db_path)
        try:
            connection.execute(
                "UPDATE notification_deliveries SET status = ?, attempts = ?, "
                "error_code = ?, delivered_at = ?, updated_at = ? "
                "WHERE alert_id = ? AND channel = ?",
                (
                    status,
                    attempts,
                    error_code,
                    _now() if delivered else None,
                    _now(),
                    alert_id,
                    channel,
                ),
            )
            connection.commit()
        finally:
            connection.close()
    connection = connect(db_path)
    try:
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM notification_deliveries WHERE alert_id = ?",
                (alert_id,),
            ).fetchall()
        ]
        sent = statuses.count("sent")
        failed = statuses.count("failed")
        final_status = (
            "sent"
            if sent == len(statuses) and sent
            else "partial"
            if sent
            else "failed"
            if failed
            else "not_configured"
        )
        connection.execute(
            "UPDATE social_alert_history SET status = ? WHERE id = ?",
            (final_status, alert_id),
        )
        connection.commit()
    finally:
        connection.close()


def _dispatch_post_events(db_path: Path, post_id: int) -> None:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT e.*, p.text, p.post_url, s.username "
            "FROM social_events e JOIN social_posts p ON p.id = e.post_id "
            "LEFT JOIN social_sources s ON s.id = p.source_id "
            "WHERE p.id = ?",
            (post_id,),
        ).fetchall()
        events = [dict(row) for row in rows]
    finally:
        connection.close()
    for event in events:
        _create_alert_for_event(db_path, event)


def retry_failed_deliveries(
    db_path: Path,
    *,
    limit: int = 100,
    retry_after_seconds: int = 300,
    maximum_attempts: int = 4,
) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=retry_after_seconds)
    ).isoformat(timespec="seconds")
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT d.alert_id, d.channel FROM notification_deliveries d "
            "WHERE d.status IN ('failed', 'queued') AND d.attempts < ? "
            "AND d.updated_at <= ? ORDER BY d.updated_at LIMIT ?",
            (maximum_attempts, cutoff, limit),
        ).fetchall()
        retries: dict[int, tuple[dict[str, Any], set[str]]] = {}
        for row in rows:
            event = connection.execute(
                "SELECT e.*, p.text, p.post_url, s.username "
                "FROM social_alert_history h "
                "JOIN social_events e ON e.id = h.event_id "
                "JOIN social_posts p ON p.id = e.post_id "
                "LEFT JOIN social_sources s ON s.id = p.source_id "
                "WHERE h.id = ?",
                (row["alert_id"],),
            ).fetchone()
            if event:
                alert_id = int(row["alert_id"])
                if alert_id not in retries:
                    retries[alert_id] = (dict(event), set())
                retries[alert_id][1].add(row["channel"])
    finally:
        connection.close()

    for alert_id, (event, channels) in retries.items():
        _dispatch_alert(
            db_path,
            alert_id,
            event,
            retry_failed=True,
            retry_channels=channels,
        )
    return sum(len(channels) for _, channels in retries.values())


def get_social_statistics(db_path: Path) -> dict[str, Any]:
    connection = connect(db_path)
    try:
        post_count = connection.execute("SELECT COUNT(*) FROM social_posts").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM social_events").fetchone()[0]
        source_count = connection.execute("SELECT COUNT(*) FROM social_sources WHERE active = 1").fetchone()[0]
        pending = connection.execute(
            "SELECT COUNT(*) FROM social_posts WHERE processing_status = 'pending'"
        ).fetchone()[0]
        categories = {
            row["category"]: row["count"]
            for row in connection.execute(
                "SELECT category, COUNT(*) AS count FROM social_events GROUP BY category"
            ).fetchall()
        }
        sentiments = {
            row["sentiment"]: row["count"]
            for row in connection.execute(
                "SELECT sentiment, COUNT(*) AS count FROM social_events GROUP BY sentiment"
            ).fetchall()
        }
        priorities = {
            row["priority"]: row["count"]
            for row in connection.execute(
                "SELECT priority, COUNT(*) AS count FROM social_events GROUP BY priority"
            ).fetchall()
        }
        return {
            "posts": post_count,
            "events": event_count,
            "active_sources": source_count,
            "pending_analysis": pending,
            "categories": categories,
            "sentiments": sentiments,
            "priorities": priorities,
            "ai_status": _ai_status(),
        }
    finally:
        connection.close()


def get_notification_history(
    db_path: Path,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    connection = connect(db_path)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM notification_deliveries"
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT d.id, d.channel, d.status, d.attempts, d.error_code, "
            "d.delivered_at, d.updated_at, a.alert_key, a.created_at AS alert_created_at, "
            "e.symbol, e.category, e.priority, e.priority_score, p.post_url "
            "FROM notification_deliveries d "
            "JOIN social_alert_history a ON a.id = d.alert_id "
            "JOIN social_events e ON e.id = a.event_id "
            "JOIN social_posts p ON p.id = e.post_id "
            "ORDER BY d.updated_at DESC, d.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows], count
    finally:
        connection.close()


def test_social_notification(db_path: Path, channel: str) -> dict[str, Any]:
    if channel not in {"ntfy", "telegram"}:
        raise ValueError("Unsupported notification channel")
    if channel == "ntfy":
        if not _enabled(os.getenv("NTFY_ENABLED"), True):
            return {"channel": channel, "status": "disabled"}
        if not os.getenv("NTFY_TOPIC", "").strip():
            return {"channel": channel, "status": "not_configured"}
    else:
        if not _enabled(os.getenv("TELEGRAM_ENABLED"), False):
            return {"channel": channel, "status": "disabled"}
        if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or not os.getenv("TELEGRAM_CHAT_ID", "").strip():
            return {"channel": channel, "status": "not_configured"}

    event = {
        "event_key": f"social-test:{channel}",
        "symbol": "TEST",
        "category": "GENERAL_CRYPTO_NEWS",
        "sentiment": "neutral",
        "priority": "low",
        "priority_score": 0,
        "unverified_claim": False,
        "username": "local-test",
        "text": "Test message from the local Market Intelligence notification adapter.",
        "post_url": "https://x.com",
    }
    if channel == "ntfy":
        delivered = send_notification(
            "Market Intelligence test",
            "Local notification adapter test; not a market alert.",
            priority="low",
            sequence_id=hashlib.sha256(f"{db_path}:{channel}".encode()).hexdigest()[:32],
        )
        return {"channel": channel, "status": "sent" if delivered else "failed"}
    delivered, error_code = send_telegram_notification(event)
    return {
        "channel": channel,
        "status": "sent" if delivered else "failed",
        "error_code": error_code,
    }
