"""Walk-forward test of the Decision Desk rules on weekly history.

At every week t the stance is computed with decisions.equity_call from data up to t only
(no look-ahead), then scored on what happened over the following 4, 13 and 26 weeks.
Excess return = forward return minus the average forward return of all instruments on the
same date, which removes the overall market move and isolates what the rule adds.

Known biases, reported on the page: the instrument list was chosen today (survivorship),
returns are in local currency, weekly closes, no costs or taxes, no macro stress overlay.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import decisions as D

HORIZONS = (4, 13, 26)
WARMUP = 52  # weeks needed for 1-year momentum and 40-week average


def stance_panel(prices: pd.DataFrame) -> pd.DataFrame:
    """Long table: date, symbol, stance, forward returns. Uses only past data for the stance."""
    rows = []
    for sym in prices.columns:
        s = prices[sym].dropna()
        if len(s) < WARMUP + 2:
            continue
        ma10, ma40 = s.rolling(10).mean(), s.rolling(40).mean()
        r13, r52 = s / s.shift(13) - 1, s / s.shift(52) - 1
        vol = s.pct_change().rolling(52).std() * math.sqrt(52)
        fwd = {h: s.shift(-h) / s - 1 for h in HORIZONS}
        for i in range(WARMUP, len(s)):
            last, sh, lo = s.iloc[i], ma10.iloc[i], ma40.iloc[i]
            state = "up" if last > sh > lo else "down" if last < sh < lo else "mixed"
            v = vol.iloc[i]
            mom = (0.5 * r13.iloc[i] + 0.5 * r52.iloc[i]) / v if v and v > 0 else None
            call = D.equity_call({"last": last, "momentum_score": mom,
                                  "trend": {"state": state, "short_ma": sh, "long_ma": lo, "pct_vs_long_ma": last / lo - 1}})
            rows.append({"date": s.index[i], "symbol": sym, "stance": call["stance"],
                         **{f"fwd{h}": fwd[h].iloc[i] for h in HORIZONS}})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # next-week return for the strategy simulation
    nxt = []
    for sym, g in df.groupby("symbol"):
        s = prices[sym].dropna()
        r = (s.shift(-1) / s - 1).reindex(g["date"])
        nxt.append(pd.Series(r.values, index=g.index))
    df["next1"] = pd.concat(nxt).sort_index()
    for h in HORIZONS:
        df[f"xs{h}"] = df[f"fwd{h}"] - df.groupby("date")[f"fwd{h}"].transform("mean")
    return df


def _stats(x: pd.Series) -> dict:
    x = x.dropna()
    if x.empty:
        return {"n": 0, "mean": None, "median": None, "hit": None}
    return {"n": int(len(x)), "mean": float(x.mean()), "median": float(x.median()), "hit": float((x > 0).mean())}


def welch_t(a: pd.Series, b: pd.Series) -> float | None:
    a, b = a.dropna(), b.dropna()
    if len(a) < 5 or len(b) < 5:
        return None
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return None if se == 0 else float((a.mean() - b.mean()) / se)


def strategy(df: pd.DataFrame) -> dict:
    """Weekly: equal weight in names whose stance is Accumulate or Hold (trend up), else cash.
    Benchmark: equal weight in every instrument with data. Both earn next week's return."""
    d = df.dropna(subset=["next1"])
    by = d.groupby("date")
    bench = by["next1"].mean()
    held = d[d["stance"].isin(["Accumulate", "Hold"])].groupby("date")["next1"].mean().reindex(bench.index).fillna(0.0)
    invested = d.assign(h=d["stance"].isin(["Accumulate", "Hold"])).groupby("date")["h"].mean()

    def perf(r):
        w = (1 + r).cumprod()
        yrs = len(r) / 52
        return {"cagr": float(w.iloc[-1] ** (1 / yrs) - 1) if yrs > 0 else None,
                "vol": float(r.std(ddof=1) * math.sqrt(52)),
                "sharpe": float(r.mean() / r.std(ddof=1) * math.sqrt(52)) if r.std(ddof=1) > 0 else None,
                "max_drawdown": float((w / w.cummax() - 1).min()), "total": float(w.iloc[-1] - 1)}
    return {"strategy": perf(held), "benchmark": perf(bench), "avg_invested": float(invested.mean()),
            "weeks": int(len(bench)), "start": bench.index[0].date().isoformat(), "end": bench.index[-1].date().isoformat(),
            "curve": {"dates": [x.date().isoformat() for x in bench.index],
                      "strategy": [float(v) for v in (1 + held).cumprod()],
                      "benchmark": [float(v) for v in (1 + bench).cumprod()]}}


def run(prices: pd.DataFrame) -> dict:
    df = stance_panel(prices)
    if df.empty:
        raise ValueError("not enough history to backtest")
    dates = sorted(df["date"].unique())
    mid = dates[len(dates) // 2]
    by_stance = {}
    for st in D.STANCE_ORDER:
        g = df[df["stance"] == st]
        by_stance[st] = {f"{k}{h}": _stats(g[f"{k}{h}"]) for k in ("fwd", "xs") for h in HORIZONS}
        by_stance[st]["xs13_first_half"] = _stats(g[g["date"] < mid]["xs13"])
        by_stance[st]["xs13_second_half"] = _stats(g[g["date"] >= mid]["xs13"])
    # Non-overlapping 13-week samples for a fair significance test.
    keep = set(dates[::13])
    s13 = df[df["date"].isin(keep)]
    t_acc_avoid = welch_t(s13[s13["stance"] == "Accumulate"]["xs13"], s13[s13["stance"] == "Avoid"]["xs13"])
    acc, avo = by_stance["Accumulate"], by_stance["Avoid"]
    halves_ok = all(x["mean"] is not None for x in (acc["xs13_first_half"], acc["xs13_second_half"],
                                                     avo["xs13_first_half"], avo["xs13_second_half"]))
    supported = bool(halves_ok and acc["xs13_first_half"]["mean"] > 0 and acc["xs13_second_half"]["mean"] > 0
                     and avo["xs13_first_half"]["mean"] < 0 and avo["xs13_second_half"]["mean"] < 0)
    strat = strategy(df)
    return {"instruments": sorted(df["symbol"].unique().tolist()), "observations": int(len(df)),
            "first_date": dates[0].date().isoformat(), "last_date": dates[-1].date().isoformat(),
            "split_date": pd.Timestamp(mid).date().isoformat(),
            "by_stance": by_stance, "t_accumulate_vs_avoid_13w": t_acc_avoid,
            "verdict": {"supported_both_halves": supported,
                        "significant": t_acc_avoid is not None and abs(t_acc_avoid) >= 2,
                        "strategy_beats_benchmark_risk_adjusted": (strat["strategy"]["sharpe"] or 0) > (strat["benchmark"]["sharpe"] or 0),
                        "drawdown_reduced": strat["strategy"]["max_drawdown"] > strat["benchmark"]["max_drawdown"]},
            "strategy": strat}
