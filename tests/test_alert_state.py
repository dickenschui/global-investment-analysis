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
    assert r == {"new": [], "escalated": [], "cleared": [], "pending": [], "repeats": []}
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


def test_repeat_within_desk_week_is_logged_not_resent(tmp_path):
    p = tmp_path / "state.json"
    k = "LSEG|Revisit trigger crossed"
    S.mark_delivered(keys(S.update([al("LSEG", "Revisit trigger crossed", "INFO")], {"LSEG"}, p, T1,
                                   levels_as_of="2026-10-09")["new"]), p)
    assert keys(S.update([], {"LSEG"}, p, "2026-10-13T13:34:00Z", levels_as_of="2026-10-09")["cleared"]) == [k]
    # crosses again later in the same desk week: the owner already has this notice
    r = S.update([al("LSEG", "Revisit trigger crossed", "INFO")], {"LSEG"}, p, "2026-10-16T10:09:00Z",
                 levels_as_of="2026-10-09")
    assert r["new"] == [] and r["pending"] == [] and keys(r["repeats"]) == [k]
    assert r["repeats"][0]["repeat_of"] == T1
    # a higher severity is always sent
    S.update([], {"LSEG"}, p, "2026-10-16T11:00:00Z", levels_as_of="2026-10-09")
    r = S.update([al("LSEG", "Revisit trigger crossed", "WARN")], {"LSEG"}, p, "2026-10-16T12:00:00Z",
                 levels_as_of="2026-10-09")
    assert keys(r["new"]) == [k]


def test_repeat_scope_is_new_levels_for_level_rules_and_a_new_day_for_moves(tmp_path):
    p = tmp_path / "state.json"
    S.mark_delivered(keys(S.update([al("JPM", "Revisit trigger crossed", "INFO"), al("XOM", "Large daily move", "WARN")],
                                   {"JPM", "XOM"}, p, T1, levels_as_of="2026-10-09")["new"]), p)
    S.update([], {"JPM", "XOM"}, p, T2, levels_as_of="2026-10-09")
    # same day: the move repeat is quiet; next week's levels: the level rule is a fresh notice
    r = S.update([al("XOM", "Large daily move", "WARN")], {"XOM"}, p, T3, levels_as_of="2026-10-09")
    assert keys(r["repeats"]) == ["XOM|Large daily move"]
    r = S.update([al("JPM", "Revisit trigger crossed", "INFO")], {"JPM"}, p, T4, levels_as_of="2026-10-16")
    assert keys(r["new"]) == ["JPM|Revisit trigger crossed"]
    S.update([], {"XOM"}, p, T4)
    r = S.update([al("XOM", "Large daily move", "WARN")], {"XOM"}, p, "2026-10-13T10:00:00Z")
    assert keys(r["new"]) == ["XOM|Large daily move"]                   # a new day is a new move


def test_levels_stale():
    assert S.levels_stale("2026-10-09", "2026-10-16T14:00:00Z") is None        # the desk week itself
    assert S.levels_stale("2026-10-09", "2026-10-18T14:00:00Z") is None        # 9 days: refresh may still land
    w = S.levels_stale("2026-10-09", "2026-10-19T07:00:00Z")                   # Monday, no refresh
    assert w["severity"] == "WARN" and w["symbol"] == S.HEARTBEAT_SYMBOL and "10 days old" in w["evidence"]
    assert S.levels_stale(None, "2026-10-19T07:00:00Z") is None


def test_condition_still_active_into_a_new_scope_is_announced_again(tmp_path):
    p = tmp_path / "state.json"
    exit_k, move_k = "XOM|Exit level breached", "NVDA|Large daily move"
    S.mark_delivered(keys(S.update([al("XOM", "Exit level breached", "URGENT"), al("NVDA", "Large daily move", "WARN")],
                                   {"XOM", "NVDA"}, p, "2026-10-16T19:47:00Z", levels_as_of="2026-10-09")["new"]), p)
    # same day, same levels: quiet
    r = S.update([al("XOM", "Exit level breached", "URGENT"), al("NVDA", "Large daily move", "WARN")],
                 {"XOM", "NVDA"}, p, "2026-10-16T20:07:00Z", levels_as_of="2026-10-09")
    assert r["new"] == []
    # never cleared over the weekend (markets closed); Monday brings new levels and a new day
    r = S.update([al("XOM", "Exit level breached", "URGENT"), al("NVDA", "Large daily move", "WARN")],
                 {"XOM", "NVDA"}, p, "2026-10-19T14:07:00Z", levels_as_of="2026-10-16")
    assert keys(r["new"]) == [exit_k, move_k] and r["repeats"] == []


def test_conditions_stored_before_levels_were_recorded_are_not_reset(tmp_path):
    p = tmp_path / "state.json"
    S.mark_delivered(keys(S.update([al("JPM", "Revisit trigger crossed", "INFO")], {"JPM"}, p, T1)["new"]), p)
    r = S.update([al("JPM", "Revisit trigger crossed", "INFO")], {"JPM"}, p, T2, levels_as_of="2026-10-09")
    assert r["new"] == []                                                   # no burst on upgrade
    r = S.update([al("JPM", "Revisit trigger crossed", "INFO")], {"JPM"}, p, T3, levels_as_of="2026-10-16")
    assert keys(r["new"]) == ["JPM|Revisit trigger crossed"]               # but next week's levels do reset it
