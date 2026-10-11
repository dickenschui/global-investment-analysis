from datetime import datetime, timezone

from global_investment_research import alerts as A
from global_investment_research import pulse_yahoo as P

UNIVERSE = {"instruments": [
    {"symbol": "XOM", "yahoo": "XOM"}, {"symbol": "SHEL", "yahoo": "SHEL.L"}, {"symbol": "D05", "yahoo": "D05.SI"},
    {"symbol": "VIX", "yahoo": "^VIX"}, {"symbol": "GBP.USD", "yahoo": "GBPUSD=X"}, {"symbol": "NOYAHOO"}]}
NOW = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
T = int(NOW.timestamp())


class FakeYahoo:
    """Yahoo quote fields as yfinance's Ticker.info returns them."""
    data = {
        "XOM": {"regularMarketPrice": 140.0, "regularMarketPreviousClose": 160.0, "marketState": "REGULAR",
                "regularMarketTime": T - 900, "exchangeDataDelayedBy": 0, "currency": "USD"},
        "SHEL.L": {"regularMarketPrice": 3050.0, "regularMarketPreviousClose": 3100.0, "marketState": "REGULAR",
                   "regularMarketTime": T - 1200, "exchangeDataDelayedBy": 15, "currency": "GBp"},
        "D05.SI": {"regularMarketPrice": 44.1, "previousClose": 44.0, "marketState": "CLOSED",
                   "regularMarketTime": T - 6 * 3600},
        "^VIX": {"regularMarketPrice": None, "marketState": "REGULAR"},
    }

    def __init__(self):
        self.calls = []

    def quote(self, ticker):
        self.calls.append(ticker)
        if ticker == "GBPUSD=X":
            raise ConnectionError("rate limited")
        return self.data[ticker]


def test_symbol_map():
    m = P.yahoo_symbol_map(UNIVERSE)
    assert m["SHEL"] == "SHEL.L" and m["VIX"] == "^VIX" and "NOYAHOO" not in m


def test_build_pulse_statuses_missing_and_raw_pence():
    fake = FakeYahoo()
    p = P.build_pulse(["XOM", "SHEL", "D05", "VIX", "GBP.USD", "NOYAHOO"], UNIVERSE, client=fake, now=NOW)
    assert p["retrieved_at_utc"] == "2026-10-12T14:00:00Z" and p["source"] == "Yahoo Finance"
    q = {x["symbol"]: x for x in p["quotes"]}
    assert q["XOM"] == {**q["XOM"], "last": 140.0, "prior_close": 160.0, "data_status": "REALTIME", "ts": T - 900}
    assert q["SHEL"]["last"] == 3050.0 and q["SHEL"]["data_status"] == "DELAYED"       # pence left raw
    assert q["D05"]["data_status"] == "FROZEN" and q["D05"]["prior_close"] == 44.0      # closed market
    assert p["missing"] == ["GBP.USD", "NOYAHOO", "VIX"]
    assert p["missing_reasons"]["VIX"] == "no regular-market price"
    assert p["missing_reasons"]["GBP.USD"] == "quote request failed (ConnectionError)"
    assert "NOYAHOO" not in fake.calls


def test_pulse_feeds_alerts_end_to_end():
    p = P.build_pulse(["XOM", "SHEL", "D05"], UNIVERSE, client=FakeYahoo(), now=NOW)
    mon = {"metrics": {"XOM": {"vol": 0.25}, "SHEL": {"vol": 0.25, "last": 37.95}, "D05": {"vol": 0.2, "last": 44.0}}}
    usable, stale = A.normalise_quotes(p, mon)
    assert usable["SHEL"]["last"] == 30.5                       # rescaled from pence by alerts, not by the pulse
    assert stale == ["D05"]                                     # CLOSED -> FROZEN -> stale
    dec = {"calls": [{"symbol": "XOM", "stance": "Accumulate", "stop": 146.0, "trigger": None}]}
    assert ("XOM", "Exit level breached") in {(a["symbol"], a["rule"]) for a in A.evaluate(p, dec, mon)}
