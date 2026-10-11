import json

from global_investment_research import alert_state as S


def al(sym, rule, sev):
    return {"symbol": sym, "rule": rule, "severity": sev, "evidence": f"{sym} {sev}", "action": "review"}


T1, T2, T3, T4 = "2026-10-12T09:17:00Z", "2026-10-12T10:17:00Z", "2026-10-12T11:17:00Z", "2026-10-12T12:17:00Z"


def keys(lst):
    return [a["key"] for a in lst]


def test_first_activation_then_quiet_then_escalation(tmp_path):
    p = tmp_path / "state.json"
    r = S.update([al("XOM", "Large daily move", "WARN")], {"XOM"}, p, T1)
    assert keys(r["new"]) == ["XOM|Large daily move"] and r["pending"] == []
    S.mark_delivered(keys(r["new"]), p)
    # still active at the same severity: nothing to send
    r = S.update([al("XOM", "Large daily move", "WARN")], {"XOM"}, p, T2)
    assert r == {"new": [], "escalated": [], "cleared": [], "pending": []}
    # severity rises: notify once as an escalation
    r = S.update([al("XOM", "Large daily move", "URGENT")], {"XOM"}, p, T3)
    assert keys(r["escalated"]) == ["XOM|Large daily move"] and r["escalated"][0]["severity"] == "URGENT"
    assert r["escalated"][0]["first_seen"] == T1
    S.mark_delivered(["XOM|Large daily move"], p)
    # falls back to WARN: still active, below what was notified, so quiet
    r = S.update([al("XOM", "Large daily move", "WARN")], {"XOM"}, p, T4)
    assert r["new"] == r["escalated"] == r["pending"] == []
    c = json.loads(p.read_text())["conditions"]["XOM|Large daily move"]
    assert c["severity"] == "WARN" and c["notified_severity"] == "URGENT" and c["last_seen"] == T4


def test_clear_rearms_only_when_symbol_was_quoted(tmp_path):
    p = tmp_path / "state.json"
    S.mark_delivered(keys(S.update([al("XOM", "Exit level breached", "URGENT")], {"XOM"}, p, T1)["new"]), p)
    # XOM not quoted this run (feed gap): absence proves nothing, so the condition stays active
    r = S.update([], {"SPY"}, p, T2)
    assert r["cleared"] == []
    assert S.update([al("XOM", "Exit level breached", "URGENT")], {"XOM"}, p, T3)["new"] == []
    # XOM quoted and the condition is gone: cleared and re-armed
    r = S.update([], {"XOM"}, p, T3)
    assert keys(r["cleared"]) == ["XOM|Exit level breached"]
    assert json.loads(p.read_text())["conditions"] == {}
    # breaks again: a fresh notice
    assert keys(S.update([al("XOM", "Exit level breached", "URGENT")], {"XOM"}, p, T4)["new"]) == ["XOM|Exit level breached"]


def test_undelivered_notices_are_retried_until_marked(tmp_path):
    p = tmp_path / "state.json"
    r = S.update([al("VIX", "Volatility stress", "URGENT"), al("JPM", "Revisit trigger crossed", "INFO")],
                 {"VIX", "JPM"}, p, T1)
    assert keys(r["new"]) == ["VIX|Volatility stress", "JPM|Revisit trigger crossed"]  # URGENT first
    # delivery failed: nothing marked. Next run, both are pending (not new again)
    r = S.update([al("VIX", "Volatility stress", "URGENT"), al("JPM", "Revisit trigger crossed", "INFO")],
                 {"VIX", "JPM"}, p, T2)
    assert r["new"] == [] and keys(r["pending"]) == ["VIX|Volatility stress", "JPM|Revisit trigger crossed"]
    S.mark_delivered(["VIX|Volatility stress"], p)
    r = S.update([al("VIX", "Volatility stress", "URGENT")], {"VIX"}, p, T3)  # JPM not quoted: still pending
    assert keys(r["pending"]) == ["JPM|Revisit trigger crossed"]


def test_cleared_but_undelivered_notice_is_still_retried(tmp_path):
    p = tmp_path / "state.json"
    S.update([al("XOM", "Exit level breached", "URGENT")], {"XOM"}, p, T1)   # send failed
    r = S.update([], {"XOM"}, p, T2)                                       # recovered before delivery
    assert keys(r["cleared"]) == ["XOM|Exit level breached"]
    assert keys(r["pending"]) == ["XOM|Exit level breached"] and r["pending"][0]["cleared_at"] == T2
    S.mark_delivered(["XOM|Exit level breached"], p)
    assert json.loads(p.read_text())["conditions"] == {}                   # delivered + cleared: re-armed


def test_heartbeat_gap(tmp_path):
    p = tmp_path / "state.json"
    assert S.heartbeat_gap(p, T1, weekday=True) is None                     # no previous run
    S.update([], set(), p, "2026-10-12T09:00:00Z")
    assert S.heartbeat_gap(p, "2026-10-12T11:30:00Z", weekday=True) is None  # 2.5 h exactly: within limit
    w = S.heartbeat_gap(p, "2026-10-12T12:00:00Z", weekday=True)            # 3.0 h
    assert w["severity"] == "WARN" and w["symbol"] == S.HEARTBEAT_SYMBOL and "gap 3.0 hours" in w["evidence"]
    assert S.heartbeat_gap(p, "2026-10-12T12:00:00Z", weekday=False) is None
    # the heartbeat alert goes through update() and re-arms on the next run without a gap
    assert keys(S.update([w], set(), p, "2026-10-12T12:00:00Z")["new"]) == ["MONITOR|Monitor heartbeat gap"]
    S.mark_delivered(["MONITOR|Monitor heartbeat gap"], p)
    assert keys(S.update([], set(), p, "2026-10-12T13:00:00Z")["cleared"]) == ["MONITOR|Monitor heartbeat gap"]
