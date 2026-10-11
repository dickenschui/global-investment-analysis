"""Live-quote pulse from Yahoo Finance, for the hourly check on GitHub Actions (no Claude session needed).

Produces the pulse schema alerts.py reads:
{"retrieved_at_utc", "source": "Yahoo Finance", "quotes": [{"symbol", "last", "prior_close", "data_status", "ts", ...}],
 "missing": [symbol, ...], "missing_reasons": {symbol: reason}}
Prices are passed through exactly as Yahoo reports them: London lines arrive in pence and alerts.normalise_quotes
rescales them against the weekly close. A symbol without a usable price is omitted and listed in "missing";
nothing is estimated or carried forward. Yahoo is a secondary, usually delayed vendor feed, not an executable quote.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

SOURCE = "Yahoo Finance"


def yahoo_symbol_map(universe: dict) -> dict[str, str]:
    """{repo symbol: Yahoo ticker} from config/universe.json's per-instrument "yahoo" field."""
    return {i["symbol"]: i["yahoo"] for i in universe.get("instruments", []) if i.get("yahoo")}


class _YFinanceClient:
    """Default client: yfinance's Ticker.info, which carries Yahoo's quote fields. Imported lazily so the package
    and its tests do not need yfinance."""

    def __init__(self):
        try:
            import yfinance
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError("yfinance is not installed: pip install '.[yahoo]'") from exc
        self._yf = yfinance

    def quote(self, ticker: str) -> dict:
        return dict(self._yf.Ticker(ticker).info or {})


def _number(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 else None


def _status(q: dict) -> str:
    """Only REGULAR is live trading. Delay of 0 minutes is REALTIME; unknown delay is reported as DELAYED (never
    claim real time without evidence). CLOSED, PRE, POST and anything unrecognised are FROZEN (stale for alerts)."""
    if str(q.get("marketState") or "").upper() != "REGULAR":
        return "FROZEN"
    return "REALTIME" if q.get("exchangeDataDelayedBy") == 0 else "DELAYED"


def _ts(v):
    """Regular-market time as epoch seconds or ISO, as alerts.normalise_quotes accepts; None if absent."""
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    return v if isinstance(v, str) and v else None


def build_pulse(symbols, universe: dict, client=None, now: datetime | str | None = None) -> dict:
    """Quote each repo symbol via its Yahoo ticker. `client.quote(ticker) -> dict` of Yahoo quote fields
    (regularMarketPrice, regularMarketPreviousClose, marketState, regularMarketTime, exchangeDataDelayedBy) is
    injectable for tests. One failed symbol never stops the rest."""
    ymap = yahoo_symbol_map(universe)
    client = client or _YFinanceClient()
    if isinstance(now, str):
        now = datetime.fromisoformat(now.replace("Z", "+00:00"))
    now = now or datetime.now(timezone.utc)
    now = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    quotes, missing = [], {}
    for sym in symbols:
        ticker = ymap.get(sym)
        if not ticker:
            missing[sym] = "no Yahoo ticker in universe"
            continue
        try:
            q = client.quote(ticker) or {}
        except Exception as exc:  # noqa: BLE001 - vendor errors are per symbol
            missing[sym] = f"quote request failed ({type(exc).__name__})"
            continue
        last = _number(q.get("regularMarketPrice"))
        if last is None:
            missing[sym] = "no regular-market price"
            continue
        prior = _number(q.get("regularMarketPreviousClose"))
        if prior is None:
            prior = _number(q.get("previousClose"))
        quotes.append({"symbol": sym, "yahoo": ticker, "last": last, "prior_close": prior,
                       "data_status": _status(q), "market_state": q.get("marketState"),
                       "ts": _ts(q.get("regularMarketTime")), "currency": q.get("currency")})
    return {"retrieved_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": SOURCE, "quotes": quotes,
            "missing": sorted(missing), "missing_reasons": missing}
