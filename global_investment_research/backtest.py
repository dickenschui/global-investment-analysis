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


COST = 0.001  # 10 bp of traded value, charged on turnover (one-way), for every simulated portfolio


def forward_return(s: pd.Series, h: int) -> pd.Series:
    """Return from each bar to the bar h bars later, kept only when those bars are h weeks apart (3 days'
    slack). Across a data gap (a suspended line, a long holiday) the move is not a weekly return; drop it."""
    r = s.shift(-h) / s - 1
    gap = (s.index.to_series().shift(-h) - s.index.to_series()).dt.days
    return r.where(gap <= 7 * h + 3)


def stance_panel(prices: pd.DataFrame, funds: set | None = None) -> pd.DataFrame:
    """Long table: date, symbol, stance, size, levels, forward returns. Uses only past data for the stance."""
    funds = funds or set()
    rows = []
    for sym in prices.columns:
        s = prices[sym].dropna()
        if len(s) < WARMUP + 2:
            continue
        ma10, ma40 = s.rolling(10).mean(), s.rolling(40).mean()
        r13, r52 = s / s.shift(13) - 1, s / s.shift(52) - 1
        vol = s.pct_change().rolling(52).std() * math.sqrt(52)
        fwd = {h: forward_return(s, h) for h in HORIZONS}
        for i in range(WARMUP, len(s)):
            last, sh, lo = s.iloc[i], ma10.iloc[i], ma40.iloc[i]
            state = "up" if last > sh > lo else "down" if last < sh < lo else "mixed"
            v = vol.iloc[i]
            mom = (0.5 * r13.iloc[i] + 0.5 * r52.iloc[i]) / v if v and v > 0 else None
            call = D.equity_call({"last": last, "momentum_score": mom,
                                  "trend": {"state": state, "short_ma": sh, "long_ma": lo, "pct_vs_long_ma": last / lo - 1}},
                                 is_fund=sym in funds)
            rows.append({"date": s.index[i], "symbol": sym, "stance": call["stance"], "size": call["size"],
                         "last": last, "ma40": lo,
                         **{f"fwd{h}": fwd[h].iloc[i] for h in HORIZONS}})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # next-week return for the strategy simulation
    nxt = []
    for sym, g in df.groupby("symbol"):
        s = prices[sym].dropna()
        r = forward_return(s, 1).reindex(g["date"])
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


def _cash_weekly(index: pd.DatetimeIndex, cash: pd.Series | None) -> pd.Series:
    """Weekly cash return from an annual T-bill yield series (decimal), last value known at each date."""
    if cash is None or cash.empty:
        return pd.Series(0.0, index=index)
    return (cash.sort_index().reindex(index, method="ffill").fillna(0.0) / 52)


def _perf(r: pd.Series, rf: pd.Series) -> dict:
    w = (1 + r).cumprod()
    yrs = len(r) / 52
    ex = r - rf.reindex(r.index).fillna(0.0)
    sd = r.std(ddof=1)
    return {"cagr": float(w.iloc[-1] ** (1 / yrs) - 1) if yrs > 0 else None, "vol": float(sd * math.sqrt(52)),
            "sharpe": float(ex.mean() / sd * math.sqrt(52)) if sd > 0 else None,
            "max_drawdown": float((w / w.cummax() - 1).min()), "total": float(w.iloc[-1] - 1)}


