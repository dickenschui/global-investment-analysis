import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from global_investment_research import macro_sources as M

NOW = datetime(2026, 10, 12, 6, 30, tzinfo=timezone.utc)

FRED_CSV = b"observation_date,DGS10\n2026-10-07,4.10\n2026-10-08,.\n2026-10-09,4.15\n"
FRED_OLD_CSV = b"DATE,T10Y3M\n2026-10-08,0.25\n2026-10-09,\n"
ECB_CSV = (b"KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_CONF\n"
           b"EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-10-08,0.8612,A,F\n"
           b"EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-10-09,0.8630,A,F\n")
BOE_CSV = b"DATE,IUDBEDR\n06 Aug 2026,4.00\n07 Aug 2026,3.75\n"
ONS_JSON = json.dumps({
    "description": {"cdid": "L55O", "title": "CPIH ANNUAL RATE 00: ALL ITEMS 2015=100", "unit": "%"},
    "years": [{"date": "2025", "value": "3.9"}],
    "months": [
        {"date": "2026 JUL", "value": "3.6", "label": "2026 JUL", "year": "2026", "month": "July", "sourceDataset": "MM23"},
        {"date": "2026 AUG", "value": "3.8", "label": "2026 AUG", "year": "2026", "month": "August", "sourceDataset": "MM23"},
    ]}).encode()
UST_CSV = (b'Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
           b"10/09/2026,4.20,4.19,4.18,4.10,4.05,3.98,3.85,3.60,3.55,3.62,3.80,4.15,4.60,4.70\n"
           b"10/08/2026,4.21,4.20,4.19,4.11,4.06,3.99,3.86,3.61,3.56,3.63,3.81,,4.61,4.71\n")
WB_JSON = json.dumps([
    {"page": 1, "pages": 1, "per_page": 100, "total": 2, "sourceid": "2", "lastupdated": "2026-07-01"},
    [{"indicator": {"id": "NY.GDP.MKTP.KD.ZG", "value": "GDP growth (annual %)"}, "country": {"id": "SG", "value": "Singapore"},
      "countryiso3code": "SGP", "date": "2026", "value": None, "unit": "", "obs_status": "", "decimal": 1},
     {"indicator": {"id": "NY.GDP.MKTP.KD.ZG", "value": "GDP growth (annual %)"}, "country": {"id": "SG", "value": "Singapore"},
      "countryiso3code": "SGP", "date": "2025", "value": 4.4, "unit": "", "obs_status": "", "decimal": 1}]]).encode()


def test_fred_dot_is_missing_and_old_header_supported():
    obs = M.parse_fredgraph_csv(FRED_CSV, {"series_code": "DGS10"})
    assert obs == [{"date": "2026-10-07", "value": 4.10}, {"date": "2026-10-08", "value": None},
                   {"date": "2026-10-09", "value": 4.15}]
    assert M.parse_fredgraph_csv(FRED_OLD_CSV, {"series_code": "T10Y3M"})[1] == {"date": "2026-10-09", "value": None}
    with pytest.raises(ValueError):
        M.parse_fredgraph_csv(FRED_CSV, {"series_code": "DGS2"})


def test_ecb_checks_quote_direction():
    entry = {"expect": {"CURRENCY": "GBP", "CURRENCY_DENOM": "EUR"}}
    assert M.parse_ecb_csvdata(ECB_CSV, entry)[-1] == {"date": "2026-10-09", "value": 0.8630}
    with pytest.raises(ValueError, match="CURRENCY"):
        M.parse_ecb_csvdata(ECB_CSV, {"expect": {"CURRENCY": "USD"}})


def test_boe_ons_treasury_worldbank():
    assert M.parse_boe_iadb_csv(BOE_CSV, {"series_code": "IUDBEDR"}) == [
        {"date": "2026-08-06", "value": 4.0}, {"date": "2026-08-07", "value": 3.75}]
    assert M.parse_ons_timeseries_json(ONS_JSON, {"period": "months"}) == [
        {"date": "2026-07-01", "value": 3.6}, {"date": "2026-08-01", "value": 3.8}]
    ust = M.parse_treasury_par_curve_csv(UST_CSV, {"column": "10 Yr"})
    assert ust == [{"date": "2026-10-09", "value": 4.15}, {"date": "2026-10-08", "value": None}]
    assert M.parse_treasury_par_curve_csv(UST_CSV, {"column": "3 Mo"})[0]["value"] == 4.10
    wb = M.parse_worldbank_json(WB_JSON, {"series_code": "NY.GDP.MKTP.KD.ZG", "country": "SG"})
    assert wb == [{"date": "2026", "value": None}, {"date": "2025", "value": 4.4}]
    with pytest.raises(ValueError, match="API error"):
        M.parse_worldbank_json(b'[{"message":[{"id":"120","key":"Invalid value"}]}]', {"series_code": "x"})


