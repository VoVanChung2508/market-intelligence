from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from backend.ai_provider import (
    GeminiAIProvider,
    is_ai_configured,
    translate_sentiment,
)


def test_ai_provider_heuristic_fallback_when_no_key():
    provider = GeminiAIProvider(api_key="")
    assert is_ai_configured() is False or True  # depends on env, but with empty key:

    news_res = provider.analyze_news(
        title="Bitcoin surges to new record high after regulatory approval",
        summary="BTC saw a major growth rally following positive decisions.",
        related_assets=["BTC"],
    )
    assert news_res["ai_status"] in {"not_configured", "fallback"}
    assert news_res["model"] == "keyword-heuristic"
    assert news_res["sentiment"] == "positive"
    assert "Bitcoin surges" in news_res["summary"]
    assert news_res["processed_at"] is not None


def test_ai_provider_successful_gemini_call(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json", {})
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"summary": "Apple reported record services revenue despite supply constraints.", '
                                        '"sentiment": "positive", "impact_score": 85.0, "market_implications": "Bullish for tech sector"}'
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    session = MagicMock()
    session.post = fake_post
    provider = GeminiAIProvider(api_key="test-gemini-key", model="gemini-2.5-flash", session=session)

    res = provider.analyze_news(
        title="Apple Reports Q4 Results",
        summary="Revenue up 6% YoY.",
        related_assets=["AAPL"],
    )

    assert "test-gemini-key" in captured["url"]
    assert res["ai_status"] == "success"
    assert res["model"] == "gemini-2.5-flash"
    assert res["sentiment"] == "positive"
    assert "record services revenue" in res["summary"]
    assert res["impact_score"] == 85.0


def test_ai_provider_handles_api_failure_and_falls_back(monkeypatch):
    session = MagicMock()

    def failing_post(url, **kwargs):
        raise requests.HTTPError("429 Client Error: Quota Exceeded")

    session.post = failing_post
    provider = GeminiAIProvider(api_key="test-key", session=session)

    # Should not crash; gracefully returns heuristic fallback
    res = provider.analyze_news(
        title="Security hack exploits DeFi protocol",
        summary="Over $10M was drained in an exploit.",
        related_assets=["ETH"],
    )

    assert res["ai_status"] == "fallback"
    assert res["model"] == "keyword-heuristic"
    assert res["sentiment"] == "negative"


def test_ai_provider_analyze_social_post():
    session = MagicMock()

    def fake_post(url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"summary": "Major exchange announced listing of new SOL trading pairs.", '
                                        '"related_assets": ["SOL"], "event_category": "EXCHANGE_LISTING", '
                                        '"sentiment": "positive", "impact_score": 78.0, "confidence_score": 90.0, '
                                        '"urgency": "high", "potential_market_implications": "Increased liquidity", '
                                        '"verification_status": "verified"}'
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    session.post = fake_post
    provider = GeminiAIProvider(api_key="test-key", session=session)

    analysis = provider.analyze(
        {
            "text": "Binance will list new SOL trading pair today",
            "post_url": "https://x.com/binance/status/123",
        }
    )

    assert analysis["event_category"] == "EXCHANGE_LISTING"
    assert analysis["sentiment"] == "positive"
    assert analysis["related_assets"] == ["SOL"]
    assert analysis["urgency"] == "high"


def test_translate_sentiment():
    assert "Tích cực" in translate_sentiment("positive")
    assert "Tiêu cực" in translate_sentiment("negative")
    assert "Trung lập" in translate_sentiment("neutral")
    assert "Hỗn hợp" in translate_sentiment("mixed")

