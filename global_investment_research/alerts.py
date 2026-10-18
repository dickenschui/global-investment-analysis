"""Intraday exception engine for the hourly trading-day check.

Compares a 'pulse' of live quotes against this week's Decision Desk levels and each
instrument's own volatility, and reports only exceptions. Each exception carries a
severity (URGENT / WARN / INFO), the rule, the evidence and the action from the Handbook.
Already-reported exceptions are suppressed for the rest of the day via a state file (new_since_last), or by
condition state in alert_state.py, which re-arms when a condition clears.

Pulse schema: {"retrieved_at_utc": str, "quotes": [{"symbol", "last", "prior_close", "data_status", "ts"}]}
`ts` (optional) is the quote's own time, epoch seconds or ISO; quotes older than max_age_minutes are stale.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from .alert_state import HEARTBEAT_SYMBOL

DAILY_WARN_SD = 2.5      # daily move in standard deviations of the instrument's own volatility
DAILY_URGENT_SD = 4.0
VIX_STRESS = 25.0
VIX_JUMP = 0.20          # +20% day-on-day
HKD_PEG_BAND = (7.75, 7.85)
FX_URGENT_MOVE = 0.015   # 1.5% daily move in a major pair
MAX_QUOTE_AGE_MIN = 90   # a quote older than this at retrieval is not live movement
FUTURE_SKEW_MIN = 5      # clock skew tolerated before a quote time "in the future" is rejected
MACRO = {"VIX", "TLT", "IEF", "UUP", "GLD", "SLV", "USO", "CPER", "UNG", "DBA"}
MARKETS = {"SPY": "US", "QQQ": "US", "ISF": "UK", "ES3": "SG", "2800": "HK", "3188": "China"}


def watch_list(dec: dict, opt: dict | None, meta_kinds: dict) -> list[str]:
    """Symbols the hourly pulse must quote: positions with levels, option underlyings, markets, macro, FX."""
    syms = {c["symbol"] for c in dec["calls"] if c["stance"] in ("Accumulate", "Hold")}
    syms |= {c["symbol"] for c in dec["calls"] if c.get("trigger") and c["stance"] == "Watch"}
    syms |= {i["symbol"] for i in (opt or {}).get("ideas", [])}
    syms |= set(MARKETS) | MACRO | {s for s, k in meta_kinds.items() if k == "fx"}
    return sorted(syms)


def _alert(sev, sym, rule, evidence, action, *, observed=None, reference=None, kind=None):
    """An exception, with the numbers alert_review needs to score it at the official close: `observed` (the vendor
    quote), `reference` (the level, band or prior close it was judged against) and `kind` ("above", "below", "band"
    or "move")."""
    a = {"severity": sev, "symbol": sym, "rule": rule, "evidence": evidence, "action": action}
    if kind:
        a.update(observed=observed, reference=reference, kind=kind)
    return a


def _is_fx(sym: str, kinds: dict | None) -> bool:
    if kinds is not None:
        return kinds.get(sym) == "fx"
    a, _, b = sym.partition(".")
    return len(a) == 3 and len(b) == 3 and a.isalpha() and b.isalpha()


def _utc(value) -> datetime | None:
    """Epoch seconds or ISO string -> aware UTC datetime; None if absent or unreadable."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def quote_age_minutes(quote: dict, retrieved_at_utc) -> float | None:
    """Minutes between the quote's own time (`ts`) and retrieval; None when either is missing or unreadable."""
    t, r = _utc(quote.get("ts")), _utc(retrieved_at_utc)
    return None if t is None or r is None else (r - t).total_seconds() / 60


def normalise_quotes(pulse: dict, mon: dict, *, max_age_minutes: float = MAX_QUOTE_AGE_MIN) -> tuple[dict, list[str]]:
    """Usable quotes in the same units as this week's levels. London lines quote live prices in pence while
    the weekly closes (and so every exit and trigger) are in pounds: a quote about 100x the weekly close is
    divided by 100, whoever collected it. Set aside as stale: frozen quotes (market closed, weekend), quotes whose
    `ts` is more than max_age_minutes before the pulse's retrieved_at_utc, and quotes whose `ts` is unreadable or
    in the future. Quotes without `ts` are judged on data_status alone."""
    q, stale = {}, []
    retrieved = pulse.get("retrieved_at_utc")
    for x in pulse["quotes"]:
        if not x.get("last"):
            continue
        if "FROZEN" in str(x.get("data_status") or "").upper():
            stale.append(x["symbol"])
            continue
        if x.get("ts") is not None and retrieved:
            age = quote_age_minutes(x, retrieved)
            if age is None or age > max_age_minutes or age < -FUTURE_SKEW_MIN:
                stale.append(x["symbol"])
                continue
        ref = (mon["metrics"].get(x["symbol"]) or {}).get("last")
        k = 100.0 if ref and x["last"] / ref > 50 else 1.0
        q[x["symbol"]] = {**x, "last": x["last"] / k,
                          "prior_close": x["prior_close"] / k if x.get("prior_close") else x.get("prior_close")}
    return q, stale


