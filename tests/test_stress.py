import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from global_investment_research import data, stress as S

ROOT = Path(__file__).resolve().parent.parent
NAN = np.nan

# Weekly Friday closes. E1 runs Fri 10 Jan -> Fri 24 Jan; E2 starts on a Monday (13 Jan), so its start close
# is Fri 10 Jan.
DATES = pd.to_datetime(["2020-01-03", "2020-01-10", "2020-01-17", "2020-01-24", "2020-01-31"])
EPS = ({"name": "E1", "start": "2020-01-10", "end": "2020-01-24"},
       {"name": "E2", "start": "2020-01-13", "end": "2020-01-31"})


def frame():
    return pd.DataFrame({
        "AAA": [100, 100, 90, 80, 75],            # USD
        "BBB": [NAN, 50, 45, 40, 45],             # SGD
        "CCC": [20, NAN, NAN, 22, 24],            # USD, gap: no close near 10 Jan
        "DDD": [10, 10, 10, 11, 11],              # EUR, via the direct EUR.GBP pair
        "HHH": [5, 5, 5, 4, 4],                   # HKD, but no USD.HKD pair in this history
        "GBP.USD": [1.25, 1.25, 1.25, 1.0, 1.0],
        "USD.SGD": [1.25, 1.25, 1.25, 1.0, 1.25],
        "EUR.GBP": [0.9, 0.9, 0.9, 0.99, 0.99],
    }, index=DATES, dtype=float)


CCY = {"AAA": "USD", "BBB": "SGD", "CCC": "USD", "DDD": "EUR", "HHH": "HKD"}


def test_episode_returns_close_on_or_just_before_and_never_filled():
    r = S.episode_returns(frame(), EPS)
    assert r.at["AAA", "E1"] == pytest.approx(80 / 100 - 1)          # -20%
    assert r.at["AAA", "E2"] == pytest.approx(75 / 100 - 1)          # Monday start uses Friday 10 Jan close
    assert r.at["BBB", "E1"] == pytest.approx(40 / 50 - 1)
    assert r.at["CCC", "E1"] == pytest.approx(22 / 20 - 1)          # last close 3 Jan is fine (7 days) ...
    assert r.at["CCC", "E2"] is None                                 # ... but 10 days before 13 Jan is not
    detail = {(d["instrument"], d["episode"]): d for d in S.episode_detail(frame(), EPS)}
    assert detail[("AAA", "E2")]["start_used"] == "2020-01-10"
    assert "more than 7 days" in detail[("CCC", "E2")]["reason"]


def test_episode_gap_limit():
    # CCC E1: start close is 3 Jan, exactly 7 days before 10 Jan: allowed at 7, refused at 6.
    r7 = S.episode_detail(frame()[["CCC"]], EPS[:1], max_gap_days=7)[0]
    r6 = S.episode_detail(frame()[["CCC"]], EPS[:1], max_gap_days=6)[0]
    assert r7["return"] == pytest.approx(22 / 20 - 1) and r6["return"] is None


def test_to_base_returns_uses_fx_direction_and_crosses():
    local = S.episode_returns(frame(), EPS)
    base, notes = S.to_base_returns(local, local, CCY, "GBP")
    # USD asset, GBP.USD 1.25 -> 1.0: one USD is worth 1.25x more GBP. 0.8 x 1.25 - 1 = 0.
    assert base.at["AAA", "E1"] == pytest.approx(0.0)
    assert base.at["AAA", "E2"] == pytest.approx(0.75 * 1.25 - 1)
    # SGD asset via USD: SGD->USD gross 1/0.8, USD->GBP gross 1/0.8 -> 1.5625.
    assert base.at["BBB", "E1"] == pytest.approx(0.8 * 1.5625 - 1)
    assert base.at["BBB", "E2"] == pytest.approx(0.9 * 1.25 - 1)     # USD.SGD unchanged over E2
    # EUR asset via the direct EUR.GBP pair: 1.1 x 1.1 - 1.
    assert base.at["DDD", "E1"] == pytest.approx(0.21)
    assert base.at["HHH", "E1"] is None
    assert any(n["instrument"] == "HHH" and "HKD/USD" in n["reason"] for n in notes)
    assert base.at["CCC", "E2"] is None                              # no local return stays None, no note
    assert base.at["CCC", "E1"] == pytest.approx(1.1 * 1.25 - 1)


def test_value_in_levels_and_missing_pair():
    q = {"GBP.USD": 1.3, "USD.SGD": 1.35, "EUR.GBP": 0.85}
    assert S.value_in("GBP", "SGD", q)[0] == pytest.approx(1.3 * 1.35)
    assert S.value_in("SGD", "GBP", q)[0] == pytest.approx(1 / (1.3 * 1.35))
    assert S.value_in("EUR", "GBP", q) == (0.85, "EUR.GBP")
    assert S.value_in("CNH", "GBP", q)[0] is None


