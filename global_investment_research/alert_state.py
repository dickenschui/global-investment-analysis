"""Condition-state alerting for the hourly check: notify on change, not on every sighting.

Replaces per-day dedupe (alerts.new_since_last). Each alert is a condition keyed "symbol|rule". The owner is told
when a condition first activates or its severity rises; a condition re-arms only once a check that actually quoted
its symbol no longer sees it (an unquoted symbol proves nothing, so it never clears). Anything notified but not
confirmed delivered stays pending and is offered again next run (retry ledger). Ported from the reference
hosting/hourly_rules.py `condition()` logic.

Repeats are not re-sent: a delivered condition that clears and fires again at the same or lower severity within its
scope is reactivated silently and reported under "repeats". The scope is the same UTC day for one-day moves and
monitor notices (DAY_SCOPED_RULES), and the same desk week (`levels_as_of`) for level rules, which act on the weekly
close anyway. A higher severity is always sent, and a condition still active into a new scope (a new day, new weekly
levels) is announced again as a fresh activation.

State file: {"schema_version": 1, "last_run_utc": iso, "conditions": {"symbol|rule": {severity, first_seen,
last_seen, notified_severity, delivered, alert, levels_as_of, [cleared_at], [repeat_of]}},
"recent": {"symbol|rule": {cleared_at, first_seen, notified_severity, levels_as_of}}}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

RANK = {"INFO": 0, "WARN": 1, "URGENT": 2}
HEARTBEAT_SYMBOL = "MONITOR"   # the monitor always observes itself, so its own conditions can clear
FORGET_AFTER_DAYS = 7          # delivered conditions for symbols no longer quoted are dropped after this
DAY_SCOPED_RULES = {"Large daily move", "Extreme daily move", "Volatility spike", "FX shock",
                    "Monitor heartbeat gap", "Weekly levels stale"}


def _aware(value) -> datetime:
    """Timezone-aware UTC datetime from epoch seconds or an ISO string (naive ISO is read as UTC)."""
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, (int, float)):
        d = datetime.fromtimestamp(value, timezone.utc)
    else:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def key_of(alert: dict) -> str:
    return f"{alert['symbol']}|{alert['rule']}"


def load(state_path: str | Path) -> dict:
    p = Path(state_path)
    s = json.loads(p.read_text()) if p.exists() else {}
    s.setdefault("schema_version", 1)
    s.setdefault("conditions", {})
    s.setdefault("recent", {})
    return s


def _save(state: dict, state_path: str | Path) -> None:
    """Write atomically so a cancelled workflow cannot leave a half-written ledger."""
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(p)


def _out(key: str, c: dict) -> dict:
    """The stored alert as it should be (re)sent, with its ledger fields."""
    extra = {"key": key, "first_seen": c["first_seen"], "last_seen": c["last_seen"]}
    if c.get("cleared_at"):
        extra["cleared_at"] = c["cleared_at"]
    return {**c["alert"], **extra}


def _remember(s: dict, key: str, c: dict, cleared_at: str) -> None:
    """Keep what a delivered, now-cleared condition told the owner, so a repeat within its scope is not re-sent."""
    s["recent"][key] = {"cleared_at": cleared_at, "first_seen": c["first_seen"],
                        "notified_severity": c["notified_severity"], "levels_as_of": c.get("levels_as_of")}


def _is_repeat(key: str, severity: str, prior: dict | None, now_iso: str, levels_as_of: str | None) -> bool:
    if not prior or RANK[severity] > RANK[prior["notified_severity"]]:
        return False
    if key.split("|", 1)[1] in DAY_SCOPED_RULES:
        return prior["cleared_at"][:10] == now_iso[:10]
    return levels_as_of is not None and prior.get("levels_as_of") == levels_as_of


def _scope_rolled(key: str, c: dict, now_iso: str, levels_as_of: str | None) -> bool:
    """True when a still-active condition has outlived its scope (a new UTC day for day-scoped rules, new weekly levels
    for level rules), so it is announced again as a fresh activation. Conditions stored before levels were recorded
    are not reset; they take this week's levels instead."""
    if key.split("|", 1)[1] in DAY_SCOPED_RULES:
        return c["first_seen"][:10] != now_iso[:10]
    return None not in (c.get("levels_as_of"), levels_as_of) and c["levels_as_of"] != levels_as_of


