import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

POSITIVE_TERMS = {
    "approval", "approved", "bullish", "growth", "launch", "partnership",
    "rally", "record", "surge", "upgrade", "success", "adoption", "gain",
    "profit", "beat", "higher", "positive", "expansion", "soars",
}
NEGATIVE_TERMS = {
    "attack", "ban", "bearish", "breach", "crash", "decline", "delist",
    "exploit", "hack", "lawsuit", "loss", "plunge", "scam", "suspend",
    "outage", "drained", "fraud", "fall", "lower", "drop", "negative",
}

SENTIMENT_VI_MAP = {
    "positive": "Tích cực (Positive)",
    "negative": "Tiêu cực (Negative)",
    "neutral": "Trung lập (Neutral)",
    "mixed": "Hỗn hợp (Mixed)",
}


def translate_sentiment(sentiment: str) -> str:
    key = str(sentiment).strip().casefold()
    return SENTIMENT_VI_MAP.get(key, f"{sentiment.capitalize()}")


def is_ai_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _ai_http_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=0,
        status=2,
        backoff_factor=1.0,
        status_forcelist=(429, 503),
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


class GeminiAIProvider:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = (model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip() or DEFAULT_GEMINI_MODEL
        self.session = session or _ai_http_session()
        self.owns_session = session is None
        self._last_call_time: float = 0.0

    def _rate_limit_wait(self) -> None:
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < 0.2:
            time.sleep(0.2 - elapsed)
        self._last_call_time = time.time()

    def _heuristic_sentiment(self, text: str) -> str:
        words = set(re.findall(r"\w+", text.lower()))
        pos = len(words & POSITIVE_TERMS)
        neg = len(words & NEGATIVE_TERMS)
        if pos > neg and pos > 0:
            return "positive"
        elif neg > pos and neg > 0:
            return "negative"
        elif pos > 0 and neg > 0:
            return "mixed"
        return "neutral"

    def _heuristic_summary(self, title: str, text: str) -> str:
        combined = f"{title}. {text}".strip()
        sentences = re.split(r"(?<=[.!?])\s+", combined)
        summary = " ".join(sentences[:2]).strip()
        if len(summary) > 280:
            summary = summary[:277] + "..."
        return summary or title

    def _call_gemini_json(self, prompt: str) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        self._rate_limit_wait()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        try:
            response = self.session.post(url, json=payload, timeout=(5, 15))
            response.raise_for_status()
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts") or []
            if not parts:
                return None
            text_result = parts[0].get("text", "").strip()
            # Clean markdown codeblocks if enclosed
            if text_result.startswith("```"):
                text_result = re.sub(r"^```(?:json)?\s*", "", text_result)
                text_result = re.sub(r"\s*```$", "", text_result)
            parsed = json.loads(text_result)
            return parsed if isinstance(parsed, dict) else None
        except Exception as exc:
            LOGGER.warning("gemini_api_call_failed model=%s error=%s", self.model, type(exc).__name__)
            return None

    def analyze_news(
        self,
        title: str,
        summary: str | None = None,
        content: str | None = None,
        related_assets: list[str] | None = None,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        assets_str = ", ".join(related_assets or [])
        text_body = summary or content or ""

        # Check if API key is present
        if not self.api_key:
            heuristic_sent = self._heuristic_sentiment(f"{title} {text_body}")
            heuristic_summ = self._heuristic_summary(title, text_body)
            return {
                "summary": heuristic_summ,
                "sentiment": heuristic_sent,
                "impact_score": 60.0 if heuristic_sent in {"positive", "negative"} else 40.0,
                "model": "keyword-heuristic",
                "processed_at": now_iso,
                "ai_status": "not_configured",
            }

        prompt = (
            "You are an expert financial market analyst. Analyze the following news article for market impact.\n"
            f"Title: {title}\n"
            f"Body: {text_body[:1500]}\n"
            f"Tracked Assets: {assets_str}\n\n"
            "Respond ONLY with a JSON object in this exact schema:\n"
            "{\n"
            '  "summary": "1-2 concise sentences summarizing the core event and financial/crypto significance",\n'
            '  "sentiment": "positive" | "negative" | "neutral" | "mixed",\n'
            '  "impact_score": 0 to 100,\n'
            '  "market_implications": "short sentence on price or sector implications"\n'
            "}"
        )

        res = self._call_gemini_json(prompt)
        if res and isinstance(res, dict) and "summary" in res:
            sentiment = res.get("sentiment", "neutral").lower()
            if sentiment not in {"positive", "negative", "neutral", "mixed"}:
                sentiment = "neutral"
            impact_score = float(res.get("impact_score", 50.0))
            return {
                "summary": str(res["summary"]).strip(),
                "sentiment": sentiment,
                "impact_score": max(0.0, min(100.0, impact_score)),
                "market_implications": str(res.get("market_implications", "")).strip(),
                "model": self.model,
                "processed_at": now_iso,
                "ai_status": "success",
            }

        # Fallback to heuristic
        heuristic_sent = self._heuristic_sentiment(f"{title} {text_body}")
        heuristic_summ = self._heuristic_summary(title, text_body)
        return {
            "summary": heuristic_summ,
            "sentiment": heuristic_sent,
            "impact_score": 60.0 if heuristic_sent in {"positive", "negative"} else 40.0,
            "model": "keyword-heuristic",
            "processed_at": now_iso,
            "ai_status": "fallback",
        }

    def analyze(self, post: dict[str, Any]) -> dict[str, Any]:
        """Implements SocialAIProvider Protocol."""
        text = str(post.get("text", ""))
        post_url = str(post.get("post_url", ""))
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if not self.api_key:
            sent = self._heuristic_sentiment(text)
            return {
                "summary": self._heuristic_summary("", text),
                "related_assets": post.get("related_assets", ["BTC"]),
                "event_category": post.get("category", "GENERAL_CRYPTO_NEWS"),
                "sentiment": sent,
                "impact_score": 50.0,
                "confidence_score": 50.0,
                "urgency": "medium",
                "potential_market_implications": "Heuristic baseline assessment.",
                "verification_status": "unverified",
                "original_source_url": post_url,
            }

        prompt = (
            "You are an expert crypto and social market intelligence analyst.\n"
            f"Post: {text}\n"
            f"Source URL: {post_url}\n\n"
            "Analyze the post and respond ONLY with a JSON object in this exact schema:\n"
            "{\n"
            '  "summary": "1-2 concise sentences summarizing the post and claims",\n'
            '  "related_assets": ["BTC", "ETH", ...],\n'
            '  "event_category": "EXPLOIT" | "EXCHANGE_LISTING" | "SECURITY_HACK" | "REGULATORY_NEWS" | "PARTNERSHIP" | "WHALE_ACTIVITY" | "GENERAL_CRYPTO_NEWS",\n'
            '  "sentiment": "positive" | "negative" | "neutral" | "mixed",\n'
            '  "impact_score": 0 to 100,\n'
            '  "confidence_score": 0 to 100,\n'
            '  "urgency": "critical" | "high" | "medium" | "low",\n'
            '  "potential_market_implications": "short sentence on potential price implication",\n'
            '  "verification_status": "verified" | "unverified" | "speculative"\n'
            "}"
        )

        res = self._call_gemini_json(prompt)
        if res and isinstance(res, dict) and "summary" in res:
            sent = str(res.get("sentiment", "neutral")).lower()
            if sent not in {"positive", "negative", "neutral", "mixed"}:
                sent = "neutral"
            urgency = str(res.get("urgency", "low")).lower()
            if urgency not in {"critical", "high", "medium", "low"}:
                urgency = "low"
            verif = str(res.get("verification_status", "unverified")).lower()
            if verif not in {"verified", "unverified", "speculative"}:
                verif = "unverified"
            assets = res.get("related_assets")
            if not isinstance(assets, list):
                assets = ["BTC"]
            return {
                "summary": str(res["summary"]).strip(),
                "related_assets": [str(a).upper() for a in assets][:10],
                "event_category": str(res.get("event_category", "GENERAL_CRYPTO_NEWS")),
                "sentiment": sent,
                "impact_score": float(res.get("impact_score", 50.0)),
                "confidence_score": float(res.get("confidence_score", 50.0)),
                "urgency": urgency,
                "potential_market_implications": str(res.get("potential_market_implications", "")).strip(),
                "verification_status": verif,
                "original_source_url": post_url,
            }

        # Fallback
        sent = self._heuristic_sentiment(text)
        return {
            "summary": self._heuristic_summary("", text),
            "related_assets": ["BTC"],
            "event_category": "GENERAL_CRYPTO_NEWS",
            "sentiment": sent,
            "impact_score": 50.0,
            "confidence_score": 50.0,
            "urgency": "medium",
            "potential_market_implications": "Heuristic fallback evaluation.",
            "verification_status": "unverified",
            "original_source_url": post_url,
        }

    def close(self) -> None:
        if self.owns_session:
            self.session.close()


def get_default_ai_provider() -> GeminiAIProvider:
    return GeminiAIProvider()

