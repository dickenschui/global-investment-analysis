"""Weekly alert scorecard: which hourly alerts led to useful action, and where the monitor was blind.

An hourly alert is a claim made on a delayed vendor quote (Yahoo Finance). It is verified or refuted at the next
official weekly close, which is the point at which the Handbook acts:
- level rules ("above" / "below" / "band"): verified if the week-end close is still past the level;
- one-day moves ("move"): verified if the week-end close kept at least half of the move from the prior close
  (HELD_SHARE); otherwise the move reversed.
A condition counts as acted on only when config/decision-log.csv records a decision on the symbol within
ACT_WINDOW_DAYS of the first notice. Monitor-health notices are counted as system notices, not market calls.

Inputs are the week's alert runs (reports/<date>/alerts/alerts-*.json, kept on the alert-state branch), that week's
decisions.json and the official closes for the week end (the next weekly snapshot). Only runs inside the desk week
are scored; runs after it mean the next week's levels never landed. Nothing is estimated: a condition without a
close or a stored reference is reported as unscored.
"""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from .alert_state import HEARTBEAT_SYMBOL, RANK

HELD_SHARE = 0.5
ACT_WINDOW_DAYS = 7
SLOTS_PER_WEEKDAY = 63      # hourly-alerts.yml: minutes 7, 27, 47 of hours 01-21 UTC, Monday to Friday
OBSERVED_TARGET = 0.9       # below this share of scheduled checks the monitor counts as blind
NEWS_FAIL_LIMIT = 0.5
LEVEL_FALLBACK = {"Revisit trigger crossed": ("trigger", "above"), "Exit level breached": ("stop", "below")}


def _at(run: dict, name: str) -> str:
    """The run's retrieval time: recorded in newer runs, else read from the file name (alerts-YYYYMMDDTHHMM)."""
    if run.get("retrieved_at_utc"):
        return run["retrieved_at_utc"]
    s = name.removeprefix("alerts-").removesuffix(".json")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:00Z"


def load_runs(alerts_dir: str | Path) -> list[dict]:
    """Every alert run in a reports/<date>/alerts directory, oldest first, each with its time under "at"."""
    runs = []
    for f in sorted(Path(alerts_dir).glob("alerts-*.json")):
        r = json.loads(f.read_text())
        runs.append({**r, "at": _at(r, f.name)})
    return sorted(runs, key=lambda r: r["at"])


def load_decision_log(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(fh) if row.get("symbol") and row.get("date")]


def week_end(levels_as_of: str) -> date:
    """Levels dated Friday D are monitored through the following Friday, D + 7."""
    return date.fromisoformat(levels_as_of[:10]) + timedelta(days=7)


def outcome(alert: dict, close: float | None, levels: dict | None = None) -> str:
    """'verified', 'refuted' or 'unscored' for one alert against the official week-end close."""
    kind, ref, obs = alert.get("kind"), alert.get("reference"), alert.get("observed")
    if kind is None and alert["rule"] in LEVEL_FALLBACK and levels:  # runs written before alerts stored references
        field, kind = LEVEL_FALLBACK[alert["rule"]]
        ref = levels.get(field)
    if close is None or ref is None or kind is None:
        return "unscored"
    if kind == "above":
        return "verified" if close > ref else "refuted"
    if kind == "below":
        return "verified" if close < ref else "refuted"
    if kind == "band":
        return "verified" if not (ref[0] <= close <= ref[1]) else "refuted"
    if kind == "move":
        if obs is None or obs == ref:
            return "unscored"
        return "verified" if (close - ref) / (obs - ref) >= HELD_SHARE else "refuted"
    return "unscored"


def _weekdays(start: date, end: date) -> int:
    return sum((start + timedelta(days=i)).weekday() < 5 for i in range((end - start).days + 1))


