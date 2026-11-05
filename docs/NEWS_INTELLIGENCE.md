# News Intelligence

## Current implementation

The first increment collects RSS 2.x and Atom feeds through a small provider interface. Configure feeds in the private `.env` as a JSON array:

```dotenv
MARKET_NEWS_FEEDS='[{"name":"Example Wire","url":"https://example.com/feed.xml"}]'
```

Feed names must be unique. At most 20 feeds are accepted. Sources must use HTTPS and cannot include URL credentials or custom ports. Requests have a 10-second read timeout, a 2 MiB response limit, and do not follow redirects. Feed failures are isolated so a broken source does not prevent other sources from being processed.

Run collection manually or via the user systemd timer (`deploy/market-news-collector.timer`, scheduled every 15 minutes):

```bash
source .venv/bin/activate
python -m backend.run_news_collector
```

No source is configured by default. Collection results are printed and logged; the command exits unsuccessfully if any configured source fails. News collection uses no model inference and can run independently from market collection and model training.

When `TELEGRAM_ENABLED=true` and Telegram bot credentials are configured, newly inserted articles that match tracked watchlist assets trigger a notification via Telegram containing the title, source, category, matched asset symbols, and canonical URL. Furthermore, when `GEMINI_API_KEY` is configured, each notification is enriched with an AI-generated concise summary (1–2 sentences) and market impact assessment (`Tích cực`, `Tiêu cực`, or `Trung lập`). If the Gemini API key is unset or quota limits are reached (HTTP 429), the system falls back safely to keyword-based heuristics (`POSITIVE_TERMS` / `NEGATIVE_TERMS`). AI analysis and Telegram delivery errors are isolated and never prevent news ingestion or database commits from succeeding.

The importer stores title, source-provided summary/content, article URL, source label, feed provenance in the local database, language when provided, UTC publication timestamp when parseable, deterministic category, and tracked asset matches. If publication time is missing or invalid, it stays `null`. HTML in feed summaries is converted to text; it is not rendered as markup in the dashboard. The source URL is kept internally for provenance and is not included in API responses; sensitive query parameters are removed before storage.

Duplicate checks cover canonical article URLs, normalized content fingerprints, and normalized title fingerprints on the same UTC publication date. These checks are designed for repeat ingestion and common cross-feed duplicates; they are not semantic near-duplicate detection.

## Gemini AI Integration

The `backend.ai_provider.GeminiAIProvider` handles LLM-based intelligence enrichment:
- **Model**: Configurable via `GEMINI_MODEL` (default: `gemini-2.5-flash`).
- **Resilience**: Configured with timeouts, bounded exponential backoff retries on rate limits, and fallback to keyword heuristics upon provider failure or quota exhaustion.
- **Safety**: Raw source data is stored unmutated; AI summaries and impact assessments are attached on alert delivery or stored with model provenance without rolling back collected content.

## API and dashboard

- `GET /api/v1/news` supports text search, exact asset filtering, category filtering, and bounded pagination.
- `GET /api/v1/news/{article_id}` returns an individual stored article.
- The News Intelligence dashboard displays the latest collected records, source links, expandable feed content, category, timestamp, language, and tracked-asset filters.

Categories are assigned by deterministic keyword rules. They are not verified labels or model interpretations. The dashboard explicitly states that no AI summary or sentiment has been generated.

## Not implemented yet

- News API adapters, collection-run history, near-duplicate semantic matching, and multilingual language detection.
- Ticker/entity extraction beyond matching the currently known local asset symbols and names.
- Sentiment, event classification beyond simple category keywords, AI summaries, confidence, and model provenance.
- Recommendations, sentiment distributions, and an event timeline.

Before adding AI processing, preserve the source fields separately, record provider/model and processing timestamps, add provider timeouts/retries/rate limiting, and ensure processing failures never roll back successfully collected articles. Verify article and feed licensing before storing or redistributing full text; this increment stores feed content only and does not crawl article pages.
