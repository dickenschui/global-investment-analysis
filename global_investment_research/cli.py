"""Command line: `global-research {monitor,portfolio,demo}`."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import alert_review, alert_state, alerts, app_data, backtest, brief, curve, data, evidence, decisions, desk, handbook, monitor, news, notify, options, options_desk, portfolio, reports


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


HISTORY_DIR = "data/history/weekly_long"   # long weekly history: curve readings and stress tests
MACRO_LATEST = "data/macro/latest.json"   # written by the macro-daily GitHub workflow


HOURLY_UTC = range(1, 22)      # hourly-alerts.yml runs 01:07-21:47 UTC on weekdays


def _missed_scheduled_run(last_iso: str | None, now_iso: str, margin_minutes: int = 45) -> bool:
    """True when the hourly schedule (weekdays, 01:00-21:00 UTC) should have run at least once between the last check
    and this one, allowing for the scheduler's usual delay. Overnight and weekend gaps are not outages."""
    if not last_iso:
        return False
    last, now = (alert_state._aware(x) for x in (last_iso, now_iso))
    margin = dt.timedelta(minutes=margin_minutes)
    t = last.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    while t < now - margin:
        if t > last + margin and t.weekday() < 5 and t.hour in HOURLY_UTC:
            return True
        t += dt.timedelta(hours=1)
    return False


def write_reports(prices, meta, out: Path, state_dir: Path | None, events: str | None, standalone=True,
                  chains_path: str | Path | None = None, history_dir: str | Path | None = None,
                  macro_path: str | Path | None = None):
    out.mkdir(parents=True, exist_ok=True)
    rates = curve.load_rates(history_dir) if history_dir else None
    macro = json.loads(Path(macro_path).read_text()) if macro_path and Path(macro_path).exists() else None
    mon = monitor.build_monitor(prices, meta, events, rates=rates, macro=macro)
    changes = []
    if state_dir:
        monitor.append_history(mon, state_dir)
        changes = monitor.regime_changes(state_dir)
    (out / "monitor.json").write_text(json.dumps(_clean(mon), indent=1, default=_json_default))
    (out / "monitor.html").write_text(reports.monitor_report(mon, meta, prices, changes, standalone), encoding="utf-8")
    (out / "us-tech.html").write_text(reports.us_tech_report(mon, meta, prices, standalone), encoding="utf-8")
    (out / "uk-fx.html").write_text(reports.uk_fx_report(mon, meta, prices, standalone), encoding="utf-8")
    (out / "sg-income.html").write_text(reports.sg_income_report(mon, meta, prices, standalone=standalone), encoding="utf-8")
    hist_path = Path("data/history/sg_distributions_5y.json")
    dist = ({i["symbol"]: i["corp_actions"] for i in json.loads(hist_path.read_text())["instruments"]}
            if hist_path.exists() else None)
    dec = decisions.build_decisions(mon, meta, prices=prices, dist_history=dist)
    if history_dir and Path(history_dir).exists():
        from . import stress
        hist, hmeta = data.load_ibkr_snapshot(history_dir, {"instruments": list(meta.values())})
        exc_path = Path(data.DEFAULT_UNIVERSE).parent / "data-exceptions.json"
        exc = json.loads(exc_path.read_text())["exceptions"] if exc_path.exists() else []
        dec["stress"] = stress.summary(dec["calls"], hist, hmeta, exc)
    conf = None
    if (out / "backtest.json").exists():
        conf = evidence.confidence_note(json.loads((out / "backtest.json").read_text()))
    (out / "decisions.json").write_text(json.dumps(_clean(dec), indent=1, default=_json_default))
    (out / "decision-desk.html").write_text(desk.desk_page(dec, mon, meta, standalone, conf), encoding="utf-8")
    (out / "handbook.html").write_text(handbook.to_html(standalone), encoding="utf-8")
    opt = None
    if chains_path and Path(chains_path).exists():
        opt = options.build_ideas(json.loads(Path(chains_path).read_text()), dec, mon["as_of"])
        (out / "options.json").write_text(json.dumps(_clean(opt), indent=1, default=_json_default))
        (out / "options.html").write_text(options_desk.options_page(opt, standalone), encoding="utf-8")
    (out / "brief.html").write_text(brief.brief_page(dec, mon, standalone, opt, conf), encoding="utf-8")
    try:
        from . import workbook
        workbook.build_workbook(dec, out / "decision-book.xlsx", opt=opt)
    except ImportError:
        print("openpyxl not installed: skipped decision-book.xlsx (pip install -e .[excel])")
    (out / "index.html").write_text(index_page(mon["as_of"], out), encoding="utf-8")
    dg = notify.digest(dec, mon, opt, changes, conf)
    (out / "email.html").write_text(notify.email_html(dg), encoding="utf-8")
    (out / "telegram.txt").write_text(notify.telegram_text(dg), encoding="utf-8")
    return mon