def review(runs: list[dict], dec: dict, closes: dict[str, float], *, decision_log=(), structural=()) -> dict:
    """Scorecard for one desk week. `closes` = {symbol: official week-end close}; `structural` = known source gaps
    from config ({"id", "reason"}), listed last in the gap ranking."""
    as_of = dec["as_of"]
    end = week_end(as_of)
    start = date.fromisoformat(as_of[:10]) + timedelta(days=1)
    late = [r for r in runs if date.fromisoformat(r["at"][:10]) > end]  # checks still on these levels after the week
    runs = [r for r in runs if start <= date.fromisoformat(r["at"][:10]) <= end]
    levels = {c["symbol"]: c for c in dec.get("calls", [])}
    acted_on = {}
    for row in decision_log:
        acted_on.setdefault(row["symbol"], []).append(date.fromisoformat(row["date"][:10]))

    conds: dict[str, dict] = {}
    for r in runs:
        pushed = set(r["pushed"]) if "pushed" in r else None
        material = any(a["severity"] in ("URGENT", "WARN") for a in r.get("sent", []))
        for a in r.get("sent", []):
            k = a.get("key") or f"{a['symbol']}|{a['rule']}"
            c = conds.setdefault(k, {"key": k, "symbol": a["symbol"], "rule": a["rule"], "severity": a["severity"],
                                     "first_notice": r["at"], "notices": set(), "pushed": False,
                                     "pushed_inferred": False, "context": False, "alert": a})
            c["notices"].add(a.get("first_seen") or r["at"])  # a retry of an undelivered notice is not a new one
            if RANK[a["severity"]] > RANK[c["severity"]]:
                c["severity"], c["alert"] = a["severity"], a
            c["context"] |= bool(a.get("context"))
            if pushed is not None:
                c["pushed"] |= k in pushed
            elif material:  # older runs: channels were configured, so a run with WARN/URGENT was pushed
                c["pushed"] = c["pushed_inferred"] = True
        for a in r.get("repeats", []):
            k = a.get("key") or f"{a['symbol']}|{a['rule']}"
            if k in conds:
                conds[k].setdefault("repeats_suppressed", 0)
                conds[k]["repeats_suppressed"] += 1

    rows = []
    for c in conds.values():
        first = date.fromisoformat(c["first_notice"][:10])
        acted = any(first <= d <= first + timedelta(days=ACT_WINDOW_DAYS) for d in acted_on.get(c["symbol"], []))
        if c["symbol"] == HEARTBEAT_SYMBOL:
            res, use = "system", "system"
        else:
            res = outcome(c["alert"], closes.get(c["symbol"]), levels.get(c["symbol"]))
            use = "acted" if acted else {"verified": "verified, not acted", "refuted": "noise",
                                         "unscored": "unscored"}[res]
        rows.append({"key": c["key"], "symbol": c["symbol"], "rule": c["rule"], "severity": c["severity"],
                     "first_notice": c["first_notice"], "notices": len(c["notices"]),
                     "duplicates": len(c["notices"]) - 1, "repeats_suppressed": c.get("repeats_suppressed", 0),
                     "pushed": c["pushed"], "pushed_inferred": c["pushed_inferred"], "headline_context": c["context"],
                     "observed": c["alert"].get("observed"), "reference": c["alert"].get("reference"),
                     "close": closes.get(c["symbol"]), "outcome": res, "usefulness": use})
    rows.sort(key=lambda x: (-RANK[x["severity"]], x["rule"], x["symbol"]))

    by_rule: dict[str, dict] = {}
    for x in rows:
        b = by_rule.setdefault(x["rule"], {"conditions": 0, "notices": 0, "duplicates": 0, "pushed": 0,
                                           "verified": 0, "refuted": 0, "unscored": 0, "acted": 0})
        b["conditions"] += 1
        b["notices"] += x["notices"]
        b["duplicates"] += x["duplicates"]
        b["pushed"] += x["pushed"]
        b["acted"] += x["usefulness"] == "acted"
        if x["outcome"] in ("verified", "refuted", "unscored"):
            b[x["outcome"]] += 1
    for b in by_rule.values():
        scored = b["verified"] + b["refuted"]
        b["verified_rate"] = round(b["verified"] / scored, 2) if scored else None

    market = [x for x in rows if x["symbol"] != HEARTBEAT_SYMBOL]
    totals = {"conditions": len(market), "notices": sum(x["notices"] for x in market),
              "duplicates": sum(x["duplicates"] for x in market),
              "verified": sum(x["outcome"] == "verified" for x in market),
              "refuted": sum(x["outcome"] == "refuted" for x in market),
              "acted": sum(x["usefulness"] == "acted" for x in market),
              "pushed": sum(x["pushed"] for x in market),
              "system_notices": sum(x["notices"] for x in rows if x["symbol"] == HEARTBEAT_SYMBOL),
              "decision_log_entries": len(decision_log)}
    coverage = _coverage(runs, start, end, market)
    coverage["checks_after_week_end"] = len(late)
    return {"levels_as_of": as_of, "week_end": end.isoformat(), "conditions": rows, "by_rule": by_rule,
            "totals": totals, "coverage": coverage, "gaps": _gaps(coverage, structural)}


