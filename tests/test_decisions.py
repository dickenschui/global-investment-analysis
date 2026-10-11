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
    assert c["size"] == pytest.approx(min(D.RISK_PER_POSITION / (110 / 97 - 1 + D.GAP_ALLOWANCE), D.MAX_POSITION))
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
    for f in ("brief.html", "index.html"):
        assert "<title>" in (tmp_path / f).read_text()


def test_workbook_formulas_reference_policy(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook
    from global_investment_research.workbook import build_workbook
    px, meta = synthetic(load_universe())
    dec = D.build_decisions(build_monitor(px, meta), meta)
    wb = load_workbook(build_workbook(dec, tmp_path / "b.xlsx"))
    ds = wb["Decisions"]
    assert ds["O2"].value.startswith("=IF(AND(F2=\"Accumulate\"") and "Policy!$B$4" in ds["O2"].value
    assert ds["P2"].value == "=IF(O2=\"\",\"\",O2*Policy!$B$3*Policy!$B$2)"
    assert wb["Policy"]["B2"].fill.fgColor.rgb.endswith("FFFF00")  # user input highlighted


def test_sustainable_yield_discounts_a_special_and_flags_cuts():
    import pandas as pd
    from global_investment_research.income import distribution_history, sustainable_yield
    asof = pd.Timestamp("2026-10-09")
    ca = [{"type": "CashDividends", "date": d, "value": v} for d, v in
          [("20260801", "0.05"), ("20260201", "0.05"), ("20250801", "0.03"), ("20250201", "0.03"),
           ("20240801", "0.03"), ("20240201", "0.03"), ("20230801", "0.03"), ("20230201", "0.03")]]
    h = distribution_history(ca, asof)
    assert [round(x, 4) for x in h["prior_12m"]] == [0.06, 0.06, 0.06]
    out = sustainable_yield({"yield": 0.10, "trailing_cash": 0.10}, h)   # last year paid 0.10 vs 0.06 before
    assert out["yield_sustainable"] < 0.10 and "special or timing" in out["history_flag"]
    cut_ca = [dict(a, value="0.02") if a["date"].startswith("2026") else a for a in ca]   # last two payments cut
    cut = sustainable_yield({"yield": 0.04, "trailing_cash": 0.04}, distribution_history(cut_ca, asof))
    assert cut["yield_sustainable"] <= 0.04 + 1e-12 and "falling" in cut["history_flag"]
    # an ex-date that slipped past the 365-day window: the 12-month sum holds one payment, the payer pays twice
    slip = [a for a in ca if a["date"] != "20251001"] + [{"type": "CashDividends", "date": "20251008", "value": "0.03"}]
    slip = [a for a in slip if a["date"] != "20260801"] + [{"type": "CashDividends", "date": "20261012", "value": "0.03"}]
    hs = distribution_history(slip, asof)
    assert hs["payments_per_year"] == 2
    assert distribution_history(ca[:2], asof) is None                     # no multi-year record: say so, never guess


def test_distribution_components_on_one_ex_date_are_one_payment():
    import pandas as pd
    from global_investment_research.income import distribution_history
    asof = pd.Timestamp("2026-10-09")
    ca = []
    for y in (2022, 2023, 2024, 2025, 2026):
        for m in ("02", "05", "08", "11"):
            if f"{y}{m}01" <= "20261009":
                ca += [{"type": "CashDividends", "date": f"{y}{m}01", "value": "0.007", "currency": "SGD"},
                       {"type": "CashDividends", "date": f"{y}{m}01", "value": "0.011", "currency": "SGD"}]
    h = distribution_history(ca, asof, currency="SGD")
    assert h["payments_per_year"] == 4                       # quarterly payer, two components each
    assert round(h["last_n_cash"], 6) == round(4 * 0.018, 6)