def test_units_per_base_takes_last_complete_date():
    p = frame()
    assert S.units_per_base(p, "SGD") == (pytest.approx(1.0 * 1.25), "2020-01-31")
    p.loc[DATES[-1], "USD.SGD"] = NAN
    assert S.units_per_base(p, "SGD") == (pytest.approx(1.0 * 1.0), "2020-01-24")


def base_frame():
    cols = ["E1", "E2"]
    rows = {"SPY": [-0.3, -0.1], "AAA": [-0.5, -0.2], "NEW": [None, 0.05], "OIL": [None, None]}
    return pd.DataFrame([rows[k] for k in rows], index=list(rows), columns=cols, dtype=object)


MARKET = {"SPY": "US", "AAA": "US", "NEW": "US", "OIL": "Cross-asset"}


def test_portfolio_stress_without_proxies_reports_gap_and_withholds_verdict():
    e1, e2 = S.portfolio_stress({"AAA": 0.5, "NEW": 0.2}, base_frame(), proxies=False, tolerance=0.2)
    assert e1["loss"] == pytest.approx(-0.25) and e1["covered_weight"] == pytest.approx(0.5)
    assert e1["cash_weight"] == pytest.approx(0.3)
    assert [m["instrument"] for m in e1["missing"]] == ["NEW"] and e1["breaches_tolerance"] is None
    assert e2["loss"] == pytest.approx(0.5 * -0.2 + 0.2 * 0.05) and e2["breaches_tolerance"] is False


def test_portfolio_stress_with_proxies_lists_every_proxied_name():
    e1, _ = S.portfolio_stress({"AAA": 0.5, "NEW": 0.2}, base_frame(), proxies=True, market_of=MARKET,
                               tolerance=0.2)
    assert e1["proxied"] == [{"instrument": "NEW", "proxy": "SPY", "weight": 0.2}]
    assert e1["loss"] == pytest.approx(-0.25 + 0.2 * -0.3)              # -31%
    assert e1["complete"] and e1["breaches_tolerance"] is True
    # A cross-asset name has no market proxy: still missing.
    e1, _ = S.portfolio_stress({"OIL": 0.1}, base_frame(), proxies=True, market_of=MARKET, tolerance=0.2)
    assert e1["missing"][0]["instrument"] == "OIL" and e1["loss"] == 0


def test_portfolio_stress_guards():
    with pytest.raises(ValueError):
        S.portfolio_stress({"AAA": 0.8, "SPY": 0.3}, base_frame(), tolerance=0.2)
    with pytest.raises(ValueError):
        S.portfolio_stress({"AAA": 0.5}, base_frame(), proxies=True, tolerance=0.2)
    with pytest.raises(ValueError):
        S.portfolio_stress({"AAA": -0.1}, base_frame(), tolerance=0.2)


def test_weights_from_calls_and_desk_check_scales():
    calls = [{"symbol": "AAA", "stance": "Accumulate", "size": 0.4},
             {"symbol": "NEW", "stance": "Hold", "size": 0.2},
             {"symbol": "SPY", "stance": "Watch", "size": None},
             {"symbol": "OIL", "stance": "Avoid", "size": None}]
    assert S.weights_from_calls(calls, 0.5) == {"AAA": 0.2, "NEW": 0.1}
    full, half = S.desk_check(calls, base_frame(), MARKET, tolerance=0.2)
    assert full["results"][0]["loss"] == pytest.approx(0.4 * -0.5 + 0.2 * -0.3)
    assert half["results"][0]["loss"] == pytest.approx(full["results"][0]["loss"] / 2)


def test_mandate_is_gbp_20pct():
    m = S.load_mandate()
    assert m["base_currency"] == "GBP" and m["drawdown_tolerance"] == 0.20
    assert m["sg_yield_band"] == [0.06, 0.08]


def test_currencies_from_meta_and_venue():
    ccy, notes = S.currencies({"A": {"currency": "usd"}, "B": {"currency": None, "exchange": "SEHK"},
                               "C": {"currency": None, "exchange": "SEHKNTL"}, "D": {"exchange": "XYZ"}})
    assert ccy == {"A": "USD", "B": "HKD", "C": "CNH", "D": None}
    assert len(notes) == 3 and "not mapped" in notes[-1]["reason"]


def test_clean_history_blanks_bad_print_and_lookup_falls_back_one_week():
    p = frame()[["AAA"]].copy()
    p.loc[DATES[3], "AAA"] = 0.8                                       # isolated unit error on 24 Jan
    clean, fixes = S.clean_history(p)
    assert fixes and fixes[0]["date"] == "2020-01-24"
    assert S.episode_returns(clean, EPS).at["AAA", "E1"] == pytest.approx(90 / 100 - 1)  # 17 Jan close