def _coverage(runs: list[dict], start: date, end: date, market: list[dict]) -> dict:
    week = [r for r in runs if date.fromisoformat(r["at"][:10]).weekday() < 5]
    slots = SLOTS_PER_WEEKDAY * _weekdays(start, end)
    no_quote: dict[str, int] = {}
    recorded = [r for r in runs if "no_quote" in r]
    for r in recorded:
        for s in r["no_quote"]:
            no_quote[s] = no_quote.get(s, 0) + 1
    attempts, fails = 0, {}
    for r in runs:
        fetched = r["news_fetched"] if "news_fetched" in r else bool(r.get("sent"))
        attempts += bool(fetched)
        for f in r.get("news_failures", []):
            src = fails.setdefault(f["source"], {"failures": 0, "reasons": []})
            src["failures"] += 1
            if f.get("reason") and f["reason"] not in src["reasons"]:
                src["reasons"].append(f["reason"])
    review_now = [x for x in market if x["severity"] in ("URGENT", "WARN")]
    return {"runs": len(runs), "runs_in_week": len(week), "scheduled_slots": slots,
            "observed_share": round(len(week) / slots, 3) if slots else None,
            "runs_recording_quote_gaps": len(recorded), "no_quote_runs_by_symbol": no_quote,
            "news_attempts": attempts,
            "news_failures": {k: {**v, "rate": round(v["failures"] / attempts, 2) if attempts else None}
                              for k, v in sorted(fails.items())},
            "review_alerts": len(review_now),
            "review_alerts_with_headline": sum(x["headline_context"] for x in review_now)}


def _gaps(cov: dict, structural) -> list[dict]:
    """Coverage gaps, most blinding first: tier 1 = the monitor could not see (no run, stale levels, no quote);
    tier 2 = it saw but had no context (news); tier 3 = markets with no official source configured. Within a tier,
    gaps that blind every line ("all") rank above gaps on one line or one feed ("one"), then by measured share."""
    out = []
    if cov["observed_share"] is not None and cov["observed_share"] < OBSERVED_TARGET:
        out.append({"tier": 1, "scope": "all", "gap": "Scheduled checks did not run", "share": round(1 - cov["observed_share"], 3),
                    "measure": f"{cov['runs_in_week']} of {cov['scheduled_slots']} scheduled checks ran",
                    "fix": "Start hourly-alerts.yml from an external scheduler (workflow_dispatch); GitHub's own "
                           "schedule drops most runs on this repository."})
    if cov.get("checks_after_week_end"):
        out.append({"tier": 1, "scope": "all", "gap": "Weekly levels stale", "share": 1.0,
                    "measure": f"{cov['checks_after_week_end']} checks after the week end still used these levels",
                    "fix": "Run the weekly refresh (the routine needs the repository attached) and merge its PR."})
    for sym, n in sorted(cov["no_quote_runs_by_symbol"].items(), key=lambda kv: -kv[1]):
        share = n / cov["runs_recording_quote_gaps"]
        out.append({"tier": 1, "scope": "one", "gap": f"No vendor quote for {sym}", "share": round(share, 3),
                    "measure": f"missing in {n} of {cov['runs_recording_quote_gaps']} checks",
                    "fix": "Check the Yahoo ticker in config/universe.json for this line."})
    for src, f in cov["news_failures"].items():
        if f["rate"] is not None and f["rate"] >= NEWS_FAIL_LIMIT:
            out.append({"tier": 2, "scope": "one", "gap": f"News feed {src} failing", "share": min(f["rate"], 1.0),
                        "measure": f"{f['failures']} failures in {cov['news_attempts']} fetches ({'; '.join(f['reasons'])})",
                        "fix": "Fix or replace the feed URL in config/news-sources.json; the reason names the HTTP status."})
    if cov["review_alerts"]:
        missing = cov["review_alerts"] - cov["review_alerts_with_headline"]
        if missing / cov["review_alerts"] >= 0.5:
            out.append({"tier": 2, "scope": "all", "gap": "Review alerts without headline context", "share": round(missing / cov["review_alerts"], 3),
                        "measure": f"{missing} of {cov['review_alerts']} WARN/URGENT conditions had no matching headline",
                        "fix": "Add official feeds or market words for the affected lines (news.MARKET_WORDS)."})
    for s in structural:
        out.append({"tier": 3, "scope": "one", "gap": f"No official source: {s['id']}", "share": None, "measure": "not configured",
                    "fix": s.get("reason", "")})
    return sorted(out, key=lambda g: (g["tier"], g["scope"] != "all", -(g["share"] or 0)))