def test_validate_rejects_duplicates_future_and_all_missing():
    asof = NOW.date()
    assert [o["date"] for o in M.validate([{"date": "2026-10-09", "value": 1.0}, {"date": "2026-10-08", "value": 2.0}], asof)] \
        == ["2026-10-08", "2026-10-09"]
    for bad in ([{"date": "2026-10-09", "value": 1.0}] * 2, [{"date": "2026-10-13", "value": 1.0}],
                [{"date": "2026-10-09", "value": None}], []):
        with pytest.raises(ValueError):
            M.validate(bad, asof)
    assert M.validate([{"date": "2026", "value": 1.0}], asof)  # annual period in the current year is not future


def test_resolve_url_placeholders():
    url = M.resolve_url("a?cosd={start_date}&d={boe_start}&y={year}", NOW, 10)
    assert url == "a?cosd=2026-10-02&d=02/Oct/2026&y=2026"


def test_shipped_config_is_complete():
    cfg = json.loads((Path(__file__).parents[1] / "config/macro-sources.json").read_text())
    ids = [s["id"] for s in cfg["series"]]
    assert len(ids) == len(set(ids))
    for s in cfg["series"]:
        assert s["format"] in M.PARSERS and isinstance(s["verified"], bool)   # true only after a clean live parse
        assert {"source", "url", "units", "frequency", "purpose"} <= set(s)
    assert "resource-center/data-chart-center" in next(s["url"] for s in cfg["series"] if s["id"] == "UST_PAR_10Y")
    for code in ["DGS3MO", "DGS2", "DGS10", "T10Y3M", "T10Y2Y", "BAMLH0A0HYM2", "CPIAUCSL", "UNRATE", "ICSA", "NFCI",
                 "DTWEXBGS", "DCOILBRENTEU", "BOE_IUDBEDR", "BOE_IUDMNZC", "ONS_L55O", "ECB_GBP_EUR", "WB_GDP_HK"]:
        assert code in ids
    assert {n["id"] for n in cfg["not_configured"]} >= {"HKMA_HIBOR", "MAS_SORA"}


def test_fetch_all_records_failures_and_shares_urls(tmp_path):
    cfg = {"lookback_days": 30, "not_configured": [{"id": "X", "reason": "r"}], "series": [
        {"id": "DGS10", "series_code": "DGS10", "format": "fredgraph_csv", "url": "https://fred/x?id=DGS10&cosd={start_date}"},
        {"id": "BAD", "series_code": "DGS2", "format": "fredgraph_csv", "url": "https://fred/x?id=DGS2"},
        {"id": "DOWN", "series_code": "IUDBEDR", "format": "boe_iadb_csv", "url": "https://boe/down"},
        {"id": "UST10", "column": "10 Yr", "format": "treasury_par_curve_csv", "url": "https://ust/{year}"},
        {"id": "UST3M", "column": "3 Mo", "format": "treasury_par_curve_csv", "url": "https://ust/{year}"},
    ]}
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if "down" in url:
            raise OSError("connection refused")
        if "ust" in url:
            return UST_CSV
        return FRED_CSV  # BAD expects DGS2 column -> parse failure

    res = M.fetch_all(cfg, opener=opener, timeout=5, now=NOW)
    assert calls.count("https://ust/2026") == 1 and "https://fred/x?id=DGS10&cosd=2026-09-12" in calls
    s = res["series"]
    assert s["DGS10"]["status"] == "ok" and s["DGS10"]["latest"] == {"date": "2026-10-09", "value": 4.15}
    assert s["DGS10"]["observations"][1]["value"] is None
    assert s["DGS10"]["sha256"] == __import__("hashlib").sha256(FRED_CSV).hexdigest()
    assert s["UST10"]["observations"][0]["date"] == "2026-10-08"  # sorted ascending
    assert s["UST3M"]["latest"]["value"] == 4.10
    assert {f["id"] for f in res["failures"]} == {"BAD", "DOWN"}
    assert s["BAD"]["error"].startswith("parse failed") and s["DOWN"]["error"].startswith("fetch failed")
    assert s["DOWN"]["observations"] == [] and "sha256" not in s["DOWN"]
    assert res["summary"] == {"configured": 5, "ok": 3, "failed": 2}

    paths = M.write_snapshot(res, tmp_path / "macro")
    assert paths["dated"].endswith("2026-10-12/macro.json")
    assert json.loads(Path(paths["dated"]).read_text()) == json.loads(Path(paths["latest"]).read_text())
    assert json.loads(Path(paths["latest"]).read_text())["retrieved_at"] == "2026-10-12T06:30:00Z"


def test_run_writes_snapshot(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"series": [{"id": "DGS10", "series_code": "DGS10", "format": "fredgraph_csv", "url": "u"}]}))
    out = M.run(cfg, tmp_path / "m", opener=lambda u, t: FRED_CSV, now=NOW)
    assert out["ok"] == 1 and out["failed"] == 0 and Path(out["paths"]["latest"]).exists()


# ---------------------------------------------------------------- readings
def ser(points, **meta):
    return {"status": "ok", "observations": [{"date": d, "value": v} for d, v in points], **meta}


def by_topic(rs):
    return {r["topic"]: r for r in rs}