def test_sg_income_stress_worked_example():
    r = S.sg_income_stress(100_000, 0.3, 0.06, 0.2, -0.25, 0.08)
    assert r["capital_before"] == pytest.approx(30_000)
    assert r["income_before"] == pytest.approx(1_800)
    assert r["income_after"] == pytest.approx(30_000 * 0.06 * 0.8 * 1.08)   # 1,555.20
    assert r["capital_after"] == pytest.approx(30_000 * 0.75 * 1.08)        # 24,300
    assert r["capital_change"] == pytest.approx(-5_700)
    assert r["portfolio_capital_change"] == pytest.approx(-0.057)
    with pytest.raises(ValueError):
        S.sg_income_stress(100_000, 0.3, 0.06, 1.2, 0, 0)
    with pytest.raises(ValueError):
        S.sg_income_stress(100_000, 0.3, 0.06, 0.1, None, 0)


def test_sg_basket_uses_measured_yields_only():
    calls = [{"symbol": "A", "market": "SG", "stance": "Watch", "income": {"price": 2.0, "yield_sustainable": 0.05}},
             {"symbol": "B", "market": "SG", "stance": "Avoid", "income": {"price": 1.0, "yield_sustainable": 0.07}},
             {"symbol": "C", "market": "SG", "stance": "Watch"},
             {"symbol": "D", "market": "US", "stance": "Accumulate", "income": {"price": 9, "yield_sustainable": 0.1}}]
    b = S.sg_basket(calls, 1.7, band=[0.06, 0.08])
    assert b["basket_yield"] == pytest.approx(0.06)
    a = b["members"][0]
    assert a["cash_sgd"] == pytest.approx(0.1) and a["cash_gbp"] == pytest.approx(0.1 / 1.7)
    assert a["price_gbp"] == pytest.approx(2.0 / 1.7)
    assert b["in_band_count"] == 1 and b["excluded"][0]["symbol"] == "C"
    assert S.sg_basket(calls, 1.7, stances=("Watch",), band=[0.06, 0.08])["basket_yield"] == pytest.approx(0.05)


def test_sg_shocks_measured_from_history():
    p = frame().rename(columns={"BBB": "ES3"})
    local = S.episode_returns(p, EPS)
    e1, e2 = S.sg_shocks(local)
    assert e1["price_change"] == pytest.approx(-0.2) and e1["fx_change"] == pytest.approx(1.5625 - 1)
    assert e2["fx_change"] == pytest.approx(0.25)
    gone = S.sg_shocks(local.drop(index="USD.SGD"))[0]
    assert gone["fx_change"] is None and "SGD/USD" in gone["reason"]


def test_episodes_are_ordered_and_documented():
    for ep in S.EPISODES:
        assert pd.Timestamp(ep["start"]) < pd.Timestamp(ep["end"]) and len(ep["why"]) > 40


HIST = ROOT / "data" / "history" / "weekly_long"


@pytest.mark.skipif(not HIST.exists(), reason="long weekly history not present")
def test_real_history_gfc_spy_loss_in_range():
    prices, meta = data.load_ibkr_snapshot(HIST, data.load_universe())
    r = S.episode_returns(prices[["SPY"]], S.EPISODES)
    gfc = r.at["SPY", "Global financial crisis"]
    assert -0.60 < gfc < -0.40
    out = S.base_episode_returns(prices, meta)
    # GBP fell against USD in the GFC, so the GBP loss on SPY is smaller than the USD loss.
    assert out["base"].at["SPY", "Global financial crisis"] > gfc
    dec = ROOT / "reports" / "2026-10-11" / "decisions.json"
    if dec.exists():
        calls = json.loads(dec.read_text())["calls"]
        market = {s: m.get("market") for s, m in meta.items()}
        for blk in S.desk_check(calls, out["base"], market):
            assert len(blk["results"]) == len(S.EPISODES)


def test_weekly_summary_on_real_history():
    import json
    from pathlib import Path
    from global_investment_research import data, stress as S
    hist = Path("data/history/weekly_long")
    rep = Path("reports/2026-10-11/decisions.json")
    if not hist.exists() or not rep.exists():
        pytest.skip("real history not present")
    prices, meta = data.load_ibkr_snapshot(hist, data.load_universe())
    calls = json.loads(rep.read_text())["calls"]
    s = S.summary(calls, prices, meta, json.loads(Path("config/data-exceptions.json").read_text())["exceptions"])
    assert [r["episode"] for r in s["episodes"]] == [e["name"] for e in S.EPISODES]
    for r in s["episodes"]:
        assert r["half"] == pytest.approx(r["full"] / 2)          # cash earns 0, so half size halves the loss
        assert (r["breach_full"] is None) == (not r["complete"])  # no verdict with uncovered weight
    assert s["base_currency"] == "GBP" and s["sg_income"]["names"] > 0
