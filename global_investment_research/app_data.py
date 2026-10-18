"""One compact JSON file for the Investment Desk app (app/index.html), built from a week's reports.

Everything comes from engine output already written by `backtest` and `monitor`, plus the IBKR
contract ids in the snapshot so the app can ask for live quotes. Numbers are copied, not recomputed,
except the London pence factor, which is measured from the data (snapshot last vs weekly close).

The daily after-hours publish (docs/DAILY_UPDATE.md) adds `daily`: each session's official IBKR closes checked
against this week's levels. Levels and stances do not change between weekly refreshes; the Handbook acts on the
weekly close, so a daily close below an exit is a warning for Friday, not a new decision.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import data, decisions as D, evidence, monitor, notify

SPARK_WEEKS = 53
TOP_OPTIONS = 12
BAR_SHIFT_HOURS = 4    # a daily bar belongs to the session dated (bar time + 4h): FX bars start 21:15 UTC the evening
                       # before, every other line's bar starts on the session date itself
DAILY_CHECK = 0.25     # a close this far from the weekly close (or 5 weekly standard deviations, if wider) is held
                       # back for checking: it is more likely a unit or contract error than a move


def _contracts(snapshot_dir: Path) -> dict:
    out = {}
    for f in data.snapshot_files(snapshot_dir):
        for i in json.loads(f.read_text())["instruments"]:
            weekly = (i.get("weekly") or {}).get("close") or []
            snap_last = (i.get("snapshot") or {}).get("last")
            # London lines quote the live price in pence and weekly closes in pounds.
            factor = 100 if snap_last and weekly and snap_last / weekly[-1] > 50 else 1
            out[i["symbol"]] = {"id": i.get("contract_id"), "exchange": i.get("exchange"),
                                "sectype": i.get("security_type"), "currency": i.get("currency"),
                                "live_divisor": factor}
    return out


def _flags(row: dict, close: float) -> list[list[str]]:
    """The app's live-check rules (index.html flagsFor) applied to an official close: [[kind, text, rule], ...]."""
    out, stop, entry, trig, last = [], row.get("stop"), row.get("entry"), row.get("trigger"), row.get("last")
    if stop is not None and close < stop:
        out.append(["crit", f"Closed below the exit level {stop:,.4g}. The exit acts on Friday's weekly close.", "exit"])
    if entry and row.get("stance") == "Accumulate":
        if entry[0] <= close <= entry[1]:
            out.append(["good", f"Closed inside the buy zone {entry[0]:,.4g}–{entry[1]:,.4g}.", "in_zone"])
        elif close > entry[1]:
            out.append(["warn", f"Closed above the buy zone ({entry[1]:,.4g}): wait rather than chase.", "above_zone"])
        elif stop is None or close >= stop:
            out.append(["info", "Closed below the buy zone but above the exit: re-check the trend before adding.", "below_zone"])
    if trig is not None and row.get("stance") in ("Watch", "Avoid") and close > trig:
        out.append(["info", f"Closed above the revisit trigger {trig:,.4g}: confirm on Friday's close.", "trigger"])
    vol = row.get("vol")
    if vol and last:
        z = (close / last - 1) / (vol / 52 ** 0.5)
        if abs(z) >= 2.5:
            out.append(["crit" if abs(z) >= 4 else "warn",
                        f"{(close / last - 1) * 100:+.1f}% since the weekly close: {abs(z):.1f} weekly standard deviations.",
                        "move"])
    return out