def desk_portfolio(df: pd.DataFrame, cash: pd.Series | None = None, cost: float = COST,
                   cap_clusters: bool = D.CAP_CLUSTERS, lag: int = 0, size_scale: float = 1.0) -> dict:
    """Simulate the Desk as it is meant to be used: buy a name when it turns Accumulate at the rule's
    risk-based size (0.5% risk to the exit level, capped), hold it until the weekly close falls below the
    exit level (40-week average x 0.97, re-set every week), keep the rest in cash. Weights are reset to
    target each week; if targets add up to more than 100% they are scaled down pro rata. Costs are charged
    on turnover. The comparison holds the same exposure each week spread equally over every instrument,
    so the difference is the value of the selection, not of holding less."""
    d = df.dropna(subset=["next1"])
    dates = sorted(d["date"].unique())
    piv = {k: d.pivot(index="date", columns="symbol", values=k) for k in ("stance", "size", "last", "ma40", "next1")}
    rf = _cash_weekly(pd.DatetimeIndex(dates), cash)
    rets = piv["last"].pct_change(fill_method=None)
    held, prev = {}, {}
    rows = []
    overshoot = []                      # how far below the exit level each exit actually closed
    queue = [{} for _ in range(lag)]   # lag=1: weights decided on a Friday earn from the following week
    for i, dt in enumerate(dates):
        st, sz, last, ma40, nx = (piv[k].loc[dt] for k in ("stance", "size", "last", "ma40", "next1"))
        for sym in list(held):
            if pd.isna(last.get(sym)):
                held.pop(sym)
            elif last[sym] < ma40[sym] * (1 - D.STOP_BUFFER):
                held.pop(sym)                                   # exit level broken on the weekly close
                overshoot.append(1 - last[sym] / (ma40[sym] * (1 - D.STOP_BUFFER)))
        for sym, stance in st.items():
            if sym not in held and stance == "Accumulate" and sz.get(sym) and sz[sym] > 0:
                held[sym] = float(sz[sym]) * size_scale
        if len(held) > 1:
            # same portfolio caps as the live Desk (gross, and clusters when switched on), trailing 52 weeks only
            dist = {k: last[k] / (ma40[k] * (1 - D.STOP_BUFFER)) - 1 for k in held}
            f, _ = D.cluster_factors(held, dist, rets[list(held)].iloc[max(0, i - 51): i + 1], cap_clusters)
            target = {k: v * f[k] for k, v in held.items()}
        else:
            target = dict(held)
        tot = sum(target.values())
        w = {k: v / tot for k, v in target.items()} if tot > 1 else target
        if lag:
            queue.append(w)
            w = {k: v for k, v in queue.pop(0).items() if pd.notna(nx.get(k))}
        expo = sum(w.values())
        turn = sum(abs(w.get(k, 0.0) - prev.get(k, 0.0)) for k in set(w) | set(prev))
        bench_eq = nx.dropna().mean()
        r = sum(v * nx[k] for k, v in w.items()) + (1 - expo) * rf[dt] - turn * cost
        rows.append((dt, r, expo, len(w), turn, expo * bench_eq + (1 - expo) * rf[dt]))
        prev = w
    out = pd.DataFrame(rows, columns=["date", "r", "expo", "names", "turn", "matched"]).set_index("date")
    mid = out.index[len(out) // 2]
    halves = {}
    for lab, m in (("first", out.index < mid), ("second", out.index >= mid)):
        a, b = _perf(out["r"][m], rf), _perf(out["matched"][m], rf)
        halves[lab] = {"desk": a["sharpe"], "matched": b["sharpe"]}
    act = (out["r"] - out["matched"])
    t = float(act.mean() / act.std(ddof=1) * math.sqrt(len(act))) if act.std(ddof=1) > 0 else None
    return {"desk": _perf(out["r"], rf), "matched": _perf(out["matched"], rf),
            "avg_exposure": float(out["expo"].mean()), "median_names": float(out["names"].median()),
            "annual_turnover": float(out["turn"].sum() / (len(out) / 52)), "halves": halves, "active_t": t,
            "beats_matched_both_halves": bool(all(h["desk"] is not None and h["matched"] is not None
                                                  and h["desk"] > h["matched"] for h in halves.values())),
            "cost_per_trade": cost, "cash_yield": cash is not None and not cash.empty, "lag_weeks": lag,
            "exit_overshoot": {"exits": len(overshoot), "mean": float(np.mean(overshoot)) if overshoot else None,
                               "median": float(np.median(overshoot)) if overshoot else None,
                               "p90": float(np.percentile(overshoot, 90)) if overshoot else None,
                               "worst": float(max(overshoot)) if overshoot else None},
            "years": {"desk": calendar_years(out["r"]), "matched": calendar_years(out["matched"])},
            "curve": {"dates": [x.date().isoformat() for x in out.index],
                      "desk": [float(v) for v in (1 + out["r"]).cumprod()],
                      "matched": [float(v) for v in (1 + out["matched"]).cumprod()]}}


def drop_bad_prints(prices: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Blank isolated one-week prints that are below 20% or above 5x BOTH neighbours (a unit
    error at the source, e.g. RIO 0.35 between 36.84 and 39.16). Raw files stay as returned."""
    out, fixes = prices.copy(), []
    for sym in prices.columns:
        s = prices[sym].dropna()
        prev, nxt = s.shift(1), s.shift(-1)
        bad = (s < 0.2 * prev.combine(nxt, min)) | (s > 5 * prev.combine(nxt, max))
        for d in s.index[bad.fillna(False)]:
            fixes.append({"symbol": sym, "date": d.date().isoformat(), "close": float(s[d]),
                          "previous": float(prev[d]), "next": float(nxt[d])})
            out.loc[d, sym] = np.nan
    return out, fixes


def drop_before_level_breaks(prices: pd.DataFrame, persist: int = 4) -> tuple[pd.DataFrame, list[dict]]:
    """A one-week move beyond 20x (or below 1/20) that does not reverse within `persist` weeks is a change of
    quote unit (pence/pounds) or an unadjusted corporate action, not a market move. Drop the history before
    it: rescaling would be an estimate."""
    out, fixes = prices.copy(), []
    for sym in prices.columns:
        s = prices[sym].dropna()
        ratio = s / s.shift(1)
        for i in np.where((ratio > 20) | (ratio < 0.05))[0]:
            after = s.iloc[i: i + persist + 1]
            if len(after) > persist and (after / after.iloc[0]).between(0.5, 2).all():
                d = s.index[i]
                out.loc[out.index < d, sym] = np.nan
                fixes.append({"symbol": sym, "date": f"before {d.date().isoformat()}",
                              "reason": f"price level changed {ratio.iloc[i]:.3g}x in one week and stayed there "
                                        "(quote unit or unadjusted corporate action); earlier history dropped"})
    return out, fixes


def apply_exceptions(prices: pd.DataFrame, exceptions: list[dict] | None) -> tuple[pd.DataFrame, list[dict]]:
    """Blank documented windows of unreliable source prices (config/data-exceptions.json)."""
    out, used = prices.copy(), []
    for e in exceptions or []:
        if e["symbol"] in out:
            m = (out.index >= pd.Timestamp(e["from"])) & (out.index <= pd.Timestamp(e["to"]))
            if out.loc[m, e["symbol"]].notna().any():
                out.loc[m, e["symbol"]] = np.nan
                used.append({"symbol": e["symbol"], "date": f"{e['from']} to {e['to']}", "reason": e["reason"]})
    return out, used


def run(prices: pd.DataFrame, cash: pd.Series | None = None, funds: set | None = None,
        exceptions: list[dict] | None = None) -> dict:
    prices, excluded = apply_exceptions(prices, exceptions)
    prices, breaks = drop_before_level_breaks(prices)
    prices, fixes = drop_bad_prints(prices)
    fixes = fixes + breaks + excluded
    df = stance_panel(prices, funds)
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
                        "significant": t_acc_avoid is not None and t_acc_avoid >= 2,
                        "strategy_beats_benchmark_risk_adjusted": (strat["strategy"]["sharpe"] or 0) > (strat["benchmark"]["sharpe"] or 0),
                        "drawdown_reduced": strat["strategy"]["max_drawdown"] > strat["benchmark"]["max_drawdown"]},
            "strategy": strat, "variants": compare_variants(prices), "data_fixes": fixes,
            "desk": desk_portfolio(df, cash), "desk_next_week": desk_portfolio(df, cash, lag=1),
            "desk_half_size": desk_portfolio(df, cash, size_scale=0.5),
            "desk_cluster_cap": desk_portfolio(df, cash, cap_clusters=True)}


# --- Rule variants -------------------------------------------------------------------
# Each variant maps (last, ma10, ma40) -> True (hold / buy) or False (cash). They are compared
# on the same history. A variant is only adopted as the live rule if it passes ADOPTION_TEST:
# a better Sharpe ratio than equal weight in both halves AND in all three thirds, and an active
# return (rule minus benchmark, weekly) with a t-statistic of at least ADOPTION_T.
VARIANTS = {
    "Current: trend up (price > 10w > 40w)": lambda p, s, l: p > s > l,
    "Pullback in up-trend (40w up, price below 10w)": lambda p, s, l: p > l and p < s,
    "Long-trend filter (price > 40w)": lambda p, s, l: p > l,
    "Contrarian (price below 40w)": lambda p, s, l: p < l,
}


ADOPTION_T = 2.0


def calendar_years(r: pd.Series) -> dict:
    """Compounded return per calendar year (keyed by year string)."""
    return {str(y): float((1 + g).prod() - 1) for y, g in r.groupby(r.index.year)}


def compare_variants(prices: pd.DataFrame) -> list[dict]:
    rows = []
    for sym in prices.columns:
        s = prices[sym].dropna()
        if len(s) < WARMUP + 2:
            continue
        ma10, ma40 = s.rolling(10).mean(), s.rolling(40).mean()
        nxt = forward_return(s, 1)
        f13 = forward_return(s, 13)
        for i in range(WARMUP, len(s)):
            rows.append({"date": s.index[i], "symbol": sym, "p": s.iloc[i], "s": ma10.iloc[i], "l": ma40.iloc[i],
                         "next1": nxt.iloc[i], "fwd13": f13.iloc[i]})
    df = pd.DataFrame(rows)
    dates = sorted(df["date"].unique())
    mid = dates[len(dates) // 2]
    bench = df.dropna(subset=["next1"]).groupby("date")["next1"].mean()
    out = []
    for name, rule in VARIANTS.items():
        on = df.apply(lambda r: bool(rule(r.p, r.s, r.l)), axis=1)
        d = df.assign(on=on)
        held = d[d.on].dropna(subset=["next1"]).groupby("date")["next1"].mean().reindex(bench.index).fillna(0.0)

        def sharpe(r):
            return float(r.mean() / r.std(ddof=1) * math.sqrt(52)) if r.std(ddof=1) > 0 else None

        def mdd(r):
            w = (1 + r).cumprod()
            return float((w / w.cummax() - 1).min())
        halves = {}
        for label, mask in (("first", bench.index < mid), ("second", bench.index >= mid)):
            halves[label] = {"sharpe": sharpe(held[mask]), "bench_sharpe": sharpe(bench[mask])}
        thirds = [(sharpe(held.iloc[k]), sharpe(bench.iloc[k])) for k in np.array_split(np.arange(len(bench)), 3)]
        thirds_won = sum(1 for a, b in thirds if a is not None and b is not None and a > b)
        act = (held - bench).dropna()
        active_t = float(act.mean() / act.std(ddof=1) * math.sqrt(len(act))) if act.std(ddof=1) > 0 else None
        names = d[d.on].groupby("date").size().reindex(bench.index).fillna(0)
        ts = d.groupby(["symbol", "on"])["fwd13"].mean().unstack()
        wins = int(((ts.get(True) - ts.get(False)) > 0).sum()) if True in ts and False in ts else None
        yrs = len(held) / 52
        out.append({"variant": name, "cagr": float((1 + held).prod() ** (1 / yrs) - 1), "sharpe": sharpe(held),
                    "max_drawdown": mdd(held), "invested": float(d.groupby("date")["on"].mean().mean()),
                    "sharpe_first_half": halves["first"]["sharpe"], "sharpe_second_half": halves["second"]["sharpe"],
                    "bench_sharpe_first_half": halves["first"]["bench_sharpe"],
                    "bench_sharpe_second_half": halves["second"]["bench_sharpe"],
                    "instruments_where_signal_helped": wins, "instruments": int(ts.shape[0]), "years": calendar_years(held),
                    "beats_benchmark_both_halves": bool(halves["first"]["sharpe"] and halves["second"]["sharpe"]
                                                        and halves["first"]["sharpe"] > halves["first"]["bench_sharpe"]
                                                        and halves["second"]["sharpe"] > halves["second"]["bench_sharpe"]),
                    "sharpe_thirds": [a for a, _ in thirds], "bench_sharpe_thirds": [b for _, b in thirds],
                    "thirds_won": thirds_won, "active_t": active_t, "median_names_held": float(names.median()),
                    "weeks_all_cash": int((names == 0).sum())})
        x = out[-1]
        x["passes_adoption_test"] = bool(x["beats_benchmark_both_halves"] and thirds_won == 3
                                         and active_t is not None and active_t >= ADOPTION_T)
    b_yrs = len(bench) / 52
    out.append({"variant": "Benchmark: equal weight, always invested", "cagr": float((1 + bench).prod() ** (1 / b_yrs) - 1),
                "sharpe": float(bench.mean() / bench.std(ddof=1) * math.sqrt(52)),
                "max_drawdown": float(((1 + bench).cumprod() / (1 + bench).cumprod().cummax() - 1).min()), "invested": 1.0,
                "sharpe_first_half": None, "sharpe_second_half": None, "instruments_where_signal_helped": None,
                "instruments": None, "beats_benchmark_both_halves": None, "years": calendar_years(bench),
                "sharpe_thirds": [sharpe(bench.iloc[k]) for k in np.array_split(np.arange(len(bench)), 3)],
                "thirds_won": None, "active_t": None, "median_names_held": None, "weeks_all_cash": 0,
                "passes_adoption_test": None})
    return out
