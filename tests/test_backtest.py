import numpy as np
import pandas as pd
import pytest

from global_investment_research import backtest as B


def make_prices(n=260, seed=3):
    idx = pd.date_range("2021-10-08", periods=n, freq="W-FRI")
    rng = np.random.default_rng(seed)
    trendy = 100 * np.cumprod(1 + 0.004 + rng.normal(0, 0.02, n))      # persistent up-trend
    falling = 100 * np.cumprod(1 - 0.004 + rng.normal(0, 0.02, n))     # persistent down-trend
    noise = 100 * np.cumprod(1 + rng.normal(0, 0.02, n))
    return pd.DataFrame({"UP": trendy, "DOWN": falling, "NOISE": noise}, index=idx)


def test_no_lookahead_in_stances():
    px = make_prices()
    base = B.stance_panel(px)
    shocked = px.copy()
    shocked.iloc[200:] *= 5  # change only the future
    alt = B.stance_panel(shocked)
    a = base[base["date"] < px.index[200]].set_index(["date", "symbol"])["stance"]
    b = alt[alt["date"] < px.index[200]].set_index(["date", "symbol"])["stance"]
    assert (a == b).all()


def test_forward_returns_and_excess_are_correct():
    px = make_prices()
    df = B.stance_panel(px)
    row = df[(df["symbol"] == "UP")].iloc[0]
    s = px["UP"]
    i = s.index.get_loc(row["date"])
    assert row["fwd13"] == pytest.approx(s.iloc[i + 13] / s.iloc[i] - 1)
    same = df[df["date"] == row["date"]]
    assert same["xs13"].sum() == pytest.approx(0, abs=1e-12)  # excess is cross-sectionally demeaned


def test_trend_rules_rank_persistent_trends():
    res = B.run(make_prices())
    acc, avo = res["by_stance"]["Accumulate"]["xs13"], res["by_stance"]["Avoid"]["xs13"]
    assert acc["n"] > 0 and avo["n"] > 0
    assert acc["mean"] > avo["mean"]           # synthetic persistent trends must be detected
    assert res["strategy"]["weeks"] > 100
    assert 0 <= res["strategy"]["avg_invested"] <= 1


def test_evidence_page_renders():
    from global_investment_research.evidence import evidence_page
    html = evidence_page(B.run(make_prices()))
    assert "<title>Rule Evidence</title>" in html and "svg" in html and "Survivorship" in html


def test_isolated_bad_print_is_dropped_not_smoothed():
    px = make_prices()
    good = px["UP"].iloc[100]
    px.iloc[100, px.columns.get_loc("UP")] = good / 100  # pence/pound style unit error
    clean, fixes = B.drop_bad_prints(px)
    assert len(fixes) == 1 and fixes[0]["symbol"] == "UP"
    assert np.isnan(clean["UP"].iloc[100])           # removed, never replaced with an estimate
    assert clean["UP"].drop(px.index[100]).equals(px["UP"].drop(px.index[100]))
    assert B.drop_bad_prints(make_prices())[1] == []  # genuine data untouched


def test_adoption_test_fields_and_confidence_requires_beating_benchmark():
    from global_investment_research.evidence import confidence_level
    res = B.run(make_prices())
    for x in res["variants"][:-1]:
        assert x["thirds_won"] in (0, 1, 2, 3) and len(x["sharpe_thirds"]) == 3
        assert x["passes_adoption_test"] == bool(x["beats_benchmark_both_halves"] and x["thirds_won"] == 3
                                                 and x["active_t"] is not None and x["active_t"] >= B.ADOPTION_T)
    good_ranking = dict(res, verdict=dict(res["verdict"], supported_both_halves=True, significant=True,
                                          strategy_beats_benchmark_risk_adjusted=False))
    assert confidence_level(good_ranking) == "LOW"   # ranking alone is not enough if following it loses to equal weight
    assert confidence_level(dict(good_ranking, verdict=dict(good_ranking["verdict"],
                                                            strategy_beats_benchmark_risk_adjusted=True))) == "HIGH"
