import json

from global_investment_research import alert_review as R
from global_investment_research.cli import main

DEC = {"as_of": "2026-10-09", "calls": [
    {"symbol": "LSEG", "stance": "Watch", "trigger": 87.236, "stop": None},
    {"symbol": "DBA", "stance": "Watch", "trigger": 28.391, "stop": None},
    {"symbol": "XLV", "stance": "Accumulate", "trigger": None, "stop": 152.48},
]}


def alert(sym, rule, sev, **kw):
    return {"symbol": sym, "rule": rule, "severity": sev, "evidence": "", "action": "", **kw}


def test_outcome_by_kind():
    assert R.outcome(alert("A", "Revisit trigger crossed", "INFO", observed=88.6, reference=87.2, kind="above"), 87.9) == "verified"
    assert R.outcome(alert("A", "Revisit trigger crossed", "INFO", observed=28.4, reference=28.39, kind="above"), 28.2) == "refuted"
    assert R.outcome(alert("A", "Exit level breached", "URGENT", observed=99, reference=100, kind="below"), 101) == "refuted"
    assert R.outcome(alert("A", "HKD peg band breached", "URGENT", observed=7.86, reference=[7.75, 7.85], kind="band"), 7.851) == "verified"
    move = alert("A", "Large daily move", "WARN", observed=177.2, reference=173.96, kind="move")
    assert R.outcome(move, 176.7) == "verified"          # kept 85% of the move
    assert R.outcome(move, 175.0) == "refuted"           # kept 32%: reversed
    assert R.outcome(move, None) == "unscored"
    # runs written before alerts stored references: level rules fall back to the week's decisions
    assert R.outcome(alert("LSEG", "Revisit trigger crossed", "INFO"), 87.94, DEC["calls"][0]) == "verified"
    assert R.outcome(alert("TLT", "Large daily move", "WARN"), 80.05) == "unscored"


def test_review_scores_dedupes_and_ranks_gaps():
    lseg = alert("LSEG", "Revisit trigger crossed", "INFO", key="LSEG|Revisit trigger crossed")
    runs = [
        {"at": "2026-10-12T12:07:00Z", "sent": [{**lseg, "first_seen": "2026-10-12T12:07:00Z"}],
         "news_failures": []},
        {"at": "2026-10-12T17:44:00Z",
         "sent": [alert("MONITOR", "Monitor heartbeat gap", "WARN", first_seen="2026-10-12T17:44:00Z"),
                  alert("DBA", "Revisit trigger crossed", "INFO", first_seen="2026-10-12T17:44:00Z")],
         "news_failures": [{"source": "bls_releases", "reason": "HTTPError 403: unavailable or not a valid feed"}]},
        {"at": "2026-10-15T15:40:00Z", "pushed": ["XLV|Large daily move"],
         "sent": [alert("XLV", "Large daily move", "WARN", key="XLV|Large daily move", first_seen="2026-10-15T15:40:00Z",
                        observed=177.19, reference=173.96, kind="move")],
         "news_fetched": True, "news_failures": [{"source": "bls_releases", "reason": "HTTPError 403: unavailable or not a valid feed"}],
         "no_quote": ["BARC"]},
        {"at": "2026-10-16T10:09:00Z", "sent": [{**lseg, "first_seen": "2026-10-16T10:09:00Z"}], "news_failures": []},
        # next Monday, still on these levels: not scored against this Friday's close, but a stale-levels gap
        {"at": "2026-10-19T07:07:00Z", "sent": [alert("UNG", "Revisit trigger crossed", "INFO")], "news_failures": []},
    ]
    closes = {"LSEG": 87.94, "DBA": 28.23, "XLV": 176.71}
    log = [{"date": "2026-10-16", "symbol": "XLV", "decision": "Hold"}]
    rev = R.review(runs, DEC, closes, decision_log=log, structural=[{"id": "MAS_SORA", "reason": "no key-less API"}])
    rows = {x["key"]: x for x in rev["conditions"]}
    assert rows["LSEG|Revisit trigger crossed"]["notices"] == 2 and rows["LSEG|Revisit trigger crossed"]["duplicates"] == 1
    assert rows["LSEG|Revisit trigger crossed"]["usefulness"] == "verified, not acted"
    assert rows["LSEG|Revisit trigger crossed"]["pushed"] is False                 # INFO-only runs are not pushed
    assert rows["DBA|Revisit trigger crossed"]["usefulness"] == "noise"
    assert rows["DBA|Revisit trigger crossed"]["pushed"] and rows["DBA|Revisit trigger crossed"]["pushed_inferred"]
    assert rows["XLV|Large daily move"]["usefulness"] == "acted" and not rows["XLV|Large daily move"]["pushed_inferred"]
    assert rows["MONITOR|Monitor heartbeat gap"]["usefulness"] == "system"
    t = rev["totals"]
    assert (t["conditions"], t["notices"], t["duplicates"], t["verified"], t["refuted"], t["acted"]) == (3, 4, 1, 2, 1, 1)
    assert rev["by_rule"]["Revisit trigger crossed"]["verified_rate"] == 0.5
    cov = rev["coverage"]
    assert cov["scheduled_slots"] == 5 * 63 and cov["runs_in_week"] == 4
    assert cov["news_failures"]["bls_releases"]["rate"] == 0.5               # 2 failures in 4 fetches
    assert "UNG|Revisit trigger crossed" not in rows and cov["checks_after_week_end"] == 1
    gaps = [g["gap"] for g in rev["gaps"]]
    assert gaps[:2] == ["Weekly levels stale", "Scheduled checks did not run"] and gaps[-1] == "No official source: MAS_SORA"
    assert gaps.index("No vendor quote for BARC") < gaps.index("News feed bls_releases failing")
    md = R.to_markdown(rev)
    assert "| LSEG | Revisit trigger crossed | INFO |" in md and "1. **Weekly levels stale**" in md


def test_cli_alert_review_scores_against_the_snapshot_close(tmp_path):
    rep = tmp_path / "rep"
    assert main(["demo", "--out", str(rep)]) == 0
    dec = json.loads((rep / "decisions.json").read_text())
    dec["as_of"] = "2026-10-09"
    dec["calls"].append({"symbol": "SPY", "stance": "Watch", "trigger": 700.0, "stop": None})
    (rep / "decisions.json").write_text(json.dumps(dec))
    (rep / "alerts").mkdir()
    a = alert("SPY", "Revisit trigger crossed", "INFO", key="SPY|Revisit trigger crossed",
              first_seen="2026-10-13T14:07:00Z", observed=701.0, reference=700.0, kind="above")
    (rep / "alerts" / "alerts-20261013T1407.json").write_text(json.dumps({"sent": [a], "news_failures": []}))
    snap = tmp_path / "snap"
    snap.mkdir()
    row = {"symbol": "SPY", "status": "ok", "weekly": {"time": ["2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z"],
                                                      "close": [690.0, 712.5]}}
    (snap / "us_equity.json").write_text(json.dumps({"source": "IBKR", "instruments": [row]}))
    out = tmp_path / "out"
    assert main(["alert-review", "--reports", str(rep), "--snapshot", str(snap), "--out", str(out),
                 "--decision-log", str(tmp_path / "none.csv")]) == 0
    rev = json.loads((out / "alert-review.json").read_text())
    assert rev["conditions"][0]["close"] == 712.5 and rev["conditions"][0]["outcome"] == "verified"
    assert (out / "alert-review.md").exists()
