"""Data loading: universe config, IBKR snapshot files, CSV prices and (optional) Yahoo.

Every loader returns the same shape so analytics never care where data came from:
  prices: DataFrame indexed by date (ascending), one column per config symbol, closes in
          the instrument's quote currency, NaN where a market had no bar.
  meta:   {symbol: dict} with currency, source, status and notes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE = REPO_ROOT / "config" / "universe.json"


def load_universe(path: str | Path = DEFAULT_UNIVERSE) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    symbols = [i["symbol"] for i in cfg["instruments"]]
    if len(symbols) != len(set(symbols)):
        raise ValueError("universe contains duplicate symbols")
    return cfg


def load_ibkr_snapshot(directory: str | Path, universe: dict) -> tuple[pd.DataFrame, dict]:
    """Read every <group>.json written by the IBKR collection step."""
    directory = Path(directory)
    by_symbol = {i["symbol"]: i for i in universe["instruments"]}
    series, meta = {}, {}
    files = sorted(f for f in directory.glob("*.json") if f.name != "options.json")  # chains load separately
    if not files:
        raise FileNotFoundError(f"no snapshot JSON files in {directory}")
    for f in files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        for row in payload["instruments"]:
            sym = row["symbol"]
            if sym not in by_symbol:
                raise ValueError(f"{f.name}: {sym} is not in the universe")
            weekly = row.get("weekly") or {}
            times, closes = weekly.get("time") or [], weekly.get("close") or []
            if len(times) != len(closes):
                raise ValueError(f"{sym}: time/close length mismatch")
            meta[sym] = {
                **by_symbol[sym],
                "source": payload.get("source", "unknown"),
                "retrieved_at_utc": payload.get("retrieved_at_utc"),
                "status": row.get("status"),
                "note": row.get("note") or "",
                "currency": row.get("currency"),
                "exchange": row.get("exchange"),
                "snapshot": row.get("snapshot") or {},
                "dividends": row.get("dividends") or [],
            }
            if times:
                idx = pd.to_datetime(times, utc=True).tz_convert(None).normalize()
                # IBKR stamps weekly bars with the week's first trading day, which differs by
                # venue around holidays (Mon vs Tue). Label each bar by its week ending Friday
                # so instruments line up on one calendar.
                idx = idx.to_period("W-FRI").end_time.normalize()
                s = pd.Series(pd.to_numeric(closes, errors="coerce"), index=idx, name=sym)
                if (s.dropna() <= 0).any():
                    raise ValueError(f"{sym}: non-positive close")
                series[sym] = s[~s.index.duplicated(keep="last")]
    # Keep every configured instrument visible, so an absent group file or an omitted
    # row is reported as missing rather than silently shrinking the universe.
    for sym, inst in by_symbol.items():
        meta.setdefault(sym, {**inst, "source": "not collected", "status": "missing",
                              "note": "no row in any snapshot file", "currency": None,
                              "snapshot": {}, "dividends": []})
    prices = pd.DataFrame(series).sort_index()
    return prices, meta


def load_csv_prices(directory: str | Path) -> pd.DataFrame:
    """One CSV per symbol named <symbol>.csv with columns date, close."""
    series = {}
    for f in sorted(Path(directory).glob("*.csv")):
        df = pd.read_csv(f, parse_dates=["date"])
        series[f.stem] = df.set_index("date")["close"].astype(float)
    if not series:
        raise FileNotFoundError(f"no CSV price files in {directory}")
    return pd.DataFrame(series).sort_index()


def fetch_yahoo(universe: dict, period: str = "1y", interval: str = "1d") -> tuple[pd.DataFrame, dict]:
    """Optional live source. Requires `pip install yfinance` and network access to Yahoo."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("yfinance is not installed; use an IBKR snapshot or CSV prices") from exc
    series, meta = {}, {}
    for inst in universe["instruments"]:
        ticker = yf.Ticker(inst["yahoo"])
        hist = ticker.history(period=period, interval=interval, auto_adjust=False)
        if hist.empty:
            meta[inst["symbol"]] = {**inst, "status": "unavailable", "source": "Yahoo Finance", "currency": None,
                                    "snapshot": {}, "dividends": []}
            continue
        close = hist["Close"]
        close.index = close.index.tz_localize(None).normalize()
        series[inst["symbol"]] = close
        divs = ticker.dividends
        dividends = [{"ex_date": d.tz_localize(None).date().isoformat(), "amount": float(a), "currency": None,
                      "type": "Yahoo dividend"} for d, a in divs.items()] if divs is not None else []
        currency = (ticker.history_metadata or {}).get("currency")
        meta[inst["symbol"]] = {**inst, "status": "ok", "source": "Yahoo Finance", "currency": currency,
                                "snapshot": {}, "dividends": dividends}
    return pd.DataFrame(series).sort_index(), meta


def load_cash_yield(directory: str | Path) -> pd.Series | None:
    """13-week US T-bill yield (annual, decimal) from rates.json in a history directory, if collected.
    IBKR's IRX index is quoted in percent; values above 25 are taken as tenths of a percent (CBOE x10 quote)."""
    path = Path(directory) / "rates.json"
    if not path.exists():
        return None
    for inst in json.loads(path.read_text())["instruments"]:
        if inst.get("symbol") == "IRX" and inst.get("status") == "ok":
            w = inst["weekly"]
            s = pd.Series(w["close"], index=pd.to_datetime(w["time"]).tz_localize(None), dtype=float)
            s.index = s.index.to_period("W-FRI").end_time.normalize()
            s = s[~s.index.duplicated(keep="last")].sort_index()
            scale = 1000.0 if s.median() > 25 else 100.0
            return (s / scale).clip(lower=0)
    return None
