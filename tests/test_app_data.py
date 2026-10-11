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
