import json
from pathlib import Path

import pytest

from global_investment_research import options as O, payoff as P

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("structure,legs,debit", [
    ("Bull call spread", [(1, "C", 100), (-1, "C", 110)], 4.0),
    ("Bear put spread", [(1, "P", 100), (-1, "P", 90)], 3.0),
    ("Long call", [(1, "C", 105)], 2.5),
    ("Long put", [(1, "P", 95)], 2.0),
])
def test_agrees_with_options_analyse(structure, legs, debit):
    a = O.analyse(structure, legs, debit, 100, 0.25, 0.3)
    e = P.expiry_analysis(legs, debit)
    assert e["breakevens"] == [pytest.approx(a["breakeven"])]
    assert e["max_loss"] == pytest.approx(a["max_loss"] * 100)
    if a["max_gain"] is not None:
        assert e["max_profit"] == pytest.approx(a["max_gain"] * 100)
    for s in (0, 50, 90, 95, 100, 104, 110, 150):
        assert P.expiry_value(legs, s) == pytest.approx(O.payoff(legs, s))


def test_single_legs_where_analyse_is_open_ended():
    call = P.expiry_analysis([(1, "C", 105)], 2.5)
    assert call["profit_unbounded"] and call["max_profit"] is None and call["tail_slope"] == 100
    # analyse leaves a single put's gain open; at expiry it is bounded by the strike: (95 - 2) x 100.
    put = P.expiry_analysis([(1, "P", 95)], 2.0)
    assert put["max_profit"] == pytest.approx(9_300) and not put["profit_unbounded"]


def test_straddle_two_breakevens():
    e = P.expiry_analysis([(1, "P", 100), (1, "C", 100)], 8.0)
    assert e["breakevens"] == [92.0, 108.0] and e["max_loss"] == 800 and e["profit_unbounded"]


def test_short_call_unbounded_loss_and_credit():
    e = P.expiry_analysis([{"qty": -1, "right": "C", "strike": 100}], -3.0)
    assert e["loss_unbounded"] and e["max_loss"] is None
    assert e["max_profit"] == pytest.approx(300) and e["breakevens"] == [103.0]


def test_short_put_loss_bounded_at_zero_price():
    e = P.expiry_analysis([(-1, "P", 100)], -3.0)
    assert e["max_loss"] == pytest.approx((100 - 3) * 100) and e["breakevens"] == [97.0]


def test_iron_condor():
    legs = [(1, "P", 90), (-1, "P", 95), (-1, "C", 105), (1, "C", 110)]
    e = P.expiry_analysis(legs, -2.0)
    assert e["max_profit"] == pytest.approx(200) and e["max_loss"] == pytest.approx(300)
    assert e["breakevens"] == [93.0, 107.0] and e["tail_slope"] == 0


def test_zero_pnl_tail_interval_and_ratio_spread():
    e = P.expiry_analysis([(1, "C", 100), (-1, "C", 110)], 10.0)
    assert e["max_profit"] == 0 and e["zero_pnl_intervals"] == [[110.0, None]] and e["breakevens"] == [110.0]
    # 1x2 call ratio for a 1.00 credit: peak +1,100 at 110, loses beyond 121.
    r = P.expiry_analysis([(1, "C", 100), (-2, "C", 110)], -1.0)
    assert r["max_profit"] == pytest.approx(1_100) and r["loss_unbounded"] and r["breakevens"] == [121.0]


def test_multiplier_and_guards():
    assert P.expiry_analysis([(1, "C", 100)], 2.0, multiplier=10)["max_loss"] == pytest.approx(20)
    with pytest.raises(ValueError):
        P.expiry_analysis([(1, "X", 100)], 1.0)
    with pytest.raises(ValueError):
        P.expiry_analysis([(1, "C", -5)], 1.0)
    with pytest.raises(ValueError):
        P.expiry_analysis([(0, "C", 100)], 1.0)
    with pytest.raises(ValueError):
        P.expiry_analysis([], 1.0)
    with pytest.raises(ValueError):                    # overflow to infinity is refused
        P.expiry_analysis([(1e308, "C", 100)], 1.0)


def test_validate_chain_flags_each_issue():
    chain = {"retrieved_at_utc": "2026-10-11T12:00:00Z", "underlyings": [{"symbol": "AAA", "expiries": [
        {"expiry": "2026-12-18", "contracts": [
            {"right": "C", "strike": 100, "bid": 2.0, "ask": 2.2, "iv": 0.3},           # clean
            {"right": "C", "strike": 105, "bid": 2.5, "ask": 2.2, "iv": 0.3},           # crossed
            {"right": "C", "strike": 110, "bid": 0, "ask": 0.5, "iv": 0.3},             # zero bid
            {"right": "C", "strike": 115, "bid": None, "ask": 0.4, "iv": float("nan")}, # missing bid, NaN IV
            {"right": "C", "strike": 100, "bid": 2.0, "ask": 2.2, "iv": 0.3},           # duplicate
            {"right": "X", "strike": 0, "bid": 1.0, "ask": 1.1, "iv": -0.1},            # bad right/strike/IV
            {"right": "P", "strike": 90, "bid": 1.0, "ask": 1.1}]},                     # IV missing
        {"expiry": "2026-10-09", "contracts": [{"right": "P", "strike": 90, "bid": 1.0, "ask": 1.1, "iv": 0.3}]},
    ]}]}
    issues = P.validate_chain(chain)
    by = {}
    for i in issues:
        by.setdefault((i["expiry"], i["strike"], i["right"]), []).append(i["issue"])
    assert ("2026-12-18", 100, "C") in by and by[("2026-12-18", 100, "C")] == ["duplicate expiry/strike/right"]
    assert by[("2026-12-18", 105, "C")][0].startswith("crossed quote")
    assert by[("2026-12-18", 110, "C")] == ["ask quoted with zero or missing bid"]
    assert len(by[("2026-12-18", 115, "C")]) == 2
    assert len(by[("2026-12-18", 0, "X")]) == 3
    assert by[("2026-12-18", 90, "P")] == ["implied volatility missing"]
    assert by[("2026-10-09", 90, "P")] == ["expiry 2026-10-09 is before as-of 2026-10-11"]
    assert len(issues) == 10
    assert P.validate_chain(chain, as_of="2026-10-01").count(
        {"symbol": "AAA", "expiry": "2026-10-09", "strike": 90, "right": "P",
         "issue": "expiry 2026-10-09 is before as-of 2026-10-11"}) == 0


OPTIONS = ROOT / "data" / "snapshots" / "2026-10-11" / "options.json"


@pytest.mark.skipif(not OPTIONS.exists(), reason="options snapshot not present")
def test_validate_real_chain_returns_list():
    issues = P.validate_chain(json.loads(OPTIONS.read_text()))
    assert isinstance(issues, list)
    assert all(set(i) == {"symbol", "expiry", "strike", "right", "issue"} for i in issues)
