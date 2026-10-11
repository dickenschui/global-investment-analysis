import pytest

from global_investment_research import options as O


def test_black_scholes_known_values():
    # Hull textbook values: S=K=100, T=1, r=5%, sigma=20%
    assert O.bs_price(100, 100, 1, 0.2, "C", r=0.05) == pytest.approx(10.4506, abs=1e-4)
    assert O.bs_price(100, 100, 1, 0.2, "P", r=0.05) == pytest.approx(5.5735, abs=1e-4)
    # put-call parity
    c, p = O.bs_price(120, 100, 0.5, 0.3, "C"), O.bs_price(120, 100, 0.5, 0.3, "P")
    assert c - p == pytest.approx(120 - 100 * pytest.importorskip("math").exp(-O.RISK_FREE * 0.5))


def test_quote_mid_rejects_wide_or_empty():
    assert O.quote_mid({"bid": 1.0, "ask": 1.1})[0] == pytest.approx(1.05)
    assert O.quote_mid({"bid": 0.5, "ask": 1.5})[0] is None
    assert O.quote_mid({"bid": None, "ask": None, "last": 2.0}) == (2.0, "last trade (no two-sided quote)")
    assert O.quote_mid({})[0] is None


def test_spread_analytics():
    a = O.analyse("Bull call spread", [(1, "C", 100), (-1, "C", 110)], 4.0, 100, 0.25, 0.3)
    assert a["breakeven"] == 104 and a["max_gain"] == 6 and a["reward_risk"] == pytest.approx(1.5)
    assert a["scenarios"]["+20%"] == pytest.approx(1.5)   # capped at width
    assert a["scenarios"]["-10%"] == pytest.approx(-1.0)  # loses whole debit
    assert 0 < a["prob_profit"] < 0.5
    p = O.analyse("Bear put spread", [(1, "P", 100), (-1, "P", 90)], 3.0, 100, 0.25, 0.3)
    assert p["breakeven"] == 97 and p["scenarios"]["-20%"] == pytest.approx(7 / 3)


def test_ideas_follow_desk_and_size_to_budget():
    chain = {"underlyings": [{"symbol": "AAA", "last": 100.0, "expiries": [{"expiry": "2026-12-18", "contracts": [
        {"right": "C", "strike": k, "bid": b, "ask": b * 1.05, "iv": 0.30, "open_interest": 500}
        for k, b in [(100, 6.0), (105, 3.8), (110, 2.3)]]}]},
        {"symbol": "BBB", "last": 50.0, "expiries": [{"expiry": "2026-12-18", "contracts": []}]}]}
    dec = {"calls": [{"symbol": "AAA", "stance": "Accumulate"}, {"symbol": "BBB", "stance": "Watch"}]}
    out = O.build_ideas(chain, dec, "2026-10-09", portfolio=1_000_000)
    names = {i["structure"] for i in out["ideas"]}
    assert names == {"Bull call spread", "Long call"}
    for i in out["ideas"]:
        assert i["premium_at_risk"] <= O.PER_TRADE_RISK * 1_000_000
    assert any(s["symbol"] == "BBB" for s in out["skipped"])
    from global_investment_research.options_desk import options_page
    html = options_page(out)
    assert "<title>Speculative Options Sleeve</title>" in html and "svg" in html and "Defined risk only" in html
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook
    from global_investment_research.workbook import build_workbook
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "b.xlsx"
    build_workbook({"as_of": "2026-10-09", "posture": {"risk_budget": 1, "risk_note": "", "tilts": []}, "calls": [], "fx": []},
                   p, opt=out)
    ws = load_workbook(p)["Options"]
    assert ws["O2"].value == "=INT(Policy!$B$2*Policy!$B$11/H2)"
