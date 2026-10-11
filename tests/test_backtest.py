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
