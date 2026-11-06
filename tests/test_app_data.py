import json
from pathlib import Path

from global_investment_research import app_data, data

ROOT = Path(__file__).resolve().parents[1]
REP, SNAP = ROOT / "reports/2026-10-11", ROOT / "data/snapshots/2026-10-11"


def test_app_data_is_complete_and_copies_engine_numbers():
    u = data.load_universe(ROOT / "config/universe.json")
    res = app_data.build(REP, SNAP, u)
    dec = json.loads((REP / "decisions.json").read_text())
    assert res["as_of"] == dec["as_of"]
    assert len(res["calls"]) == len(dec["calls"])
    by = {c["symbol"]: c for c in res["calls"]}
    for c in dec["calls"]:
        a = by[c["symbol"]]
        assert (a["stance"], a["last"], a["stop"], a["size"]) == (c["stance"], c["last"], c["stop"], c["size"])
        assert a["contract"]["id"], c["symbol"]
        assert a["spark"] and a["spark"][-1] == c["last"]       # sparkline ends at the decision price
    assert by["HSBA"]["contract"]["live_divisor"] == 100        # London live quotes are in pence
    assert by["XLE"]["contract"]["live_divisor"] == 1
    assert {c["symbol"] for c in res["calls"]}.isdisjoint({o["symbol"] for o in res["others"]})
    assert len(res["calls"]) + len(res["others"]) == len(u["instruments"])
    assert res["evidence"]["level"] in ("LOW", "MEDIUM", "HIGH")
    assert len(json.dumps(res)) < 600_000


def test_daily_closes_are_checked_against_this_weeks_levels():
    calls = [
        {"symbol": "XOM", "stance": "Accumulate", "last": 100.0, "entry": [95.0, 102.0], "stop": 90.0, "trigger": None, "vol": 0.25},
        {"symbol": "JPM", "stance": "Watch", "last": 300.0, "entry": None, "stop": None, "trigger": 310.0, "vol": 0.2},
        {"symbol": "BARC", "stance": "Hold", "last": 4.0, "entry": None, "stop": 3.5, "trigger": None, "vol": 0.3},
        {"symbol": "2800", "stance": "Hold", "last": 25.0, "entry": None, "stop": 20.0, "trigger": None, "vol": 0.2},
        {"symbol": "ODD", "stance": "Hold", "last": 10.0, "entry": None, "stop": 8.0, "trigger": None, "vol": 0.2},
        {"symbol": "GONE", "stance": "Hold", "last": 10.0, "entry": None, "stop": 8.0, "trigger": None, "vol": 0.2},
        {"symbol": "LATE", "stance": "Hold", "last": 10.0, "entry": None, "stop": 10.5, "trigger": None, "vol": 0.2},
    ]
    others = [{"symbol": "USD.SGD", "last": 1.28}, {"symbol": "VIX", "last": 14.84, "vol": 1.07}]
    doc = {"session_date": "2026-10-16", "source": "IBKR", "retrieved_at_utc": "2026-10-16T20:25:00Z", "closes": {
        "XOM": {"close": 89.0, "prior_close": 92.0, "bar_time": "2026-10-16T13:30:00Z"},      # below the exit
        "JPM": {"close": 312.0, "prior_close": 305.0, "bar_time": "2026-10-16T13:30:00Z"},    # above the trigger
        "BARC": {"close": 410.0, "prior_close": 400.0, "bar_time": "2026-10-16T07:00:00Z"},   # pence
        "2800": {"close": 25.1, "prior_close": 25.0, "bar_time": "2026-10-15T01:30:00Z"},     # holiday: stale
        "ODD": {"close": 14.0, "prior_close": 10.0, "bar_time": "2026-10-16T13:30:00Z"},      # +40%: verify
        "LATE": {"close": 10.1, "prior_close": 10.0, "bar_time": "2026-10-15T13:30:00Z"},     # IBKR not updated: stale
        "USD.SGD": {"close": 1.2798, "prior_close": 1.282, "bar_time": "2026-10-15T21:15:00Z"},  # FX: next session
        "VIX": {"close": 19.99, "prior_close": 16.46, "bar_time": "2026-10-16T07:15:00Z"}}}      # +35%: normal for VIX
    d = app_data.daily_view(calls, others, doc)
    ex = {(x["symbol"], x["kind"]) for x in d["exceptions"]}
    assert ("XOM", "crit") in ex and ("JPM", "info") in ex
    assert d["rows"]["BARC"]["close"] == 4.1 and d["rows"]["BARC"]["chg_1d"] == 4.1 / 4.0 - 1
    assert d["stale"] == ["2800", "LATE"] and d["check"] == ["ODD"] and d["missing"] == ["GONE"]
    assert not any(x["symbol"] == "LATE" for x in d["exceptions"])        # a prior-day bar is never judged
    assert "stale" not in d["rows"]["USD.SGD"] and d["exceptions"][0]["symbol"] == "XOM"
    assert "check" not in d["rows"]["VIX"] and d["rows"]["VIX"]["close"] == 19.99
    move = app_data._flags({"stance": "Hold", "last": 100.0, "vol": 0.2}, 80.0, "2026-10-09")
    assert move == [["crit", "-20.0% since the 2026-10-09 close: 7.2 weekly standard deviations.", "move"]]


def test_cli_app_data_with_daily_closes(tmp_path):
    from global_investment_research.cli import main
    dec = json.loads((REP / "decisions.json").read_text())
    c = next(x for x in dec["calls"] if x["stance"] == "Accumulate")
    daily = tmp_path / "2026-10-16.json"
    daily.write_text(json.dumps({"session_date": "2026-10-16", "source": "IBKR", "retrieved_at_utc": "2026-10-16T20:25:00Z",
                                 "closes": {c["symbol"]: {"close": c["stop"] * 0.99, "prior_close": c["last"],
                                                          "bar_time": "2026-10-16T13:30:00Z"}}}))
    out = tmp_path / "data.json"
    assert main(["app-data", "--reports", str(REP), "--snapshot", str(SNAP), "--daily", str(daily), "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["as_of"] == dec["as_of"]                                     # levels stay the week's
    assert res["daily"]["exceptions"][0] == {"symbol": c["symbol"], "kind": "crit", "rule": "exit",
                                             "text": res["daily"]["rows"][c["symbol"]]["flags"][0][1]}
