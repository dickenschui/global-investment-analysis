"""US Treasury curve and volatility term structure from CBOE indices collected through IBKR.

rates.json (data/history/weekly_long) holds weekly closes for IRX (13-week), FVX (5-year), TNX (10-year),
TYX (30-year), all quoted at ten times the yield in percent (40.57 = 4.057%), and VIX3M (3-month VIX, points).
Readings follow the monitor's rule: a label plus the numbers behind it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

YIELDS = {"IRX": "3m", "FVX": "5y", "TNX": "10y", "TYX": "30y"}
FLAT = 0.5            # 10y-3m spread below this (percentage points) reads as flat
CHANGE_WEEKS = 13
BACKWARDATION = 1.0   # VIX / VIX3M above this: near-term fear above 3-month


def load_rates(directory: str | Path) -> pd.DataFrame | None:
    """Weekly yields in percent (columns 3m, 5y, 10y, 30y) and VIX3M in points, indexed by Friday week-end."""
    path = Path(directory) / "rates.json"
    if not path.exists():
        return None
    cols = {}
    for inst in json.loads(path.read_text())["instruments"]:
        sym = inst.get("symbol")
        if inst.get("status") != "ok" or (sym not in YIELDS and sym != "VIX3M"):
            continue
        w = inst["weekly"]
        s = pd.Series(w["close"], index=pd.to_datetime(w["time"]).tz_localize(None), dtype=float)
        s.index = s.index.to_period("W-FRI").end_time.normalize()
        s = s[~s.index.duplicated(keep="last")].sort_index()
        if sym in YIELDS:
            s = s / 10.0
            if s.max() > 25:
                raise ValueError(f"{sym} scale check failed: a yield above 25% means the quote is not x10")
        cols[YIELDS.get(sym, sym)] = s
    return pd.DataFrame(cols).sort_index() if cols else None


def curve_readings(rates: pd.DataFrame | None, vix: pd.Series | None = None) -> list[dict]:
    """Yield-curve shape and VIX term structure as monitor regime readings (topic, reading, evidence)."""
    out = []
    if rates is None or rates.empty:
        return out
    if {"3m", "10y"} <= set(rates):
        c = rates[["3m", "10y"]].dropna()
        if len(c):
            spread = c["10y"] - c["3m"]
            last = float(spread.iloc[-1])
            shape = "inverted" if last < 0 else "flat" if last < FLAT else "upward-sloping"
            ev = f"10y {c['10y'].iloc[-1]:.2f}% vs 3m {c['3m'].iloc[-1]:.2f}% (spread {last:+.2f} pts"
            if len(spread) > CHANGE_WEEKS:
                chg = last - float(spread.iloc[-1 - CHANGE_WEEKS])
                ev += f", {chg:+.2f} pts over 13 weeks"
                shape += ", steepening" if chg > 0 else ", flattening" if chg < 0 else ""
            pct = float((spread <= last).mean())
            ev += f"; {pct:.0%} of weeks since {spread.index[0].year} were at or below this), week to {c.index[-1].date()}"
            out.append({"topic": "Yield curve", "reading": f"Curve {shape}", "evidence": ev})
    if "30y" in rates and "5y" in rates:
        c = rates[["5y", "30y"]].dropna()
        if len(c):
            s = float(c["30y"].iloc[-1] - c["5y"].iloc[-1])
            out.append({"topic": "Term premium", "reading": "Long end paying over 5-year" if s > 0 else "Long end below 5-year",
                        "evidence": f"30y {c['30y'].iloc[-1]:.2f}% vs 5y {c['5y'].iloc[-1]:.2f}% (spread {s:+.2f} pts)"})
    if vix is not None and "VIX3M" in rates:
        v = pd.concat([vix.rename("VIX"), rates["VIX3M"]], axis=1).dropna()
        if len(v):
            r = float(v["VIX"].iloc[-1] / v["VIX3M"].iloc[-1])
            label = ("Backwardation: near-term fear above 3-month" if r > BACKWARDATION
                     else "Contango: near-term volatility below 3-month (normal)")
            out.append({"topic": "Volatility term structure", "reading": label,
                        "evidence": f"VIX {v['VIX'].iloc[-1]:.2f} / VIX3M {v['VIX3M'].iloc[-1]:.2f} = {r:.2f}, "
                                    f"week to {v.index[-1].date()}"})
    return out