def evaluate(pulse: dict, dec: dict, mon: dict, opt: dict | None = None, kinds: dict | None = None, *,
             max_age_minutes: float = MAX_QUOTE_AGE_MIN) -> list[dict]:
    q, _ = normalise_quotes(pulse, mon, max_age_minutes=max_age_minutes)
    calls = {c["symbol"]: c for c in dec["calls"]}
    out = []
    for sym, x in q.items():
        last, prev = x["last"], x.get("prior_close")
        m = mon["metrics"].get(sym) or {}
        c = calls.get(sym)
        # 1. Exit levels and revisit triggers from the Decision Desk
        if c and c.get("stop") and last < c["stop"]:
            out.append(_alert("URGENT", sym, "Exit level breached",
                              f"{sym} {last:,.2f} below exit {c['stop']:,.2f} ({c['stance']}), intraday",
                              "Handbook §3: the exit acts on the weekly close. Cancel any add orders now; if Friday's "
                              "close is still below the exit, close the position or write down why you hold.",
                              observed=last, reference=c["stop"], kind="below"))
        if c and c.get("trigger") and c["stance"] in ("Watch", "Avoid") and last > c["trigger"]:
            out.append(_alert("INFO", sym, "Revisit trigger crossed",
                              f"{sym} {last:,.2f} above revisit level {c['trigger']:,.2f}",
                              "Opportunity: confirm on the weekly close before upgrading.",
                              observed=last, reference=c["trigger"], kind="above"))
        # 2. Abnormal move versus the instrument's own volatility
        vol = m.get("vol")
        if prev and vol and sym != "VIX":
            move = last / prev - 1
            sd = vol / math.sqrt(252)
            z = move / sd if sd else 0
            if abs(z) >= DAILY_URGENT_SD:
                out.append(_alert("URGENT", sym, "Extreme daily move",
                                  f"{sym} {move*100:+.1f}% vs prior close ({z:+.1f} standard deviations)",
                                  "Check news before acting; re-run the Decision Desk for this name.",
                                  observed=last, reference=prev, kind="move"))
            elif abs(z) >= DAILY_WARN_SD:
                out.append(_alert("WARN", sym, "Large daily move",
                                  f"{sym} {move*100:+.1f}% vs prior close ({z:+.1f} standard deviations)",
                                  "Review position and options on this underlying.",
                                  observed=last, reference=prev, kind="move"))
        # 3. Macro regime: volatility
        if sym == "VIX":
            if last >= VIX_STRESS:
                out.append(_alert("URGENT", sym, "Volatility stress",
                                  f"VIX {last:.1f} at or above {VIX_STRESS:.0f}",
                                  "Handbook §4: risk budget to 50%; no new Accumulate in high-volatility names.",
                                  observed=last, reference=VIX_STRESS, kind="above"))
            if prev and last / prev - 1 >= VIX_JUMP:
                out.append(_alert("WARN", sym, "Volatility spike", f"VIX {last:.1f}, {100*(last/prev-1):+.0f}% on the day",
                                  "Tighten: no new risk until the close.", observed=last, reference=prev, kind="move"))
        # 4. FX shocks and the HKD peg
        if sym == "USD.HKD" and not (HKD_PEG_BAND[0] <= last <= HKD_PEG_BAND[1]):
            out.append(_alert("URGENT", sym, "HKD peg band breached", f"USD/HKD {last:.4f} outside 7.75–7.85",
                              "Systemic Hong Kong risk: review all HK exposure immediately.",
                              observed=last, reference=list(HKD_PEG_BAND), kind="band"))
        if _is_fx(sym, kinds) and prev and abs(last / prev - 1) >= FX_URGENT_MOVE:
            out.append(_alert("URGENT", sym, "FX shock", f"{sym.replace('.', '/')} {100*(last/prev-1):+.2f}% on the day",
                               "Check hedges and non-base-currency positions.", observed=last, reference=prev, kind="move"))
    # 5. Options: underlying through breakeven against us, or desk exit hit on the underlying
    for i in (opt or {}).get("ideas", []):
        x = q.get(i["symbol"])  # already in the units of the weekly levels
        if not x:
            continue
        last = x["last"]
        wrong_way = (i["direction"] == "bull" and last < i["underlying"] * (1 - 0.05)) or \
                    (i["direction"] == "bear" and last > i["underlying"] * (1 + 0.05))
        if wrong_way:
            out.append(_alert("WARN", i["symbol"], "Option idea moving against",
                              f"{i['symbol']} {last:,.2f} vs {i['underlying']:,.2f} when the {i['structure']} was priced",
                              f"Handbook §9: check the −{int(50)}% premium stop on any open {i['structure']}.",
                              observed=last, kind="below" if i["direction"] == "bull" else "above",
                              reference=i["underlying"] * (0.95 if i["direction"] == "bull" else 1.05)))
    order = {"URGENT": 0, "WARN": 1, "INFO": 2}
    # one alert per (symbol, rule)
    uniq = {(a["symbol"], a["rule"]): a for a in out}
    return sorted(uniq.values(), key=lambda a: (order[a["severity"]], a["symbol"]))


