"""Command line: `global-research {monitor,portfolio,demo}`."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import data, monitor, portfolio, reports


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    raise TypeError(type(o))


def _clean(o):
    """Strict JSON: NaN/inf become null."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def load(args, universe):
    if args.snapshot:
        return data.load_ibkr_snapshot(args.snapshot, universe)
    if args.csv:
        prices = data.load_csv_prices(args.csv)
        meta = {i["symbol"]: {**i, "source": "CSV", "status": "ok", "currency": None, "snapshot": {}, "dividends": []}
                for i in universe["instruments"]}
        return prices, meta
    if args.yahoo:
        return data.fetch_yahoo(universe, period="1y", interval="1wk")
    raise SystemExit("choose a data source: --snapshot DIR, --csv DIR or --yahoo")


def synthetic(universe, weeks=60, seed=7):
    """Deterministic synthetic weekly prices for offline demos and tests. Clearly not real data."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end="2026-10-09", periods=weeks, freq="W-FRI")
    market = rng.normal(0.002, 0.02, weeks)
    cols, meta = {}, {}
    for k, inst in enumerate(universe["instruments"]):
        beta = 0.5 + (k % 7) / 5
        r = beta * market + rng.normal(0.001 * ((k % 5) - 2), 0.025, weeks)
        cols[inst["symbol"]] = 100 * np.cumprod(1 + r)
        divs = []
        if inst["group"] == "sg" and inst["kind"] == "stock":
            q = cols[inst["symbol"]][-1] * (0.04 + (k % 6) * 0.008) / 4
            divs = [{"ex_date": d.date().isoformat(), "amount": round(float(q), 4), "currency": "SGD", "type": "synthetic"}
                    for d in idx[-52::13]]
        meta[inst["symbol"]] = {**inst, "source": "SYNTHETIC demo data", "status": "ok", "snapshot": {}, "dividends": divs}
    return pd.DataFrame(cols, index=idx), meta


def write_reports(prices, meta, out: Path, state_dir: Path | None, events: str | None, standalone=True):
    out.mkdir(parents=True, exist_ok=True)
    mon = monitor.build_monitor(prices, meta, events)
    changes = []
    if state_dir:
        monitor.append_history(mon, state_dir)
        changes = monitor.regime_changes(state_dir)
    (out / "monitor.json").write_text(json.dumps(_clean(mon), indent=1, default=_json_default))
    (out / "monitor.html").write_text(reports.monitor_report(mon, meta, prices, changes, standalone), encoding="utf-8")
    (out / "us-tech.html").write_text(reports.us_tech_report(mon, meta, prices, standalone), encoding="utf-8")
    (out / "uk-fx.html").write_text(reports.uk_fx_report(mon, meta, prices, standalone), encoding="utf-8")
    (out / "sg-income.html").write_text(reports.sg_income_report(mon, meta, prices, standalone=standalone), encoding="utf-8")
    return mon


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="global-research")
    p.add_argument("--universe", default=str(data.DEFAULT_UNIVERSE))
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("monitor", "portfolio"):
        s = sub.add_parser(name)
        src = s.add_mutually_exclusive_group()
        src.add_argument("--snapshot", help="directory of IBKR snapshot JSON files")
        src.add_argument("--csv", help="directory of <symbol>.csv files (date,close)")
        src.add_argument("--yahoo", action="store_true", help="fetch live from Yahoo (needs network + yfinance)")
    m = sub.choices["monitor"]
    m.add_argument("--out", required=True)
    m.add_argument("--state-dir", help="directory holding history.jsonl across runs")
    m.add_argument("--events", help="CSV of macro events: date,event,category")
    pf = sub.choices["portfolio"]
    pf.add_argument("--symbols", nargs="+", required=True)
    pf.add_argument("--max-weight", type=float, default=0.4)
    pf.add_argument("--train-fraction", type=float, default=0.7)
    d = sub.add_parser("demo")
    d.add_argument("--out", required=True)
    args = p.parse_args(argv)
    universe = data.load_universe(args.universe)

    if args.cmd == "demo":
        prices, meta = synthetic(universe)
        mon = write_reports(prices, meta, Path(args.out), None, None)
        print(f"Synthetic demo written to {args.out} (as of {mon['as_of']}).")
        return 0
    prices, meta = load(args, universe)
    if args.cmd == "monitor":
        mon = write_reports(prices, meta, Path(args.out), Path(args.state_dir) if args.state_dir else None, args.events)
        dq = mon["data_quality"]
        print(f"Monitor as of {mon['as_of']}: {dq['with_prices']}/{dq['instruments_configured']} instruments; "
              f"missing {dq['missing'] or 'none'}; reports in {args.out}")
        return 0
    missing = [s for s in args.symbols if s not in prices]
    if missing:
        raise SystemExit(f"no prices for {missing}")
    ccy = {s: meta[s].get("currency") for s in args.symbols}
    unknown = [s for s, c in ccy.items() if not c]
    if unknown:
        raise SystemExit(f"quote currency unknown for {unknown}; use a source that records currency (e.g. --snapshot)")
    if len(set(ccy.values())) > 1:
        raise SystemExit(f"mixed currencies {ccy}: convert with portfolio.to_base_currency before allocating")
    from .analytics import infer_bars_per_year
    res = portfolio.backtest_min_variance(prices[args.symbols], args.train_fraction, args.max_weight,
                                          infer_bars_per_year(prices.index))
    print(json.dumps(_clean(res), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
