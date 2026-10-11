"""Command line: `global-research {monitor,portfolio,demo}`."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import alerts, backtest, brief, data, evidence, decisions, desk, handbook, monitor, notify, options, options_desk, portfolio, reports


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


def write_reports(prices, meta, out: Path, state_dir: Path | None, events: str | None, standalone=True,
                  chains_path: str | Path | None = None):
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
    dec = decisions.build_decisions(mon, meta)
    (out / "decisions.json").write_text(json.dumps(_clean(dec), indent=1, default=_json_default))
    (out / "decision-desk.html").write_text(desk.desk_page(dec, mon, meta, standalone), encoding="utf-8")
    (out / "handbook.html").write_text(handbook.to_html(standalone), encoding="utf-8")
    opt = None
    if chains_path and Path(chains_path).exists():
        opt = options.build_ideas(json.loads(Path(chains_path).read_text()), dec, mon["as_of"])
        (out / "options.json").write_text(json.dumps(_clean(opt), indent=1, default=_json_default))
        (out / "options.html").write_text(options_desk.options_page(opt, standalone), encoding="utf-8")
    (out / "brief.html").write_text(brief.brief_page(dec, mon, standalone, opt), encoding="utf-8")
    try:
        from . import workbook
        workbook.build_workbook(dec, out / "decision-book.xlsx", opt=opt)
    except ImportError:
        print("openpyxl not installed: skipped decision-book.xlsx (pip install -e .[excel])")
    (out / "index.html").write_text(index_page(mon["as_of"], out), encoding="utf-8")
    dg = notify.digest(dec, mon, opt, changes)
    (out / "email.html").write_text(notify.email_html(dg), encoding="utf-8")
    (out / "telegram.txt").write_text(notify.telegram_text(dg), encoding="utf-8")
    return mon


DELIVERABLES = [
    ("brief.html", "Weekly Investment Brief", "The call in one page: posture, actions, income plan, risks."),
    ("decision-desk.html", "Decision Desk", "Every instrument's decision with buy zone, exit level and position size."),
    ("decision-book.xlsx", "Decision Book (Excel)", "Editable limits drive sizes and limit prices; includes the decision log."),
    ("options.html", "Speculative Options Sleeve", "Defined-risk call/put ideas from real chains, capped at 5% of portfolio."),
    ("evidence.html", "Rule Evidence", "Five-year walk-forward test of the decision rules."),
    ("handbook.html", "Investment Handbook", "The routine, rules, risk limits and review loop."),
    ("monitor.html", "Global Markets Monitor", "Markets, macro readings, factor links, sectors, all instruments."),
    ("us-tech.html", "US Tech Watchlist", "Large-cap tech ranked by risk-adjusted momentum."),
    ("uk-fx.html", "UK Equities and FX", "UK large caps by sector and nine FX pairs."),
    ("sg-income.html", "Singapore Income Watchlist", "REITs and trusts against the 6–8% yield target."),
]


def index_page(as_of: str, out: Path) -> str:
    from . import html as H
    items = "".join(
        f'<li><a href="{f}">{H.esc(t)}</a> <span class="mut">· {H.esc(d)}</span></li>'
        for f, t, d in DELIVERABLES if (out / f).exists() or (f.endswith(".html") and f not in ("options.html", "evidence.html")))
    return H.page("Global Investment Pack", f"Week to {as_of}",
                  "Start with the Brief, act from the Desk, follow the Handbook.",
                  H.section("Deliverables", f'<ul class="notes">{items}</ul>'),
                  "Generated by global-research from IBKR weekly closes.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="global-research")
    p.add_argument("--universe", default=str(data.DEFAULT_UNIVERSE))
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("monitor", "portfolio", "review"):
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
    sub.choices["review"].add_argument("--log", default="config/decision-log.csv")
    d = sub.add_parser("demo")
    d.add_argument("--out", required=True)
    bt = sub.add_parser("backtest", help="walk-forward test of the decision rules on long weekly history")
    bt.add_argument("--history", required=True, help="directory of 5-year weekly JSON files")
    bt.add_argument("--out", required=True)
    wl = sub.add_parser("watchlist", help="symbols the hourly pulse must quote, from this week's reports")
    wl.add_argument("--reports", required=True)
    al = sub.add_parser("alerts", help="evaluate a live pulse against this week's levels; print new exceptions")
    al.add_argument("--pulse", required=True)
    al.add_argument("--reports", required=True)
    al.add_argument("--state-dir", default="data/state")
    al.add_argument("--telegram", action="store_true", help="also send new WARN/URGENT exceptions to Telegram")
    nt = sub.add_parser("notify", help="send the week's telegram.txt via the Telegram Bot API")
    nt.add_argument("--reports", required=True, help="reports/<date> directory produced by monitor")
    hb = sub.add_parser("handbook", help="write docs/HANDBOOK.md from the policy source")
    hb.add_argument("--out", default="docs/HANDBOOK.md")
    args = p.parse_args(argv)
    universe = data.load_universe(args.universe)

    if args.cmd == "backtest":
        prices, _ = data.load_ibkr_snapshot(args.history, universe)
        res = backtest.run(prices)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "backtest.json").write_text(json.dumps(_clean(res), indent=1, default=_json_default))
        (out / "evidence.html").write_text(evidence.evidence_page(res), encoding="utf-8")
        v = res["verdict"]
        print(f"Backtest {res['first_date']}..{res['last_date']}: {len(res['instruments'])} instruments; "
              f"supported both halves={v['supported_both_halves']}, significant={v['significant']}")
        return 0
    if args.cmd in ("watchlist", "alerts"):
        rep = Path(args.reports)
        dec = json.loads((rep / "decisions.json").read_text())
        mon = json.loads((rep / "monitor.json").read_text())
        opt = json.loads((rep / "options.json").read_text()) if (rep / "options.json").exists() else None
        if args.cmd == "watchlist":
            kinds = {i["symbol"]: i["kind"] for i in universe["instruments"]}
            print(json.dumps(alerts.watch_list(dec, opt, kinds)))
            return 0
        pulse = json.loads(Path(args.pulse).read_text())
        found = alerts.evaluate(pulse, dec, mon, opt)
        fresh = alerts.new_since_last(found, args.state_dir, pulse["retrieved_at_utc"][:10])
        quoted = {x["symbol"] for x in pulse["quotes"] if x.get("last")}
        kinds = {i["symbol"]: i["kind"] for i in universe["instruments"]}
        stale = sorted(set(alerts.watch_list(dec, opt, kinds)) - quoted)
        text = alerts.alert_text(fresh, pulse["retrieved_at_utc"], stale)
        out = rep / "alerts"
        out.mkdir(exist_ok=True)
        stamp = pulse["retrieved_at_utc"].replace(":", "").replace("-", "")[:13]
        (out / f"alerts-{stamp}.json").write_text(json.dumps({"all": found, "new": fresh, "missing_quotes": stale}, indent=1))
        print(text if fresh else f"No new exceptions ({len(found)} already reported today).")
        if args.telegram and any(a["severity"] in ("URGENT", "WARN") for a in fresh):
            try:
                notify.send_telegram(text)
                print("Telegram alert sent.")
            except Exception as exc:  # delivery failure must not hide the alert itself
                print(f"Telegram not sent: {exc}")
        return 0
    if args.cmd == "notify":
        import os
        if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
            print("Telegram skipped: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not set.")
            return 0
        text = (Path(args.reports) / "telegram.txt").read_text(encoding="utf-8")
        notify.send_telegram(text)
        print("Telegram message sent.")
        return 0
    if args.cmd == "handbook":
        Path(args.out).write_text(handbook.to_markdown() + "\n", encoding="utf-8")
        print(f"Handbook written to {args.out}")
        return 0

    if args.cmd == "demo":
        prices, meta = synthetic(universe)
        mon = write_reports(prices, meta, Path(args.out), None, None)
        print(f"Synthetic demo written to {args.out} (as of {mon['as_of']}).")
        return 0
    prices, meta = load(args, universe)
    if args.cmd == "monitor":
        chains = Path(args.snapshot) / "options.json" if args.snapshot else None
        mon = write_reports(prices, meta, Path(args.out), Path(args.state_dir) if args.state_dir else None, args.events,
                            chains_path=chains)
        dq = mon["data_quality"]
        print(f"Monitor as of {mon['as_of']}: {dq['with_prices']}/{dq['instruments_configured']} instruments; "
              f"missing {dq['missing'] or 'none'}; reports in {args.out}")
        return 0
    if args.cmd == "review":
        rows = decisions.review_log(args.log, prices)
        if not rows:
            print(f"No decisions logged in {args.log} yet.")
        for r in rows:
            if "return" in r:
                print(f"{r['date']} {r['symbol']:8} {r['decision']:10} {r['return']*100:+6.1f}%  {r['verdict']}")
            else:
                print(f"{r['date']} {r['symbol']:8} {r['status']}")
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