def daily_view(calls: list[dict], others: list[dict], doc: dict) -> dict:
    """Official closes for one session against this week's levels.

    `doc` is a data/daily/<date>.json file: {"session_date", "source", "retrieved_at_utc",
    "closes": {symbol: {"close", "prior_close", "bar_time"}}, "missing": {symbol: reason}}. A close about 100x the
    weekly close (a London line in pence) is divided by 100; a bar that does not belong to the session date (a
    holiday, or IBKR not yet updated) is marked stale and not checked; a close further from the weekly close than
    DAILY_CHECK, or five weekly standard deviations of the instrument's own volatility if wider (VIX moves 35% in a
    normal week), is flagged for checking instead of being used."""
    session = dt.date.fromisoformat(doc["session_date"])
    rows, exceptions, stale, check = {}, [], [], []
    for r in calls + others:
        q = doc["closes"].get(r["symbol"])
        if not q or not q.get("close"):
            continue
        close, prior, last = q["close"], q.get("prior_close"), r.get("last")
        if last and close / last > 50:
            close, prior = close / 100, prior / 100 if prior else prior
        bar = dt.datetime.fromisoformat(q["bar_time"].replace("Z", "+00:00")) if q.get("bar_time") else None
        row = {"close": close, "prior_close": prior, "bar_time": q.get("bar_time"),
               "chg_1d": close / prior - 1 if prior else None, "vs_week": close / last - 1 if last else None}
        if bar is not None and (bar + dt.timedelta(hours=BAR_SHIFT_HOURS)).date() != session:
            row["stale"] = True
            stale.append(r["symbol"])
        elif last and abs(close / last - 1) > max(DAILY_CHECK, 5 * (r.get("vol") or 0) / 52 ** 0.5):
            row["check"] = True
            check.append(r["symbol"])
        elif "stance" in r:
            row["flags"] = _flags(r, close)
            exceptions += [{"symbol": r["symbol"], "kind": k, "text": t, "rule": rule} for k, t, rule in row["flags"]]
        rows[r["symbol"]] = row
    order = {"crit": 0, "warn": 1, "info": 2, "good": 3}
    return {"session_date": doc["session_date"], "source": doc.get("source"), "retrieved_at_utc": doc.get("retrieved_at_utc"),
            "rows": rows, "exceptions": sorted(exceptions, key=lambda x: (order[x["kind"]], x["symbol"])),
            "missing": sorted(set(doc.get("missing") or {}) | {r["symbol"] for r in calls + others} - set(rows)),
            "stale": sorted(stale), "check": sorted(check)}


