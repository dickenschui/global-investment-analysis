"""One compact JSON file for the Investment Desk app (app/index.html), built from a week's reports.

Everything comes from engine output already written by `backtest` and `monitor`, plus the IBKR
contract ids in the snapshot so the app can ask for live quotes. Numbers are copied, not recomputed,
except the London pence factor, which is measured from the data (snapshot last vs weekly close).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import data, evidence, monitor, notify

SPARK_WEEKS = 53
TOP_OPTIONS = 12


def _contracts(snapshot_dir: Path) -> dict:
    out = {}
    for f in sorted(snapshot_dir.glob("*.json")):
        if f.name == "options.json":
            continue
        for i in json.loads(f.read_text())["instruments"]:
            weekly = (i.get("weekly") or {}).get("close") or []
            snap_last = (i.get("snapshot") or {}).get("last")
            # London lines quote the live price in pence and weekly closes in pounds.
            factor = 100 if snap_last and weekly and snap_last / weekly[-1] > 50 else 1
            out[i["symbol"]] = {"id": i.get("contract_id"), "exchange": i.get("exchange"),
                                "sectype": i.get("security_type"), "currency": i.get("currency"),
                                "live_divisor": factor}
    return out


def build(reports_dir: str | Path, snapshot_dir: str | Path, universe: dict,
          state_dir: str | Path | None = None) -> dict:
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
                             "yield_after_cut": inc.get("yield_after_cut")}
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
                       "last": m["last"], "ret_1w": m["ret_1w"], "ret_3m": m["ret_3m"], "ret_1y": m.get("ret_1y"),
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
        "links": notify.LINKS,
    }
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
                                               "return_at_1sd", "scenarios")})
        out["options"] = {"ideas": ideas, "policy": opt["policy"]}
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
        }
    return out
