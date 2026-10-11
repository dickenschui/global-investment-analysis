"""Decision engine: turns monitor metrics into explicit, rule-based calls.

Every call carries: the stance, the rule that produced it, an entry plan, an
invalidation level (the price that proves the call wrong), a volatility-based
position size, and what must still be verified. Rules are deliberately simple
and fully documented in docs/HANDBOOK.md so a human can audit and override them.
"""
from __future__ import annotations

import math

import pandas as pd

from .income import income_row

# Risk policy (see HANDBOOK.md section 4). Change here, not in the reports.
RISK_PER_POSITION = 0.005      # portfolio fraction lost if the invalidation level hits
MAX_POSITION = 0.08            # single-name cap
MAX_POSITION_ETF = 0.20        # broad index / sector ETF cap
STOP_BUFFER = 0.03             # invalidation = 3% below the 40-week average
EXTENDED = 0.20                # >20% above the 40-week average = do not chase

STANCE_ORDER = {"Accumulate": 0, "Hold": 1, "Watch": 2, "Avoid": 3}


def equity_call(m: dict, regime_stressed: bool = False, is_fund: bool = False) -> dict:
    t = m["trend"]
    state, long_ma, short_ma, last = t["state"], t["long_ma"], t["short_ma"], m["last"]
    mom, vs_ma = m["momentum_score"], t["pct_vs_long_ma"]
    if state is None or last is None:
        return {"stance": "Watch", "rule": "insufficient history", "entry": None, "stop": None,
                "stop_distance": None, "size": None, "trigger": None}
    if state == "down":
        stance, rule = "Avoid", "Price below 10- and 40-week averages (down-trend): no new money until trend repairs."
    elif state == "mixed":
        stance, rule = "Watch", "Trend unresolved: wait for price to reclaim both averages."
    elif vs_ma is not None and vs_ma > EXTENDED:
        stance, rule = "Hold", f"Up-trend but {vs_ma*100:.0f}% above 40-week average: hold, do not chase; add on pullback."
    elif mom is not None and mom >= 0.5 and not regime_stressed:
        stance, rule = "Accumulate", "Up-trend with risk-adjusted momentum >= 0.5."
    else:
        stance, rule = "Hold", "Up-trend but momentum < 0.5 (or market stressed): hold existing, no urgency to add."
    if state != "up":
        # No position plan for non-up-trends: give the level that would reopen the case instead.
        return {"stance": stance, "rule": rule, "entry": None, "stop": None, "stop_distance": None, "size": None,
                "trigger": max(short_ma, long_ma)}
    stop = long_ma * (1 - STOP_BUFFER)
    # Buy zone: from the 10-week average (pullback) up to today's price.
    entry = (min(short_ma, last), last)
    dist = last / stop - 1
    size = min(RISK_PER_POSITION / dist, MAX_POSITION_ETF if is_fund else MAX_POSITION) if dist > 0 else None
    return {"stance": stance, "rule": rule, "entry": entry, "stop": stop, "stop_distance": dist, "size": size,
            "trigger": None}


def income_call(row: dict, m: dict, band=(0.06, 0.08)) -> dict:
    """Income mandate: buy for yield only inside the band, stage entries in a down-trend."""
    y, cash, price = row["yield"], row["trailing_cash"], row["price"]
    state = m["trend"]["state"] if m else None
    if y is None:
        return {"stance": "Watch", "rule": "no distribution data", "price_for_band": None}
    # Prices at which trailing cash gives the band edges (cash in price units after reconciliation).
    cash_eff = y * price
    p_low, p_mid = cash_eff / band[1], cash_eff / ((band[0] + band[1]) / 2)
    if band[0] <= y <= band[1]:
        if state == "down":
            stance = "Accumulate"
            rule = ("Yield in target band but price in down-trend: stage in thirds (now, at "
                    f"{p_mid:.3f} for 7% yield, and after trend repair).")
        else:
            stance, rule = "Accumulate", "Yield in target band and trend not down."
    elif y > band[1]:
        stance, rule = "Watch", "Yield above band: market is pricing a cut. Verify payout cover before buying."
    else:
        stance = "Hold" if state != "down" else "Avoid"
        rule = f"Yield {y*100:.2f}% below band; would reach 6% at {cash_eff/band[0]:.3f}."
    return {"stance": stance, "rule": rule, "price_for_7pct": p_mid, "price_for_8pct": p_low,
            "price_for_6pct": cash_eff / band[0]}


def fx_view(sym: str, m: dict) -> dict:
    base, quote = sym.split(".")
    st = m["trend"]["state"]
    if st == "up":
        view = f"{base} strengthening vs {quote}"
    elif st == "down":
        view = f"{base} weakening vs {quote}"
    else:
        view = "No clear trend"
    return {"pair": f"{base}/{quote}", "view": view, "state": st}


