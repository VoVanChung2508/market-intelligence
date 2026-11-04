# Data Sources

## Current providers

| Data | Provider | Stored interval | Access/cost | Current constraints |
|---|---|---|---|---|
| BTC/USD and ETH/USD market chart | CoinGecko public API | `1h` | Keyless public endpoint | Collector requests 30 days; public limits/freshness can change; this is not an exchange-specific trade feed. Captured values are price/volume points, not exchange OHLC candles. |
| AAPL daily OHLCV | Alpha Vantage `TIME_SERIES_DAILY` | `1d` | API key required; free plan documentation says 25 requests/day | `compact` returns latest 100 points. Realtime/intraday and broader history require paid entitlements. Collector enforces a 20-hour attempt cooldown. |
| Existing AAPL history | Yahoo Finance via local CSV / legacy `yfinance` collector | `1d` | Existing local data | `yfinance` is unofficial; Yahoo terms restrict automated collection without prior permission. The new scheduled collector does not call this legacy route. Review usage rights before redistribution or continued collection. |
| Financial news | Operator-configured RSS/Atom feeds | Feed-provided timestamps, normalized to UTC | Depends on each feed's terms and availability | No feeds are active by default. Collection is manual; only HTTPS sources are accepted, redirects are not followed, and feed-provided text is stored without crawling full articles. |

Official references checked 2026-10-16:

- [CoinGecko historical chart endpoint](https://docs.coingecko.com/reference/coins-id-market-chart)
- [CoinGecko plans and pricing](https://www.coingecko.com/en/api/pricing)
- [Alpha Vantage documentation](https://www.alphavantage.co/documentation/)
- [Alpha Vantage pricing and request limits](https://www.alphavantage.co/premium/)
- [yfinance legal disclaimer](https://github.com/ranaroussi/yfinance)
- [Yahoo Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)

## Freshness and display rules

Provider timestamp, source and freshness accompany each asset. The frontend must not label historical, delayed, or stale bars as realtime. Empty, stale and provider-failure states are distinct. Existing daily stock values are based on their source bars; the change metric compares with the prior stored source interval, not a guaranteed market-session close.

The current collector timer is 30 minutes. Crypto public API quotas are not guaranteed, so schedule frequency may need adjustment if rate-limited. Stock collection is constrained to one attempt every 20 hours, leaving headroom under the documented 25 daily free requests. No source is used for commercial redistribution without confirming its license/entitlement.

## News feed configuration

Set `MARKET_NEWS_FEEDS` in the private `.env` to a JSON array containing a unique `name` and HTTPS `url` for each feed (at most 20). RSS and Atom XML are supported. Source-specific content and publication timestamps are retained as provided/normalized; missing publication dates remain unknown rather than being fabricated. The collector does not scrape article pages or bypass access controls. Verify feed licenses and terms before use or redistribution.

No news API provider, multilingual sentiment analysis, AI summary, macroeconomic provider, or automated news schedule is active. No consolidated US equities feed is claimed; Alpaca free data is IEX-only and Alpha Vantage free access does not imply realtime exchange entitlements. Market coverage, legal rights and costs must be reassessed before widening the watchlist or deploying publicly.