def update(alerts: list[dict], quoted_symbols, state_path: str | Path, now_iso: str,
           levels_as_of: str | None = None) -> dict:
    """Fold this run's alerts into the ledger and say what to send.

    Returns {"new", "escalated", "cleared", "pending", "repeats"} as lists of alert dicts (each with "key"):
    new = first activation; escalated = severity above what was last notified; cleared = conditions whose symbol
    was quoted this run but no longer fire (re-armed); pending = notified on an earlier run, never marked delivered;
    repeats = reactivations within scope of a notice already delivered (logged, not sent). `levels_as_of` is the
    desk week the levels come from (decisions.json as_of). Send new + escalated + pending, then call
    mark_delivered() with the keys that went out."""
    s = load(state_path)
    conds = s["conditions"]
    quoted = set(quoted_symbols) | {HEARTBEAT_SYMBOL}
    new, escalated, cleared, repeats = [], [], [], []
    current = {}
    for a in alerts:  # one condition per key; if a caller passes duplicates, keep the most severe
        k = key_of(a)
        if k not in current or RANK[a["severity"]] > RANK[current[k]["severity"]]:
            current[k] = a
    for k, a in current.items():
        c = conds.get(k)
        if c is None and _is_repeat(k, a["severity"], s["recent"].get(k), now_iso, levels_as_of):
            prior = s["recent"].pop(k)
            conds[k] = {"severity": a["severity"], "first_seen": now_iso, "last_seen": now_iso,
                        "notified_severity": prior["notified_severity"], "delivered": True, "alert": a,
                        "levels_as_of": levels_as_of, "repeat_of": prior["first_seen"]}
            repeats.append({**_out(k, conds[k]), "repeat_of": prior["first_seen"]})
        elif c is None or c.get("cleared_at") or _scope_rolled(k, c, now_iso, levels_as_of):
            # first activation, reactivated before a cleared retry went out, or still active into a new day or week
            s["recent"].pop(k, None)
            conds[k] = {"severity": a["severity"], "first_seen": now_iso, "last_seen": now_iso,
                        "notified_severity": a["severity"], "delivered": False, "alert": a,
                        "levels_as_of": levels_as_of}
            new.append(_out(k, conds[k]))
        elif RANK[a["severity"]] > RANK[c["notified_severity"]]:
            c.update(severity=a["severity"], last_seen=now_iso, notified_severity=a["severity"], delivered=False, alert=a)
            escalated.append(_out(k, c))
        else:  # still active at or below the notified severity: stay quiet, keep the evidence current
            c.update(severity=a["severity"], last_seen=now_iso)
            if c.get("levels_as_of") is None:
                c["levels_as_of"] = levels_as_of
            if c["delivered"]:
                c["alert"] = a
    sent_now = {x["key"] for x in new + escalated}
    for k in list(conds):
        c = conds[k]
        if k in current:
            continue
        sym = k.split("|", 1)[0]
        if sym in quoted and not c.get("cleared_at"):
            cleared.append({**_out(k, c), "cleared_at": now_iso})
            if c["delivered"]:
                _remember(s, k, c, now_iso)
                del conds[k]  # re-armed
            else:
                c["cleared_at"] = now_iso  # keep the undelivered notice for retry, flagged as since cleared
        elif c["delivered"] and (_aware(now_iso) - _aware(c["last_seen"])).days >= FORGET_AFTER_DAYS:
            del conds[k]  # symbol dropped from the watch list; bound the ledger
    pending = [_out(k, c) for k, c in conds.items() if not c["delivered"] and k not in sent_now]
    s["recent"] = {k: r for k, r in s["recent"].items()
                   if (_aware(now_iso) - _aware(r["cleared_at"])).days < FORGET_AFTER_DAYS}
    s["last_run_utc"] = now_iso
    _save(s, state_path)
    order = lambda a: (-RANK[a["severity"]], a["key"])
    return {"new": sorted(new, key=order), "escalated": sorted(escalated, key=order),
            "cleared": sorted(cleared, key=order), "pending": sorted(pending, key=order),
            "repeats": sorted(repeats, key=order)}


def mark_delivered(keys, state_path: str | Path) -> None:
    """Record that these conditions reached the owner. Delivered conditions that have since cleared are re-armed."""
    s = load(state_path)
    conds = s["conditions"]
    for k in keys:
        c = conds.get(k)
        if c is None:
            continue
        if c.get("cleared_at"):
            _remember(s, k, c, c["cleared_at"])
            del conds[k]
        else:
            c["delivered"] = True
    _save(s, state_path)


def heartbeat_gap(state_path: str | Path, now_iso: str, max_hours: float = 2.5, *, weekday: bool) -> dict | None:
    """WARN alert when the previous run is more than max_hours old, else None.

    Call BEFORE update(), which overwrites last_run_utc. `weekday` is the caller's judgement that the schedule was
    meant to run throughout the gap (e.g. a weekday inside the trading-hours window); otherwise no warning. The warning
    is retrospective: it says the monitor was not observing, not that nothing happened."""
    last = load(state_path).get("last_run_utc")
    if not weekday or not last:
        return None
    gap = (_aware(now_iso) - _aware(last)).total_seconds() / 3600
    if gap <= max_hours:
        return None
    return {"severity": "WARN", "symbol": HEARTBEAT_SYMBOL, "rule": "Monitor heartbeat gap",
            "evidence": f"Previous check {last}; this check {now_iso}; gap {gap:.1f} hours (limit {max_hours:g})",
            "action": "The monitor was not observing during the gap: review exit levels and news for that interval "
                      "manually. Check the scheduler if this repeats."}


def levels_stale(levels_as_of: str | None, now_iso: str, max_days: int = 9) -> dict | None:
    """WARN alert when this week's levels are older than max_days, else None.

    Levels dated Friday D are replaced by the weekly refresh on Saturday D+1 and merged before Monday D+3, so on Monday
    D+10 they are a week out of date: every exit and revisit level is being checked against last week's decisions."""
    if not levels_as_of:
        return None
    age = (_aware(now_iso).date() - datetime.fromisoformat(levels_as_of[:10]).date()).days
    if age <= max_days:
        return None
    return {"severity": "WARN", "symbol": HEARTBEAT_SYMBOL, "rule": "Weekly levels stale",
            "evidence": f"Levels as of {levels_as_of[:10]} are {age} days old (limit {max_days})",
            "action": "The weekly refresh did not land: alerts still use last week's exits and revisit levels. Check the "
                      "weekly routine and merge its pull request."}
