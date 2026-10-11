import json
import shutil
from pathlib import Path

import pytest

from global_investment_research import data, quality

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "data/snapshots/2026-10-11"


def _universe():
    return data.load_universe(ROOT / "config/universe.json")


def test_price_check_is_pence_aware():
    # AZN: snapshot 11998.0003 pence vs weekly close 120.24 pounds -> ratio 99.78, factor 100, gap -0.22%.
    azn = quality.price_check(11998.0003, 120.24, london=True)
    assert azn["factor"] == 100 and azn["gap"] == pytest.approx(11998.0003 / 12024 - 1) and not azn["fails"]
    assert quality.price_check(11998.0003, 120.24, london=False)["fails"]   # x100 is an error off London
    assert quality.price_check(120.24, 120.24, london=True)["gap"] == 0     # London line already in pounds
    gap = quality.price_check(111.0, 100.0, london=False)
    assert gap["gap"] == pytest.approx(0.11) and gap["fails"]
    assert not quality.price_check(109.0, 100.0, london=False)["fails"]     # 9% is within the 10% tolerance


def test_review_of_real_snapshot_first_week():
    rev = quality.review(SNAP, None, _universe())
    assert rev["as_of"] == "2026-10-11"
    assert (rev["with_prices"], rev["configured"], rev["coverage"]) == (98, 98, 1.0)
    assert rev["missing"] == [] and rev["stale"] == [] and rev["moves"] == [] and rev["mismatches"] == []
    assert rev["identity"] == []
    assert rev["manifest"]["status"] == "verified"
    assert rev["contract_changes"] is None and rev["dividend_changes"] is None
    assert len(rev["options"]["underlyings"]) == 11 and rev["options"]["contracts"] == 154
    assert rev["options"]["issues"] == []
    json.dumps(rev)                                                          # JSON-ready for the CLI
    md = quality.to_markdown(rev)
    assert "first week" in md and "manifest verified (7 files)" in md
    assert "98/98 instruments with prices (100.0%)" in md and "stale (last bar over 10 days old): none" in md


def _rows(path):
    return json.loads(path.read_text())


def test_review_of_doctored_copy(tmp_path):
    u = _universe()
    cur = tmp_path / "doctored" / "2026-10-11"
    shutil.copytree(SNAP, cur)

    us = _rows(cur / "us_equity.json")
    nvda = next(r for r in us["instruments"] if r["symbol"] == "NVDA")
    prev_close = nvda["weekly"]["close"][-2]
    nvda["weekly"]["close"][-1] = round(prev_close * 1.40, 2)                # +40% in a week, snapshot last left
    (cur / "us_equity.json").write_text(json.dumps(us))

    sec = _rows(cur / "us_sector_macro.json")
    sec["instruments"] = [r for r in sec["instruments"] if r["symbol"] != "XLB"]   # instrument dropped
    (cur / "us_sector_macro.json").write_text(json.dumps(sec))

    sg = _rows(cur / "sg.json")
    d05 = next(r for r in sg["instruments"] if r["symbol"] == "D05")
    d05["contract_id"] = 123456                                               # contract changed
    removed = d05["dividends"].pop()                                          # a dividend record vanished
    es3 = next(r for r in sg["instruments"] if r["symbol"] == "ES3")
    es3["weekly"]["time"], es3["weekly"]["close"] = es3["weekly"]["time"][:-1], es3["weekly"]["close"][:-1]
    (cur / "sg.json").write_text(json.dumps(sg))

    rev = quality.review(cur, SNAP, u)
    assert rev["as_of"] == "2026-10-11"
    assert (rev["with_prices"], rev["configured"]) == (97, 98)
    assert rev["coverage"] == pytest.approx(97 / 98)
    assert rev["missing"] == [{"symbol": "XLB", "reason": "no row in any snapshot file"}]

    [move] = rev["moves"]
    assert move["symbol"] == "NVDA" and move["from_close"] == prev_close
    assert move["move"] == pytest.approx(round(prev_close * 1.40, 2) / prev_close - 1)

    [mm] = rev["mismatches"]                                                  # 229.33 vs the doctored close
    assert mm["symbol"] == "NVDA" and mm["factor"] == 1
    assert mm["gap"] == pytest.approx(229.33 / round(prev_close * 1.40, 2) - 1)

    # ES3 lost its last bar, so its latest is stamped 2026-09-28: 13 days before 2026-10-11 (> 10).
    assert rev["stale"] == [{"symbol": "ES3", "last_bar": "2026-09-28", "age_days": 13}]

    assert rev["contract_changes"] == [{"symbol": "D05", "previous": 15785538, "current": 123456}]
    [div] = rev["dividend_changes"]
    assert div["symbol"] == "D05" and div["added"] == []
    assert div["removed"] == [{"ex_date": removed["ex_date"], "amount": removed["amount"],
                               "currency": removed["currency"], "type": removed["type"]}]
    assert {"symbol": "D05", "issue": "contract_id changed: 15785538 -> 123456"} in rev["identity"]
    assert rev["manifest"]["status"] == "failed"
    changed = {p.split()[1] for p in rev["manifest"]["problems"]}
    assert changed == {"sg.json", "us_equity.json", "us_sector_macro.json"}
    assert all(p.startswith("changed: ") for p in rev["manifest"]["problems"])
    assert rev["options"]["issues"] == []

    md = quality.to_markdown(rev)
    assert "(vs 2026-10-11)" in md and "manifest FAILED" in md
    assert "D05 15785538 -> 123456" in md and "XLB (no row in any snapshot file)" in md
    assert "NVDA" in md and "ES3" in md and "97/98 instruments with prices" in md
