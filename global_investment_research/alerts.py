"""Intraday exception engine for the hourly trading-day check.

Compares a 'pulse' of live quotes against this week's Decision Desk levels and each
instrument's own volatility, and reports only exceptions. Each exception carries a
severity (URGENT / WARN / INFO), the rule, the evidence and the action from the Handbook.
Already-reported exceptions are suppressed for the rest of the day via a state file.

Pulse schema: {"retrieved_at_utc": str, "quotes": [{"symbol", "last", "prior_close", "data_status"}]}
"""
from __future__ import annotations

import html
import json
import math
from datetime import date
from pathlib import Path

DAILY_WARN_SD = 2.5      # daily move in standard deviations of the instrument's own volatility
DAILY_URGENT_SD = 4.0
VIX_STRESS = 25.0
VIX_JUMP = 0.20          # +20% day-on-day
HKD_PEG_BAND = (7.75, 7.85)
FX_URGENT_MOVE = 0.015   # 1.5% daily move in a major pair
MACRO = {"VIX", "TLT", "IEF", "UUP", "GLD", "SLV", "USO", "CPER", "UNG", "DBA"}
MARKETS = {"SPY": "US", "QQQ": "US", "ISF": "UK", "ES3": "SG", "2800": "HK", "3188": "China"}


def watch_list(dec: dict, opt: dict | None, meta_kinds: dict) -> list[str]:
    """Symbols the hourly pulse must quote: positions with levels, option underlyings, markets, macro, FX."""
    syms = {c["symbol"] for c in dec["calls"] if c["stance"] in ("Accumulate", "Hold")}
    syms |= {c["symbol"] for c in dec["calls"] if c.get("trigger") and c["stance"] == "Watch"}
    syms |= {i["symbol"] for i in (opt or {}).get("ideas", [])}
    syms |= set(MARKETS) | MACRO | {s for s, k in meta_kinds.items() if k == "fx"}
    return sorted(syms)


def _alert(sev, sym, rule, evidence, action):
    return {"severity": sev, "symbol": sym, "rule": rule, "evidence": evidence, "action": action}


def _is_fx(sym: str, kinds: dict | None) -> bool:
    if kinds is not None:
        return kinds.get(sym) == "fx"
    a, _, b = sym.partition(".")
    return len(a) == 3 and len(b) == 3 and a.isalpha() and b.isalpha()


def normalise_quotes(pulse: dict, mon: dict) -> tuple[dict, list[str]]:
    """Usable quotes in the same units as this week's levels. London lines quote live prices in pence while
    the weekly closes (and so every exit and trigger) are in pounds: a quote about 100x the weekly close is
    divided by 100, whoever collected it. Frozen quotes (market closed, weekend) are set aside as stale."""
    q, stale = {}, []
    for x in pulse["quotes"]:
        if not x.get("last"):
            continue
        if "FROZEN" in str(x.get("data_status") or "").upper():
            stale.append(x["symbol"])
            continue
        ref = (mon["metrics"].get(x["symbol"]) or {}).get("last")
        k = 100.0 if ref and x["last"] / ref > 50 else 1.0
        q[x["symbol"]] = {**x, "last": x["last"] / k,
                          "prior_close": x["prior_close"] / k if x.get("prior_close") else x.get("prior_close")}
    return q, stale


def evaluate(pulse: dict, dec: dict, mon: dict, opt: dict | None = None, kinds: dict | None = None) -> list[dict]:
    q, _ = normalise_quotes(pulse, mon)
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
                              "close is still below the exit, close the position or write down why you hold."))
        if c and c.get("trigger") and c["stance"] in ("Watch", "Avoid") and last > c["trigger"]:
            out.append(_alert("INFO", sym, "Revisit trigger crossed",
                              f"{sym} {last:,.2f} above revisit level {c['trigger']:,.2f}",
                              "Opportunity: confirm on the weekly close before upgrading."))
        # 2. Abnormal move versus the instrument's own volatility
        vol = m.get("vol")
        if prev and vol and sym != "VIX":
            move = last / prev - 1
            sd = vol / math.sqrt(252)
            z = move / sd if sd else 0
            if abs(z) >= DAILY_URGENT_SD:
                out.append(_alert("URGENT", sym, "Extreme daily move",
                                  f"{sym} {move*100:+.1f}% vs prior close ({z:+.1f} standard deviations)",
                                  "Check news before acting; re-run the Decision Desk for this name."))
            elif abs(z) >= DAILY_WARN_SD:
                out.append(_alert("WARN", sym, "Large daily move",
                                  f"{sym} {move*100:+.1f}% vs prior close ({z:+.1f} standard deviations)",
                                  "Review position and options on this underlying."))
        # 3. Macro regime: volatility
        if sym == "VIX":
            if last >= VIX_STRESS:
                out.append(_alert("URGENT", sym, "Volatility stress",
                                  f"VIX {last:.1f} at or above {VIX_STRESS:.0f}",
                                  "Handbook §4: risk budget to 50%; no new Accumulate in high-volatility names."))
            if prev and last / prev - 1 >= VIX_JUMP:
                out.append(_alert("WARN", sym, "Volatility spike", f"VIX {last:.1f}, {100*(last/prev-1):+.0f}% on the day",
                                  "Tighten: no new risk until the close."))
        # 4. FX shocks and the HKD peg
        if sym == "USD.HKD" and not (HKD_PEG_BAND[0] <= last <= HKD_PEG_BAND[1]):
            out.append(_alert("URGENT", sym, "HKD peg band breached", f"USD/HKD {last:.4f} outside 7.75–7.85",
                              "Systemic Hong Kong risk: review all HK exposure immediately."))
        if _is_fx(sym, kinds) and prev and abs(last / prev - 1) >= FX_URGENT_MOVE:
            out.append(_alert("URGENT", sym, "FX shock", f"{sym.replace('.', '/')} {100*(last/prev-1):+.2f}% on the day",
                               "Check hedges and non-base-currency positions."))
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
                              f"Handbook §9: check the −{int(50)}% premium stop on any open {i['structure']}."))
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


def alert_text(alerts: list[dict], as_of: str, stale: list[str] | None = None) -> str:
    e = html.escape
    icon = {"URGENT": "🔴", "WARN": "🟠", "INFO": "🔵"}
    urgent = sum(a["severity"] == "URGENT" for a in alerts)
    lines = [f"<b>{'URGENT: ' if urgent else ''}Market exceptions · {e(as_of)}</b>"]
    for a in alerts:
        lines += [f"{icon[a['severity']]} <b>{e(a['symbol'])}: {e(a['rule'])}</b>", e(a["evidence"]), f"→ {e(a['action'])}", ""]
    if stale:
        lines.append(f"<i>No live quote for: {e(', '.join(stale))}</i>")
    lines.append('<a href="https://claude.ai/artifact/SSQ1VK33aGvevMkNsappyW">Decision Desk</a>')
    return "\n".join(lines)
