import pandas as pd
import pytest

from global_investment_research import decisions as D
from global_investment_research import handbook
from global_investment_research.cli import main, synthetic
from global_investment_research.data import load_universe
from global_investment_research.monitor import build_monitor


def metrics(state, last=110.0, short=105.0, long=100.0, mom=0.8):
    return {"last": last, "momentum_score": mom,
            "trend": {"state": state, "short_ma": short, "long_ma": long, "pct_vs_long_ma": last / long - 1}}


def test_stance_rules():
    assert D.equity_call(metrics("up"))["stance"] == "Accumulate"
    assert D.equity_call(metrics("up", mom=0.2))["stance"] == "Hold"
    assert D.equity_call(metrics("up", last=130))["stance"] == "Hold"  # >20% above 40w: do not chase
    assert D.equity_call(metrics("up"), regime_stressed=True)["stance"] == "Hold"
    assert D.equity_call(metrics("mixed"))["stance"] == "Watch"
    assert D.equity_call(metrics("down", last=90))["stance"] == "Avoid"


def test_sizing_and_levels():
    c = D.equity_call(metrics("up"))  # stop = 97, distance = 110/97-1
    assert c["stop"] == pytest.approx(97.0)
    assert c["size"] == pytest.approx(min(D.RISK_PER_POSITION / (110 / 97 - 1), D.MAX_POSITION))
    assert c["entry"] == (105.0, 110.0)
    tight = D.equity_call(metrics("up", last=100.5, short=100.2, long=100.0))
    assert tight["size"] == D.MAX_POSITION  # cap binds when stop is close
    w = D.equity_call(metrics("down", last=90, short=95, long=100))
    assert w["stop"] is None and w["size"] is None and w["trigger"] == 100


def test_income_call_band_and_staging():
    m_down = {"trend": {"state": "down"}}
    row = {"yield": 0.07, "trailing_cash": 0.07, "price": 1.0}
    c = D.income_call(row, m_down)
    assert c["stance"] == "Accumulate" and "stage" in c["rule"]
    assert c["price_for_8pct"] == pytest.approx(0.875)
    assert D.income_call({**row, "yield": 0.09}, m_down)["stance"] == "Watch"
    assert D.income_call({**row, "yield": 0.04}, m_down)["stance"] == "Avoid"


def test_posture_cites_its_reasons():
    p = D.macro_posture([{"topic": "Risk appetite", "reading": "Volatility stressed"},
                         {"topic": "Rates", "reading": "Long yields rising (bond prices down-trend)"}])
    assert p["risk_budget"] == 0.5 and p["stressed"]
    assert all(t["because"] for t in p["tilts"])


def test_review_log_verdicts(tmp_path):
    idx = pd.date_range("2026-09-04", periods=4, freq="W-FRI")
    px = pd.DataFrame({"AAA": [100, 95, 105, 110.0], "BBB": [100, 90, 80, 70.0]}, index=idx)
    log = tmp_path / "log.csv"
    log.write_text("date,symbol,decision,price,size_pct,exit_level,assumption,counter_case,review_date\n"
                   "2026-09-04,AAA,Accumulate,100,0.05,90,x,y,2026-12-01\n"
                   "2026-09-04,BBB,Accumulate,100,0.05,85,x,y,2026-12-01\n"
                   "2026-09-04,BBB,Avoid,100,,,x,y,2026-12-01\n")
    r = D.review_log(log, px)
    assert r[0]["verdict"] == "working" and r[0]["return"] == pytest.approx(0.10)
    assert r[1]["exit_breached"] and r[1]["verdict"].startswith("exit level hit")
    assert r[2]["verdict"] == "avoided a loss"


def test_handbook_uses_engine_policy():
    md = handbook.to_markdown()
    assert f"{D.RISK_PER_POSITION*100:.1f}% of portfolio" in md
    assert "<title>Investment Handbook</title>" in handbook.to_html()


def test_desk_page_end_to_end(tmp_path):
    assert main(["demo", "--out", str(tmp_path)]) == 0
    html = (tmp_path / "decision-desk.html").read_text()
    assert "<title>Decision Desk</title>" in html and 'id="pv"' in html
    dec = D.build_decisions(build_monitor(*synthetic(load_universe())), synthetic(load_universe())[1])
    assert {c["stance"] for c in dec["calls"]} <= set(D.STANCE_ORDER)