DELIVERABLES = [
    ("brief.html", "Weekly Investment Brief", "The call in one page: posture, actions, income plan, risks."),
    ("decision-desk.html", "Decision Desk", "Every instrument's decision with buy zone, exit level and position size."),
    ("decision-book.xlsx", "Decision Book (Excel)", "Editable limits drive sizes and limit prices; includes the decision log."),
    ("options.html", "Speculative Options Sleeve", "Defined-risk call/put ideas from real chains, capped at 5% of portfolio."),
    ("evidence.html", "Rule Evidence", "19-year walk-forward test of the decision rules (2008 to date)."),
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
    bt.add_argument("--history", required=True, help="directory of long weekly history JSON files")
    bt.add_argument("--out", required=True)
    bt.add_argument("--start", default="2007-01-01",
                    help="ignore history before this date (default 2007-01-01: before it only a few instruments exist "
                         "and several London series change quote unit)")
    wl = sub.add_parser("watchlist", help="symbols the hourly pulse must quote, from this week's reports")
    wl.add_argument("--reports", required=True)
    pu = sub.add_parser("pulse", help="quote this week's watch list from Yahoo Finance (for the hourly check)")
    pu.add_argument("--yahoo", action="store_true", required=True, help="quote source (the only one available outside Claude)")
    pu.add_argument("--reports", required=True)
    pu.add_argument("--out", required=True)
    al = sub.add_parser("alerts", help="evaluate a live pulse against this week's levels; print new exceptions")
    al.add_argument("--pulse", required=True)
    al.add_argument("--reports", required=True)
    al.add_argument("--state-dir", default="data/state")
    al.add_argument("--max-age-minutes", type=float, default=alerts.MAX_QUOTE_AGE_MIN,
                    help="quotes older than this are set aside as stale")
    al.add_argument("--news", action="store_true", help="attach official headlines (config/news-sources.json) to URGENT alerts")
    al.add_argument("--telegram", action="store_true", help="also send new WARN/URGENT exceptions to Telegram")
    al.add_argument("--email", action="store_true",
                    help="also email new WARN/URGENT exceptions (MAIL_USERNAME, MAIL_APP_PASSWORD, MAIL_TO secrets)")
    mc = sub.add_parser("macro", help="fetch official macro series (FRED, ECB, BoE, ONS, Treasury, World Bank)")
    mc.add_argument("--config", default="config/macro-sources.json")
    mc.add_argument("--out", default="data/macro")
    nt = sub.add_parser("notify", help="send the week's telegram.txt via the Telegram Bot API")
    nt.add_argument("--reports", required=True, help="reports/<date> directory produced by monitor")
    ad = sub.add_parser("app-data", help="write the Investment Desk app's data file from a week's reports")
    ad.add_argument("--reports", required=True, help="reports/<date> directory (after backtest and monitor)")
    ad.add_argument("--snapshot", required=True, help="data/snapshots/<date> directory (for IBKR contract ids)")
    ad.add_argument("--state-dir", default=None)
    ad.add_argument("--out", required=True)
    mf = sub.add_parser("manifest", help="write (or --verify) SHA-256 manifests for snapshot/history directories")
    mf.add_argument("dirs", nargs="+")
    mf.add_argument("--verify", action="store_true")
    qa = sub.add_parser("quality", help="weekly data-quality review: coverage, staleness, cross-checks, identity, manifest")
    qa.add_argument("--snapshot", required=True)
    qa.add_argument("--previous", help="last week's snapshot directory, for contract and dividend changes")
    qa.add_argument("--out", help="directory for quality.json and quality.md")
    ar = sub.add_parser("alert-review", help="weekly scorecard: which alerts were verified at the close and acted "
                                             "on, duplicates, and coverage gaps ranked")
    ar.add_argument("--reports", required=True, help="the reports/<date> directory whose alerts/ the week produced")
    ar.add_argument("--snapshot", required=True, help="the next weekly snapshot, for the official week-end closes")
    ar.add_argument("--decision-log", default="config/decision-log.csv")
    ar.add_argument("--out", help="directory for alert-review.json and alert-review.md")
    hb = sub.add_parser("handbook", help="write docs/HANDBOOK.md from the policy source")
    hb.add_argument("--out", default="docs/HANDBOOK.md")
    args = p.parse_args(argv)
    universe = data.load_universe(args.universe)

    if args.cmd == "macro":
        from . import macro_sources
        res = macro_sources.run(args.config, args.out)
        print(json.dumps(_clean(res), indent=1, default=_json_default))
        return 0 if res["ok"] - res.get("carried_forward", 0) else 1   # fail when nothing was fetched fresh
    if args.cmd == "manifest":
        from . import integrity
        bad = False
        for d in args.dirs:
            if args.verify:
                problems = integrity.verify_manifest(d)
                print(f"{d}: " + ("OK" if not problems else "; ".join(problems)))
                bad |= bool(problems)
            else:
                path = integrity.write_manifest(d)
                print(f"{path}: {len(json.loads(path.read_text())['files'])} files")
        return 1 if bad else 0
    if args.cmd == "quality":
        from . import quality
        rev = quality.review(args.snapshot, args.previous, universe)
        md = quality.to_markdown(rev)
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "quality.json").write_text(json.dumps(_clean(rev), indent=1, default=_json_default))
            (out / "quality.md").write_text(md + "\n", encoding="utf-8")
        print(md)
        return 1 if rev["manifest"]["status"] == "failed" or rev["identity"] else 0
    if args.cmd == "alert-review":
        rep = Path(args.reports)
        dec = json.loads((rep / "decisions.json").read_text())
        prices, _ = data.load_ibkr_snapshot(args.snapshot, universe)
        end = alert_review.week_end(dec["as_of"])
        row = prices.loc[prices.index.normalize() == pd.Timestamp(end)]
        closes = {} if row.empty else {k: float(v) for k, v in row.iloc[-1].dropna().items()}
        rev = alert_review.review(alert_review.load_runs(rep / "alerts"), dec, closes,
                                  decision_log=alert_review.load_decision_log(args.decision_log),
                                  structural=alert_review.structural_gaps(Path(args.universe).parent))
        md = alert_review.to_markdown(rev)
        if not closes:
            md += f"\n\nNo official close dated {end} in {args.snapshot}: conditions are unscored."
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "alert-review.json").write_text(json.dumps(_clean(rev), indent=1, default=_json_default))
            (out / "alert-review.md").write_text(md + "\n", encoding="utf-8")
        print(md)
        return 0
    if args.cmd == "app-data":
        res = app_data.build(args.reports, args.snapshot, universe, args.state_dir)
        Path(args.out).write_text(json.dumps(_clean(res), separators=(",", ":"), default=_json_default))
        print(f"App data {res['as_of']}: {len(res['calls'])} decisions, {len(res['others'])} other instruments, "
              f"{Path(args.out).stat().st_size / 1024:.0f} KB -> {args.out}")
        return 0

    if args.cmd == "backtest":
        prices, _ = data.load_ibkr_snapshot(args.history, universe)
        if args.start:
            prices = prices[prices.index >= pd.Timestamp(args.start)]
        rated = {i["symbol"] for i in universe["instruments"] if i["kind"] not in ("fx", "macro_proxy")}
        funds = {i["symbol"] for i in universe["instruments"] if i["kind"] != "stock"}
        cash = data.load_cash_yield(args.history)
        exc_path = Path(args.universe).parent / "data-exceptions.json"
        exceptions = json.loads(exc_path.read_text())["exceptions"] if exc_path.exists() else []
        res = backtest.run(prices[[c for c in prices.columns if c in rated]], cash=cash, funds=funds, exceptions=exceptions)
        res["universe_note"] = (f"{len(res['instruments'])} of the {len(rated)} instruments the Desk rates"
                                + ("; cash earns the 13-week T-bill yield" if cash is not None else "; cash earns 0%"))
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "backtest.json").write_text(json.dumps(_clean(res), indent=1, default=_json_default))
        (out / "evidence.html").write_text(evidence.evidence_page(res), encoding="utf-8")
        v = res["verdict"]
        print(f"Backtest {res['first_date']}..{res['last_date']}: {len(res['instruments'])} instruments; "
              f"supported both halves={v['supported_both_halves']}, significant={v['significant']}")
        return 0
    if args.cmd in ("watchlist", "alerts", "pulse"):
        rep = Path(args.reports)
        dec = json.loads((rep / "decisions.json").read_text())
        mon = json.loads((rep / "monitor.json").read_text())
        opt = json.loads((rep / "options.json").read_text()) if (rep / "options.json").exists() else None
        if args.cmd == "watchlist":
            kinds = {i["symbol"]: i["kind"] for i in universe["instruments"]}
            print(json.dumps(alerts.watch_list(dec, opt, kinds)))
            return 0
        kinds = {i["symbol"]: i["kind"] for i in universe["instruments"]}
        if args.cmd == "pulse":
            from . import pulse_yahoo
            pulse = pulse_yahoo.build_pulse(alerts.watch_list(dec, opt, kinds), universe)
            Path(args.out).write_text(json.dumps(pulse, indent=1))
            print(f"Pulse {pulse['retrieved_at_utc']}: {len(pulse['quotes'])} quotes, missing {pulse['missing'] or 'none'}")
            return 0
        pulse = json.loads(Path(args.pulse).read_text())
        now = pulse["retrieved_at_utc"]
        found = alerts.evaluate(pulse, dec, mon, opt, kinds, max_age_minutes=args.max_age_minutes)
        usable, frozen = alerts.normalise_quotes(pulse, mon, max_age_minutes=args.max_age_minutes)
        state = Path(args.state_dir) / "alert_state.json"
        hb = alert_state.heartbeat_gap(state, now, weekday=_missed_scheduled_run(alert_state.load(state).get("last_run_utc"), now))
        found += [a for a in (hb, alert_state.levels_stale(dec.get("as_of"), now)) if a]
        # only quoted symbols can clear a condition; repeats within the desk week (or day, for moves) stay quiet
        res = alert_state.update(found, set(usable), state, now, levels_as_of=dec.get("as_of"))
        to_send = res["escalated"] + res["new"] + res["pending"]
        failures = []
        news_fetched = bool(args.news and any(a["severity"] in news.CONTEXT_SEVERITIES for a in to_send))
        if news_fetched:
            sources = json.loads((Path(args.universe).parent / "news-sources.json").read_text())["sources"]
            items, failures = news.fetch(sources)
            to_send = news.headline_context(to_send, items, {i["symbol"]: i["name"] for i in universe["instruments"]},
                                            now_iso=now)
        watched = set(alerts.watch_list(dec, opt, kinds))
        not_live = sorted(set(frozen) & watched)
        no_quote = sorted(watched - set(usable) - set(frozen))  # nothing usable from the vendor: a coverage gap
        text = alerts.alert_text(to_send, now, not_live, no_quote)
        out = rep / "alerts"
        out.mkdir(exist_ok=True)
        stamp = now.replace(":", "").replace("-", "")[:13]
        record = {"retrieved_at_utc": now, "levels_as_of": dec.get("as_of"), "all": found, "sent": to_send,
                  "repeats": res["repeats"], "cleared": res["cleared"], "missing_quotes": sorted(set(not_live) | set(no_quote)),
                  "not_live": not_live, "no_quote": no_quote,
                  "no_quote_reasons": {s: (pulse.get("missing_reasons") or {}).get(s) for s in no_quote},
                  "news_fetched": news_fetched, "news_failures": failures, "pulse_source": pulse.get("source")}
        print(text if to_send else f"No new or escalated exceptions ({len(found)} conditions active, "
                                   f"{len(res['cleared'])} cleared, {len(res['repeats'])} repeats not re-sent).")
        # Without a channel flag the printed text is the delivery (the Claude routine relays it). With channels, a
        # notice counts as delivered only once a channel confirms; otherwise it stays pending and is offered again.
        delivered, pushed = True, False
        material = any(a["severity"] in ("URGENT", "WARN") for a in to_send)
        if args.telegram and material:
            try:
                notify.send_telegram(text)
                pushed = True
                print("Telegram alert sent.")
            except Exception as exc:  # delivery failure must not hide the alert itself
                delivered = False
                print(f"Telegram not sent: {exc}")
        if args.email and material:
            from . import mailer
            urgent = sum(a["severity"] == "URGENT" for a in to_send)
            subject = f"{'URGENT: ' if urgent else ''}Market exceptions {now[:16].replace('T', ' ')}Z ({len(to_send)})"
            body = "<div style='font-family:sans-serif;font-size:14px'>" + text.replace("\n", "<br>\n") + "</div>"
            try:
                r = mailer.send_if_configured(subject, body, re.sub(r"<[^>]+>", "", text),
                                              ledger=Path(args.state_dir) / "mail_ledger.json", day=now[:10])
                pushed |= r["status"] in ("sent", "already sent")
                print(r.get("reason") or f"Email {r['status']}.")
            except Exception as exc:  # MailError carries no secret
                delivered = False
                print(f"Email not sent: {exc}")
        if delivered and to_send:
            alert_state.mark_delivered([a["key"] for a in to_send], state)
        # Without channel flags the printed text is the delivery (a relay); with them, only a confirmed send pushes.
        # INFO-only runs are logged, not pushed: they wait for the weekly close.
        relay = not (args.telegram or args.email)
        record["pushed"] = [a["key"] for a in to_send] if (relay and delivered) or pushed else []
        (out / f"alerts-{stamp}.json").write_text(json.dumps(record, indent=1))
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
                            chains_path=chains, history_dir=HISTORY_DIR, macro_path=MACRO_LATEST)
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
