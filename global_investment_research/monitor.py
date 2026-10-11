"""Daily/weekly cross-market monitor: markets, sectors, stocks and the macro backdrop.

Design rules
- Every reading cites the numbers behind it; no reading without evidence.
- Patterns are only called 'stable' if they hold in both halves of the sample.
- Macro events come from a user-maintained CSV; nothing is inferred or invented.
- Each run appends a compact record to history.jsonl so trends in the signals
  themselves can be studied as runs accumulate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import analytics as an, curve

MARKET_PROXIES = {"US": "SPY", "UK": "ISF", "SG": "ES3", "HK": "2800", "China": "3188"}
MACRO_FACTORS = {"US dollar": "UUP", "Long Treasuries": "TLT", "Gold": "GLD", "Crude oil": "USO",
                 "Copper": "CPER", "Volatility (VIX)": "VIX", "High-yield credit": "HYG"}


def _get(metrics: dict, sym: str, key: str):
    m = metrics.get(sym)
    return None if m is None else m.get(key)


def market_breadth(metrics: dict, meta: dict, market: str) -> dict:
    stocks = [s for s, m in meta.items() if m["market"] == market and m["kind"] == "stock" and s in metrics]
    states = [metrics[s]["trend"]["state"] for s in stocks]
    known = [x for x in states if x is not None]
    r3 = [metrics[s]["ret_3m"] for s in stocks if metrics[s]["ret_3m"] is not None]
    ranked = sorted((s for s in stocks if metrics[s]["ret_3m"] is not None), key=lambda s: metrics[s]["ret_3m"], reverse=True)
    return {
        "stocks": len(stocks),
        "pct_uptrend": None if not known else sum(x == "up" for x in known) / len(known),
        "pct_downtrend": None if not known else sum(x == "down" for x in known) / len(known),
        "median_ret_3m": None if not r3 else float(pd.Series(r3).median()),
        "leaders": ranked[:3],
        "laggards": ranked[-3:][::-1],
    }


def regime_readings(metrics: dict) -> list[dict]:
    """Rule-based macro readings. Each returns label + the evidence used."""
    out = []

    def add(topic, label, evidence):
        out.append({"topic": topic, "reading": label, "evidence": evidence})

    vix = metrics.get("VIX")
    if vix and vix["last"] is not None and vix["trend"]["long_ma"]:
        lvl, ma = vix["last"], vix["trend"]["long_ma"]
        label = "calm" if lvl < 16 else "elevated" if lvl < 25 else "stressed"
        add("Risk appetite", f"Volatility {label}", f"VIX {lvl:.1f} vs 40-week average {ma:.1f}")
    for topic, sym, up, down in [
        ("US dollar", "UUP", "Dollar strengthening", "Dollar weakening"),
        ("Rates", "TLT", "Long yields falling (bond prices up-trend)", "Long yields rising (bond prices down-trend)"),
        ("Gold", "GLD", "Gold in up-trend", "Gold in down-trend"),
        ("Oil", "USO", "Oil in up-trend", "Oil in down-trend"),
    ]:
        m = metrics.get(sym)
        if m and m["trend"]["state"]:
            st = m["trend"]["state"]
            label = up if st == "up" else down if st == "down" else f"{topic}: no clear trend"
            add(topic, label, f"{sym} trend={st} (price {fmt_pct(m['trend']['pct_vs_long_ma'])} vs 40-week average), "
                              f"3m {fmt_pct(m['ret_3m'])}, 1y {fmt_pct(m['ret_1y'])}")
    for topic, a, b, pos, neg in [
        ("Growth vs defensives", "XLY", "XLP", "Cyclicals leading defensives", "Defensives leading cyclicals"),
        ("Copper vs gold", "CPER", "GLD", "Copper outpacing gold (growth-positive)", "Gold outpacing copper (caution)"),
        ("Asia vs US", "2800", "SPY", "Hong Kong outperforming US", "US outperforming Hong Kong"),
        ("China A vs US", "3188", "SPY", "China A-shares outperforming US", "US outperforming China A-shares"),
        ("Credit", "HYG", "LQD", "High-yield keeping pace with investment grade (credit calm)",
         "High-yield lagging investment grade (credit stress building)"),
    ]:
        ra, rb = _get(metrics, a, "ret_3m"), _get(metrics, b, "ret_3m")
        if ra is not None and rb is not None:
            add(topic, pos if ra > rb else neg, f"3m: {a} {fmt_pct(ra)} vs {b} {fmt_pct(rb)} (spread {fmt_pct(ra - rb)})")
    return out


def macro_links(prices: pd.DataFrame) -> list[dict]:
    rets = an.returns_frame(prices)
    rows = []
    for market, proxy in MARKET_PROXIES.items():
        for factor, fsym in MACRO_FACTORS.items():
            if proxy in rets and fsym in rets:
                c = an.split_correlation(rets, proxy, fsym)
                rows.append({"market": market, "proxy": proxy, "factor": factor, "factor_symbol": fsym, **c})
    return rows


def event_reactions(prices: pd.DataFrame, events_csv: str | Path | None) -> list[dict]:
    """Return of each market proxy over the first full bar after each event.

    The bar containing the event also holds pre-event days, so the reaction is measured
    from the first close on/after the event date to the following close.
    """
    if not events_csv or not Path(events_csv).exists():
        return []
    events = pd.read_csv(events_csv, parse_dates=["date"])
    if events.empty:
        return []
    rets = an.returns_frame(prices)
    out = []
    for _, ev in events.iterrows():
        after = rets.index[rets.index >= ev["date"]]
        if len(after) < 2:
            continue
        bar = after[1]
        reactions = {m: (None if p not in rets or pd.isna(rets.at[bar, p]) else float(rets.at[bar, p]))
                     for m, p in MARKET_PROXIES.items()}
        out.append({"date": ev["date"].date().isoformat(), "event": ev["event"], "category": ev.get("category", ""),
                    "bar_end": bar.date().isoformat(), "reactions": reactions})
    return out


def fmt_pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def build_monitor(prices: pd.DataFrame, meta: dict, events_csv=None, window_days: int = 372,
                  rates: pd.DataFrame | None = None, macro: dict | None = None) -> dict:
    # One common look-back window, so a source that returns extra history (e.g. 2 years
    # for one instrument) does not change drawdown or correlation baselines.
    prices = prices[prices.index > prices.index.max() - pd.Timedelta(days=window_days)]
    bpy = an.infer_bars_per_year(prices.index)
    metrics = {s: an.instrument_metrics(prices[s], bpy) for s in prices.columns if prices[s].notna().sum() >= 2}
    stale_cutoff = prices.index.max() - pd.Timedelta(days=10)
    stale = [s for s in metrics if pd.Timestamp(metrics[s]["last_date"]) < stale_cutoff]
    missing = sorted(s for s in meta if s not in metrics)  # meta always covers the full universe
    sectors = sorted(
        ({"symbol": s, "sector": meta[s]["sector"], "ret_1m": m["ret_1m"], "ret_3m": m["ret_3m"],
          "ret_1y": m["ret_1y"], "trend": m["trend"]["state"]}
         for s, m in metrics.items() if meta[s]["kind"] == "sector_proxy"),
        key=lambda r: (r["ret_3m"] is None, -(r["ret_3m"] or 0)))
    regime = regime_readings(metrics)
    rates_note = None
    if rates is not None:
        rates = rates[rates.index <= prices.index.max()]  # never read past the snapshot week
        if len(rates) and rates.index.max() >= stale_cutoff:
            regime += curve.curve_readings(rates, prices["VIX"] if "VIX" in prices else None)
        else:
            rates_note = f"curve history ends {rates.index.max().date() if len(rates) else 'before the window'}: refresh rates.json"
    macro_note = None
    if macro is not None:  # official series fetched daily on GitHub Actions (data/macro/latest.json)
        got = pd.Timestamp(str(macro.get("retrieved_at", ""))[:10] or None)
        if pd.isna(got) or got < stale_cutoff:
            macro_note = f"official macro snapshot from {macro.get('retrieved_at') or 'unknown date'} is stale: not used"
        else:
            from . import macro_sources
            as_of = prices.index.max().date().isoformat()   # never read an observation dated after the snapshot week
            macro = {**macro, "series": {k: {**v, "observations": [o for o in v.get("observations") or []
                                                                    if str(o["date"]) <= as_of]}
                                         for k, v in (macro.get("series") or {}).items()}}
            have = {r["topic"] for r in regime}
            for r in macro_sources.readings(macro):
                if r["reading"] == macro_sources.NA:
                    continue  # reported in the macro snapshot's own failure list
                r = {**r, "topic": r["topic"] + (" (official)" if r["topic"] in have else ""),
                     "evidence": r["evidence"] + f" [official source, fetched {macro['retrieved_at'][:10]}]"}
                regime.append(r)
    return {
        "as_of": prices.index.max().date().isoformat(),
        "bars_per_year": bpy,
        "metrics": metrics,
        "markets": {m: {"proxy": p, "proxy_metrics": metrics.get(p), "breadth": market_breadth(metrics, meta, m)}
                    for m, p in MARKET_PROXIES.items()},
        "us_sectors": sectors,
        "regime": regime,
        "macro_links": macro_links(prices),
        "events": event_reactions(prices, events_csv),
        "data_quality": {"instruments_configured": len(meta), "with_prices": len(metrics),
                         "missing": missing, "stale_over_10_days": sorted(stale),
                         "rates": rates_note, "macro": macro_note},
    }


def append_history(result: dict, state_dir: str | Path) -> Path:
    """One line per run; re-running the same as_of date replaces that date's line."""
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    path = state / "history.jsonl"
    record = {
        "as_of": result["as_of"],
        "markets": {m: {"ret_1w": (v["proxy_metrics"] or {}).get("ret_1w"),
                        "ret_3m": (v["proxy_metrics"] or {}).get("ret_3m"),
                        "trend": ((v["proxy_metrics"] or {}).get("trend") or {}).get("state"),
                        "pct_uptrend": v["breadth"]["pct_uptrend"]} for m, v in result["markets"].items()},
        "regime": {r["topic"]: r["reading"] for r in result["regime"]},
    }
    lines = [l for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    lines = [l for l in lines if json.loads(l)["as_of"] != record["as_of"]] + [json.dumps(record)]
    path.write_text("\n".join(sorted(lines, key=lambda l: json.loads(l)["as_of"])) + "\n")
    return path


def regime_changes(state_dir: str | Path) -> list[dict]:
    """Compare the two most recent history records and list readings that flipped."""
    path = Path(state_dir) / "history.jsonl"
    if not path.exists():
        return []
    recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if len(recs) < 2:
        return []
    prev, cur = recs[-2], recs[-1]
    return [{"topic": t, "from": prev["regime"].get(t), "to": r, "since": prev["as_of"]}
            for t, r in cur["regime"].items() if prev["regime"].get(t) not in (None, r)]