def to_markdown(rev: dict) -> str:
    t, cov = rev["totals"], rev["coverage"]
    lines = [f"# Alert review · levels {rev['levels_as_of']}, week to {rev['week_end']}", "",
             f"{t['conditions']} market conditions, {t['notices']} notices ({t['duplicates']} duplicates), "
             f"{t['pushed']} pushed to Telegram/email. At the official close {t['verified']} were verified and "
             f"{t['refuted']} refuted. Decisions logged within {ACT_WINDOW_DAYS} days: {t['acted']} "
             f"(decision log has {t['decision_log_entries']} entries). System notices: {t['system_notices']}.", "",
             "## By rule", "", "| Rule | Conditions | Notices | Duplicates | Pushed | Verified | Refuted | Unscored | Acted | Verified rate |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for rule, b in sorted(rev["by_rule"].items()):
        rate = "n/a" if b["verified_rate"] is None else f"{b['verified_rate']:.0%}"
        lines.append(f"| {rule} | {b['conditions']} | {b['notices']} | {b['duplicates']} | {b['pushed']} | {b['verified']} "
                     f"| {b['refuted']} | {b['unscored']} | {b['acted']} | {rate} |")
    lines += ["", "## Conditions", "", "| Symbol | Rule | Severity | First notice | Notices | Pushed | Quote | Level or prior | Close | Outcome | Usefulness |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    num = lambda v: "" if v is None else (", ".join(f"{x:.10g}" for x in v) if isinstance(v, list) else f"{v:.10g}")
    for x in rev["conditions"]:
        pushed = ("yes" if x["pushed"] else "no") + (" (inferred)" if x["pushed_inferred"] else "")
        lines.append(f"| {x['symbol']} | {x['rule']} | {x['severity']} | {x['first_notice'][:16].replace('T', ' ')} | "
                     f"{x['notices']} | {pushed} | {num(x['observed'])} | {num(x['reference'])} | {num(x['close'])} | "
                     f"{x['outcome']} | {x['usefulness']} |")
    lines += ["", "## Coverage gaps, highest priority first", ""]
    if not rev["gaps"]:
        lines.append("None found.")
    for i, g in enumerate(rev["gaps"], 1):
        lines.append(f"{i}. **{g['gap']}** (tier {g['tier']}): {g['measure']}. Fix: {g['fix']}")
    return "\n".join(lines)


def structural_gaps(config_dir: str | Path) -> list[dict]:
    """Sources the configs list as not yet available (news not_included, macro not_configured)."""
    out = []
    for name, key in (("news-sources.json", "not_included"), ("macro-sources.json", "not_configured")):
        p = Path(config_dir) / name
        if p.exists():
            out += [{"id": x["id"], "reason": x.get("reason", "")} for x in json.loads(p.read_text()).get(key, [])]
    return out