def macro_posture(regime: list[dict]) -> dict:
    """Translate readings into portfolio tilts. Each tilt cites the reading behind it."""
    r = {x["topic"]: x["reading"] for x in regime}
    tilts = []

    def tilt(action, because):
        tilts.append({"action": action, "because": because})

    vol = r.get("Risk appetite", "")
    stressed = "stressed" in vol
    risk_budget = 0.5 if stressed else 0.75 if "elevated" in vol else 1.0
    if "rising" in r.get("Rates", ""):
        tilt("Keep bond duration short; avoid adding long Treasuries", r["Rates"])
    if "strengthening" in r.get("US dollar", ""):
        tilt("Hedge or underweight non-USD assets for a USD-based portfolio; strong dollar is a headwind for HK/EM", r["US dollar"])
    if "up-trend" in r.get("Oil", ""):
        tilt("Keep energy exposure (oil up-trend supports XLE/SHEL/XOM)", r["Oil"])
    if "down-trend" in r.get("Gold", ""):
        tilt("Do not add gold until it reclaims its 40-week average", r["Gold"])
    if "Defensives leading" in r.get("Growth vs defensives", ""):
        tilt("Favour quality and defensives over consumer cyclicals", r["Growth vs defensives"])
    if "US outperforming" in r.get("China A vs US", ""):
        tilt("Underweight China A-shares; wait for relative strength to turn", r["China A vs US"])
    if "stress building" in r.get("Credit", ""):
        tilt("Reduce high-beta and leveraged names; credit is leading equities lower", r["Credit"])
    if "US outperforming" in r.get("Asia vs US", ""):
        tilt("Keep Hong Kong at or below benchmark weight", r["Asia vs US"])
    return {"risk_budget": risk_budget, "stressed": stressed, "tilts": tilts,
            "risk_note": f"Deploy {risk_budget*100:.0f}% of the normal risk budget ({vol or 'volatility n/a'})."}


def build_decisions(mon: dict, meta: dict, band=(0.06, 0.08)) -> dict:
    posture = macro_posture(mon["regime"])
    calls = []
    for sym, inst in meta.items():
        m = mon["metrics"].get(sym)
        if not m or inst["kind"] in ("fx", "macro_proxy"):
            continue
        is_fund = inst["kind"] != "stock"
        c = equity_call(m, posture["stressed"], is_fund)
        c.update(symbol=sym, name=inst["name"], market=inst["market"], group=inst["group"],
                 sector=inst["sector"], kind=inst["kind"], last=m["last"], ret_3m=m["ret_3m"],
                 ret_1y=m["ret_1y"], momentum=m["momentum_score"], vol=m["vol"], trend=m["trend"]["state"])
        if inst["group"] == "sg" and inst["kind"] == "stock":
            snap = inst.get("snapshot") or {}
            price = snap.get("last") or m["last"]
            row = income_row(sym, price, pd.Timestamp(mon["as_of"]), inst.get("dividends", []),
                             snap.get("dividend_yield_pct"), band)
            ic = income_call(row, m, band)
            c.update(income=row, income_call=ic)
        calls.append(c)
    calls.sort(key=lambda c: (STANCE_ORDER[c["stance"]], -(c["momentum"] or -9)))
    fx = [dict(symbol=s, **fx_view(s, mon["metrics"][s]), ret_3m=mon["metrics"][s]["ret_3m"])
          for s, i in meta.items() if i["kind"] == "fx" and s in mon["metrics"]]
    return {"as_of": mon["as_of"], "posture": posture, "calls": calls, "fx": fx}


def review_log(log_csv, prices: pd.DataFrame) -> list[dict]:
    """Score past decisions against what happened since: return, exit breach, verdict.

    Log columns: date,symbol,decision,price,size_pct,exit_level,assumption,counter_case,review_date
    """
    log = pd.read_csv(log_csv, parse_dates=["date"])
    out = []
    for _, d in log.iterrows():
        s = prices.get(d["symbol"])
        if s is None or s.dropna().empty:
            out.append({"date": d["date"].date().isoformat(), "symbol": d["symbol"], "status": "no prices"})
            continue
        after = s[s.index > d["date"]].dropna()
        last = float(s.dropna().iloc[-1])
        ret = last / float(d["price"]) - 1
        exit_lvl = d.get("exit_level")
        breached = bool(pd.notna(exit_lvl) and not after.empty and (after < float(exit_lvl)).any())
        bullish = d["decision"] in ("Accumulate", "Hold")
        if breached:
            verdict = "exit level hit: close or justify in writing"
        elif bullish:
            verdict = "working" if ret > 0 else "not working yet"
        else:
            verdict = "avoided a loss" if ret < 0 else "missed a gain: check the rule"
        out.append({"date": d["date"].date().isoformat(), "symbol": d["symbol"], "decision": d["decision"],
                    "entry": float(d["price"]), "last": last, "return": ret, "exit_breached": breached, "verdict": verdict})
    return out


def position_value(size_fraction: float | None, portfolio: float, risk_budget: float) -> float | None:
    if size_fraction is None or not math.isfinite(size_fraction):
        return None
    return size_fraction * risk_budget * portfolio