def test_readings_worked_example():
    macro = {"series": {
        "T10Y3M": ser([("2026-07-09", -0.10), ("2026-10-08", None), ("2026-10-09", 0.45)]),
        "T10Y2Y": ser([("2026-10-09", 0.30)]),
        "BAMLH0A0HYM2": ser([("2026-07-08", 3.00), ("2026-10-09", 3.40)]),
        # CPI index: 2025-09 = 320.0, 2026-09 = 328.0 -> 328/320 - 1 = 2.5%
        "CPIAUCSL": ser([("2025-09-01", 320.0), ("2026-08-01", 327.0), ("2026-09-01", 328.0)]),
        "BOE_IUDBEDR": ser([("2026-10-09", 3.75)]),
        "ONS_L55O": ser([("2026-08-01", 3.8)]),
        # Claims: recent 4 weeks avg (230+232+228+238)/4 = 232,000; 13 weeks earlier avg 220,000 -> +5.45% (> 5%: rising)
        "ICSA": ser([("2026-06-13", 218000.0), ("2026-06-20", 222000.0), ("2026-06-27", 219000.0), ("2026-07-04", 221000.0),
                     ("2026-09-12", 230000.0), ("2026-09-19", 232000.0), ("2026-09-26", 228000.0), ("2026-10-03", 238000.0)]),
        "NFCI": ser([("2026-10-02", -0.52)]),
        "WB_GDP_SG": ser([("2025", 4.4), ("2026", None)], series_code="NY.GDP.MKTP.KD.ZG", economy="Singapore"),
        "WB_GDP_GB": {"status": "failed", "error": "fetch failed: HTTPError", "observations": [],
                      "series_code": "NY.GDP.MKTP.KD.ZG", "economy": "United Kingdom"},
    }}
    r = by_topic(M.readings(macro))
    yc = r["Yield curve"]
    assert yc["reading"] == "Yield curve (10y–3m) not inverted, steepening over 3 months"
    assert "+0.45 pp on 2026-10-09" in yc["evidence"] and "-0.10 pp on 2026-07-09" in yc["evidence"]
    assert "change +0.55 pp" in yc["evidence"] and "10y–2y +0.30 pp" in yc["evidence"]
    assert r["US credit spread"]["reading"] == "US high-yield spread tight (3.40 pp), widened 0.40 pp over 3 months"
    assert r["US inflation"]["reading"] == "US CPI inflation 2.5% y/y (Sep 2026)"
    assert r["UK rates and inflation"]["reading"] == "UK Bank Rate 3.75%, CPIH 3.8% y/y (Aug 2026); real Bank Rate -0.05 pp"
    claims = r["US jobless claims"]
    assert claims["reading"] == "US initial jobless claims rising (+5.5% over 13 weeks)"
    assert "232,000" in claims["evidence"] and "220,000" in claims["evidence"]
    assert r["Financial conditions"]["reading"] == "US financial conditions looser than average"
    assert r["GDP growth — Singapore"]["reading"] == "Real GDP growth 4.4% in 2025"
    assert r["GDP growth — United Kingdom"]["reading"] == "not available"
    assert "HTTPError" in r["GDP growth — United Kingdom"]["evidence"]


def test_readings_never_compute_from_missing_data():
    macro = {"series": {
        "T10Y3M": ser([("2026-10-09", -0.20)]),                                # no 3-month-earlier point
        "BAMLH0A0HYM2": {"status": "failed", "error": "parse failed: x", "observations": []},
        "CPIAUCSL": ser([("2025-08-01", 319.0), ("2026-09-01", 328.0)]),         # no Sep 2025 value
        "ONS_L55O": ser([("2026-08-01", 3.8)]),
        "ICSA": ser([("2026-09-26", 228000.0), ("2026-10-03", 238000.0)]),
    }}
    r = by_topic(M.readings(macro))
    assert r["Yield curve"]["reading"] == "Yield curve (10y–3m) inverted; 3-month change not available"
    assert r["US credit spread"]["reading"] == "not available" and "parse failed" in r["US credit spread"]["evidence"]
    assert r["US inflation"]["reading"] == "not available" and "Sep 2025" in r["US inflation"]["evidence"]
    assert r["UK rates and inflation"]["reading"] == "UK Bank Rate not available, CPIH 3.8% y/y (Aug 2026)"
    assert r["US jobless claims"]["reading"] == "not available"
    assert r["Financial conditions"]["reading"] == "not available"
    assert r["GDP growth"]["reading"] == "not available"
    assert all(set(x) == {"topic", "reading", "evidence"} for x in M.readings(macro))


def test_minus_months_clamps_month_end():
    from datetime import date
    assert M._minus_months(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert M._minus_months(date(2026, 1, 15), 12) == date(2025, 1, 15)
    assert M._minus_months(date(2026, 3, 31), 3) == date(2025, 12, 31)


def test_fetch_retries_once_after_a_timeout():
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("The read operation timed out")
        return b'{"x": 1}'
    cfg = {"series": [{"id": "S", "url": "https://example.org/s", "format": "fredgraph_csv"}]}
    res = M.fetch_all(cfg, opener=opener)
    assert len(calls) == 2 and "TimeoutError" not in (res["series"]["S"].get("error") or "")