def build(reports_dir: str | Path, snapshot_dir: str | Path, universe: dict,
          state_dir: str | Path | None = None, daily: str | Path | None = None) -> dict:
    rep, snap = Path(reports_dir), Path(snapshot_dir)
    dec = json.loads((rep / "decisions.json").read_text())
    mon = json.loads((rep / "monitor.json").read_text())
    opt = json.loads((rep / "options.json").read_text()) if (rep / "options.json").exists() else None
    bt = json.loads((rep / "backtest.json").read_text()) if (rep / "backtest.json").exists() else None
    prices, meta = data.load_ibkr_snapshot(snap, universe)
    contracts = _contracts(snap)

    def spark(sym):
        s = prices[sym].dropna() if sym in prices else None
        return [] if s is None else [float(v) for v in s.iloc[-SPARK_WEEKS:]]

    calls = []
    for c in dec["calls"]:
        row = {k: c.get(k) for k in ("symbol", "name", "market", "sector", "kind", "stance", "rule", "last", "entry",
                                     "stop", "stop_distance", "size", "trigger", "ret_3m", "ret_1y", "momentum", "vol",
                                     "trend")}
        if c.get("income") and c.get("income_call"):
            inc, ic = c["income"], c["income_call"]
            row["income"] = {"yield": inc["yield"], "basis": inc.get("yield_basis"), "payments": inc.get("payments"),
                             "stance": ic["stance"], "rule": ic["rule"], "price_for_6pct": ic.get("price_for_6pct"),
                             "price_for_7pct": ic.get("price_for_7pct"), "price_for_8pct": ic.get("price_for_8pct"),
                             "yield_after_cut": inc.get("yield_after_cut"), "yield_sustainable": inc.get("yield_sustainable"),
                             "yield_4y_avg": inc.get("yield_4y_avg"), "history_flag": inc.get("history_flag"),
                             "cash_by_year": inc.get("cash_by_year")}
        row["contract"] = contracts.get(c["symbol"])
        row["spark"] = spark(c["symbol"])
        calls.append(row)

    called = {c["symbol"] for c in calls}
    others = []  # FX and macro proxies the Desk does not rate but the app tracks live
    for inst in universe["instruments"]:
        s = inst["symbol"]
        if s in called or s not in mon["metrics"]:
            continue
        m = mon["metrics"][s]
        others.append({"symbol": s, "name": inst["name"], "kind": inst["kind"], "market": inst["market"],
                       "last": m["last"], "vol": m.get("vol"), "ret_1w": m["ret_1w"], "ret_3m": m["ret_3m"], "ret_1y": m.get("ret_1y"),
                       "trend": (m.get("trend") or {}).get("state"), "move_z": m.get("last_move_z"),
                       "contract": contracts.get(s), "spark": spark(s)})

    markets = []
    for name, v in mon["markets"].items():
        pm = v.get("proxy_metrics") or {}
        markets.append({"market": name, "proxy": v["proxy"], "last": pm.get("last"), "ret_1w": pm.get("ret_1w"),
                        "ret_3m": pm.get("ret_3m"), "ret_1y": pm.get("ret_1y"), "trend": (pm.get("trend") or {}).get("state"),
                        "pct_uptrend": v["breadth"]["pct_uptrend"], "leaders": v["breadth"].get("leaders", []),
                        "laggards": v["breadth"].get("laggards", []), "spark": spark(v["proxy"])})

    out = {
        "as_of": dec["as_of"],
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posture": dec["posture"],
        "markets": markets,
        "regime": mon["regime"],
        "changes": monitor.regime_changes(state_dir) if state_dir else [],
        "sectors": mon["us_sectors"],
        "macro_links": mon.get("macro_links", []),
        "fx": dec.get("fx", []),
        "calls": calls,
        "others": others,
        "data_quality": mon.get("data_quality"),
        "portfolio": dec.get("portfolio"),
        "stress": dec.get("stress"),
        "investor": D.INVESTOR,
        "links": notify.LINKS,
    }
    if daily:
        out["daily"] = daily_view(calls, others, json.loads(Path(daily).read_text()))
    if opt:
        seen, ideas = set(), []
        for i in opt["ideas"]:
            if len(ideas) == TOP_OPTIONS:
                break
            key = (i["symbol"], i["structure"])
            if key in seen:
                continue
            seen.add(key)
            ideas.append({k: i.get(k) for k in ("symbol", "direction", "stance", "structure", "expiry", "days", "underlying",
                                               "legs", "debit", "debit_per_contract", "breakeven", "breakeven_move",
                                               "max_loss", "max_gain", "reward_risk", "prob_profit", "iv", "one_sd_move",
                                               "return_at_1sd", "scenarios", "earnings")})
        out["options"] = {"ideas": ideas, "policy": opt["policy"], "chain_issues": opt.get("chain_issues")}
    if bt:
        st = bt["strategy"]
        curve = st["curve"]
        step = max(1, len(curve["dates"]) // 240)
        out["evidence"] = {
            "level": evidence.confidence_level(bt), "note": evidence.confidence_note(bt),
            "first_date": bt["first_date"], "last_date": bt["last_date"], "split_date": bt["split_date"],
            "instruments": len(bt["instruments"]), "observations": bt["observations"],
            "t_accumulate_vs_avoid_13w": bt["t_accumulate_vs_avoid_13w"],
            "by_stance": {k: {"xs13": v["xs13"]["mean"], "n": v["xs13"]["n"], "hit": v["xs13"]["hit"]}
                          for k, v in bt["by_stance"].items()},
            "strategy": st["strategy"], "benchmark": st["benchmark"], "avg_invested": st["avg_invested"],
            "curve": {"dates": curve["dates"][::step], "strategy": curve["strategy"][::step],
                      "benchmark": curve["benchmark"][::step]},
            "variants": bt.get("variants", []), "data_fixes": bt.get("data_fixes", []),
            "desk": {k: v for k, v in (bt.get("desk") or {}).items() if k != "curve"} or None,
            "desk_next_week": {k: v for k, v in (bt.get("desk_next_week") or {}).items() if k not in ("curve", "years")} or None,
            "desk_half_size": {k: v for k, v in (bt.get("desk_half_size") or {}).items() if k not in ("curve", "years")} or None,
            "desk_cluster_cap": {k: v for k, v in (bt.get("desk_cluster_cap") or {}).items() if k not in ("curve", "years")} or None,
            "universe_note": bt.get("universe_note"),
        }
    return out
