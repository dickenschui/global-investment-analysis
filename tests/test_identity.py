import json
import shutil
from pathlib import Path

from global_investment_research import data, identity

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "data/snapshots/2026-10-11"
LONG = ROOT / "data/history/weekly_long"


def _universe():
    return data.load_universe(ROOT / "config/universe.json")


def test_real_snapshot_and_history_pass():
    u = _universe()
    assert identity.check(SNAP, u) == []
    # The 19-year history was collected for the same contracts as this week's snapshot.
    assert identity.check(SNAP, u, previous_dir=LONG) == []
    assert identity.check(LONG, u) == []


def test_rules_from_the_snapshot():
    fx = identity.expected_rules({"symbol": "EUR.GBP", "market": "Cross-asset", "kind": "fx"})
    assert fx == [(frozenset({"IDEALPRO"}), frozenset({"GBP"}), "CASH")]   # quote currency, as IBKR returns it
    assert identity.expected_rules({"symbol": "VIX", "market": "Cross-asset", "kind": "macro_proxy"})[0][2] == "IND"


def _edit(directory: Path, group: str, symbol: str, **changes):
    path = directory / f"{group}.json"
    payload = json.loads(path.read_text())
    for row in payload["instruments"]:
        if row["symbol"] == symbol:
            row.update(changes)
    path.write_text(json.dumps(payload))


def test_doctored_snapshot_fails(tmp_path):
    u = _universe()
    copy = tmp_path / "snap"
    shutil.copytree(SNAP, copy)
    _edit(copy, "sg", "D05", exchange="NASDAQ")                    # wrong venue
    _edit(copy, "uk", "HSBA", currency="USD")                      # wrong currency for LSE
    _edit(copy, "us_sector_macro", "GBP.USD", security_type="STK") # FX must be CASH
    _edit(copy, "hk_cn", "600519", exchange="SEHK", currency="CNY")  # SEHK line priced in CNY
    _edit(copy, "us_equity", "NVDA", contract_id=999)             # contract changed vs the real snapshot
    _edit(copy, "us_equity", "MSFT", contract_id=4815747)         # changed (NVDA moved to 999, so not shared)
    payload = json.loads((copy / "credit.json").read_text())
    payload["instruments"].append({**payload["instruments"][0], "symbol": "JNK", "contract_id": 1})
    payload["instruments"].append(dict(payload["instruments"][0]))  # duplicate HYG row
    (copy / "credit.json").write_text(json.dumps(payload))
    _edit(copy, "uk", "LLOY", exchange="LSE")                     # unchanged: no issue
    issues = identity.check(copy, u, previous_dir=SNAP)
    got = {(i["symbol"], i["issue"]) for i in issues}
    assert ("D05", "exchange NASDAQ not an expected venue (SGX)") in got
    assert ("HSBA", "currency USD on LSE, expected GBP") in got
    assert ("GBP.USD", "security_type STK, expected CASH") in got
    assert ("600519", "currency CNY on SEHK, expected HKD, not returned") in got
    assert ("NVDA", "contract_id changed: 4815747 -> 999") in got
    assert ("MSFT", "contract_id changed: 272093 -> 4815747") in got
    assert ("JNK", "not in the universe (credit.json)") in got
    assert ("HYG", "duplicate row in credit.json (first in credit.json)") in got
    assert {s for s, _ in got} == {"D05", "HSBA", "GBP.USD", "600519", "NVDA", "MSFT", "JNK", "HYG"}


def test_shared_contract_id_and_wrong_group_file(tmp_path):
    u = _universe()
    copy = tmp_path / "snap"
    shutil.copytree(SNAP, copy)
    _edit(copy, "us_equity", "MSFT", contract_id=4815747)          # NVDA's id
    sg = json.loads((copy / "sg.json").read_text())
    uk = json.loads((copy / "uk.json").read_text())
    moved = [r for r in uk["instruments"] if r["symbol"] == "REL"]
    uk["instruments"] = [r for r in uk["instruments"] if r["symbol"] != "REL"]
    sg["instruments"] += moved
    (copy / "sg.json").write_text(json.dumps(sg))
    (copy / "uk.json").write_text(json.dumps(uk))
    got = {(i["symbol"], i["issue"]) for i in identity.check(copy, u)}
    assert got == {("MSFT", "contract_id 4815747 also used by NVDA"),
                   ("REL", "filed in sg.json, universe group is uk")}