def new_since_last(alerts: list[dict], state_dir: str | Path, today: str | None = None) -> list[dict]:
    """Drop alerts already reported today; remember the rest."""
    today = today or date.today().isoformat()
    path = Path(state_dir) / "alerts_seen.json"
    seen = json.loads(path.read_text()) if path.exists() else {}
    seen = {k: v for k, v in seen.items() if v == today}  # forget previous days
    fresh = []
    for a in alerts:
        key = f"{a['symbol']}|{a['rule']}"
        if key not in seen:
            fresh.append(a)
            seen[key] = today
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, indent=1))
    return fresh


SECTIONS = (("URGENT", "Act now"), ("WARN", "Review today"), ("INFO", "For the weekly close"))
PROVENANCE = ("<i>Prices: Yahoo Finance, a delayed vendor feed, unconfirmed until the official close. "
              "Headlines: keyword matches on official feeds, not checked against the release.</i>")


def _headline(h: dict) -> str:
    e = html.escape
    when = f" ({e(h['published'][:16].replace('T', ' '))})" if h.get("published") else ""
    title = e(h["title"])
    link = f'<a href="{e(h["link"], quote=True)}">{title}</a>' if h.get("link") else title
    src = f" · {e(h['source'])}" if h.get("source") else ""
    return f"📰 Unverified headline: {link}{when}{src}"


def alert_text(alerts: list[dict], as_of: str, not_live: list[str] | None = None,
               no_quote: list[str] | None = None) -> str:
    """One consolidated message. Each symbol appears once, in the section of its most severe exception, with every
    rule it broke; INFO lines share one action line; monitor-health notices sit apart from market exceptions.
    `not_live` (market closed or quote too old) is counted, not listed; `no_quote` (the vendor returned nothing
    usable while the market may be open) is listed, since it is a coverage gap."""
    e = html.escape
    icon = {"URGENT": "🔴", "WARN": "🟠", "INFO": "🔵"}
    rank = {"URGENT": 0, "WARN": 1, "INFO": 2}
    system = [a for a in alerts if a["symbol"] == HEARTBEAT_SYMBOL]
    groups: dict[str, list[dict]] = {}
    for a in alerts:
        if a["symbol"] != HEARTBEAT_SYMBOL:
            groups.setdefault(a["symbol"], []).append(a)
    top = {sym: min(grp, key=lambda a: rank[a["severity"]])["severity"] for sym, grp in groups.items()}
    urgent = any(v == "URGENT" for v in top.values())
    lines = [f"<b>{'URGENT: ' if urgent else ''}Market exceptions · {e(as_of)}</b>"]
    for sev, title in SECTIONS:
        syms = sorted(s for s in groups if top[s] == sev)
        if not syms:
            continue
        lines += ["", f"<b>{title}</b>"]
        actions = []
        for sym in syms:
            grp = sorted(groups[sym], key=lambda a: (rank[a["severity"]], a["rule"]))
            if sev != "INFO":
                lines.append(f"<b>{e(sym)}</b>")
            for a in grp:
                if sev == "INFO":  # weekly-close items: one line each, actions listed once below
                    lines.append(f"{icon[a['severity']]} <b>{e(sym)}</b> {e(a['rule'])}: {e(a['evidence'])}")
                    actions += [a["action"]] if a["action"] not in actions else []
                else:
                    lines += [f"{icon[a['severity']]} {e(a['rule'])}: {e(a['evidence'])}", f"→ {e(a['action'])}"]
                if a.get("cleared_at"):
                    lines.append(f"<i>Condition cleared at {e(a['cleared_at'])}; resent because this notice was not "
                                 f"delivered earlier.</i>")
            seen = set()
            for h in (h for a in grp for h in a.get("context") or []):  # news.headline_context, for review only
                if h.get("link") not in seen:
                    seen.add(h.get("link"))
                    lines.append(_headline(h))
        lines += [f"→ {e(x)}" for x in actions]
    if system:
        lines += ["", "<b>Monitor health</b>"]
        for a in system:
            lines += [f"⚙️ {e(a['rule'])}: {e(a['evidence'])}", f"→ {e(a['action'])}"]
    lines.append("")
    if no_quote:
        lines.append(f"<i>No usable vendor quote: {e(', '.join(no_quote))}</i>")
    if not_live:
        lines.append(f"<i>{len(not_live)} watched lines not live (market closed or quote too old).</i>")
    lines += [PROVENANCE, '<a href="https://claude.ai/artifact/SSQ1VK33aGvevMkNsappyW">Decision Desk</a>']
    return "\n".join(lines)
