"""Official macro series fetched outside Claude (GitHub Actions has open internet).

Config lives in config/macro-sources.json. Each source format has a pure parser returning
[{"date", "value"}] with missing values as None (never filled or interpolated). `fetch_all`
records failures per series instead of raising, `write_snapshot` stores the evidence under
data/macro/, and `readings` turns a snapshot into monitor-style macro readings.

Dates: daily/weekly "YYYY-MM-DD", monthly "YYYY-MM-01" (period start, FRED convention), annual "YYYY".
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

Opener = Callable[[str, float], bytes]
MAX_BYTES = 20_000_000
USER_AGENT = "global-investment-research/0.1 (+https://github.com/dickenschui/global-investment-analysis)"
NA = "not available"

# Judgement thresholds for labels (not model outputs); evidence always shows the raw numbers.
HY_TIGHT, HY_WIDE = 3.5, 5.0          # US high-yield OAS, percentage points
CLAIMS_TREND = 0.05                    # 4-week average change over 13 weeks counted as a trend
MAX_GAP_DAYS = 10                      # comparison point must lie within this many days of its target


# ---------------------------------------------------------------- parsers
def _num(text) -> float | None:
    """Parse a numeric cell; "", ".", "NA", "N/A" mean missing. Non-finite values are rejected."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        s = str(text).strip()
        if s in ("", ".", "NA", "N/A", "n/a"):
            return None
        value = float(s.replace(",", ""))
    if not math.isfinite(value):
        raise ValueError(f"non-finite value {text!r}")
    return value


def _csv(body: bytes) -> csv.DictReader:
    return csv.DictReader(io.StringIO(body.decode("utf-8-sig")))


def _need(reader: csv.DictReader, columns: set[str]) -> None:
    missing = columns - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"expected column(s) {sorted(missing)} not in header {reader.fieldnames}")


def parse_fredgraph_csv(body: bytes, entry: dict) -> list[dict]:
    """FRED graph CSV: header `observation_date,<ID>` (older files: `DATE,<ID>`); "." marks a missing day."""
    reader, code = _csv(body), entry["series_code"]
    _need(reader, {code})
    date_col = "observation_date" if "observation_date" in reader.fieldnames else "DATE"
    _need(reader, {date_col})
    return [{"date": date.fromisoformat(r[date_col].strip()).isoformat(), "value": _num(r[code])} for r in reader]


def parse_ecb_csvdata(body: bytes, entry: dict) -> list[dict]:
    """ECB data API `format=csvdata`: one row per observation with TIME_PERIOD and OBS_VALUE.

    `entry["expect"]` (e.g. CURRENCY=GBP, CURRENCY_DENOM=EUR) is checked on every row so a
    quote-direction mix-up fails loudly instead of being stored.
    """
    reader, expect = _csv(body), entry.get("expect", {})
    _need(reader, {"TIME_PERIOD", "OBS_VALUE", *expect})
    out = []
    for r in reader:
        for col, want in expect.items():
            if r[col] != want:
                raise ValueError(f"{col}={r[col]!r}, expected {want!r}")
        out.append({"date": date.fromisoformat(r["TIME_PERIOD"].strip()).isoformat(), "value": _num(r["OBS_VALUE"])})
    return out


def parse_boe_iadb_csv(body: bytes, entry: dict) -> list[dict]:
    """BoE IADB CSV with CSVF=TN&UsingCodes=Y: header `DATE,<code>`, dates like `02 Jan 2024`."""
    reader, code = _csv(body), entry["series_code"]
    _need(reader, {"DATE", code})
    return [{"date": datetime.strptime(r["DATE"].strip(), "%d %b %Y").date().isoformat(), "value": _num(r[code])}
            for r in reader]


def parse_ons_timeseries_json(body: bytes, entry: dict) -> list[dict]:
    """ONS time-series `/data` JSON: `months` list of {"date": "2024 JAN", "value": "4.2", ...}."""
    data = json.loads(body)
    period = entry.get("period", "months")
    if period != "months":
        raise ValueError(f"unsupported ONS period {period!r}")
    rows = data.get("months")
    if not isinstance(rows, list):
        raise ValueError("ONS JSON has no 'months' list")
    return [{"date": datetime.strptime(r["date"].strip().title(), "%Y %b").date().isoformat(), "value": _num(r.get("value"))}
            for r in rows]


def parse_treasury_par_curve_csv(body: bytes, entry: dict) -> list[dict]:
    """US Treasury daily par yield curve CSV: `Date` (MM/DD/YYYY, newest first) and tenor columns like "10 Yr"."""
    reader, col = _csv(body), entry["column"]
    _need(reader, {"Date", col})
    return [{"date": datetime.strptime(r["Date"].strip(), "%m/%d/%Y").date().isoformat(), "value": _num(r[col])}
            for r in reader]


def parse_worldbank_json(body: bytes, entry: dict) -> list[dict]:
    """World Bank API v2 JSON: `[page-metadata, [ {indicator, country, date: "2023", value}, ... ]]`."""
    data = json.loads(body)
    if not isinstance(data, list) or not data:
        raise ValueError("World Bank response is not a list")
    if isinstance(data[0], dict) and "message" in data[0]:
        raise ValueError(f"World Bank API error: {data[0]['message']}")
    if len(data) < 2 or not isinstance(data[1], list):
        raise ValueError("World Bank response has no observation list")
    out = []
    for r in data[1]:
        if r["indicator"]["id"] != entry["series_code"]:
            raise ValueError(f"indicator {r['indicator']['id']!r}, expected {entry['series_code']!r}")
        if entry.get("country") and r["country"]["id"] != entry["country"]:
            raise ValueError(f"country {r['country']['id']!r}, expected {entry['country']!r}")
        out.append({"date": str(int(r["date"])), "value": _num(r["value"])})
    return out


PARSERS: dict[str, Callable[[bytes, dict], list[dict]]] = {
    "fredgraph_csv": parse_fredgraph_csv,
    "ecb_csvdata": parse_ecb_csvdata,
    "boe_iadb_csv": parse_boe_iadb_csv,
    "ons_timeseries_json": parse_ons_timeseries_json,
    "treasury_par_curve_csv": parse_treasury_par_curve_csv,
    "worldbank_json": parse_worldbank_json,
}


def validate(observations: list[dict], as_of: date) -> list[dict]:
    """Sort ascending; reject empty, duplicate-dated or future-dated series."""
    if not observations:
        raise ValueError("no observations")
    obs = sorted(observations, key=lambda o: o["date"])
    dates = [o["date"] for o in obs]
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate observation dates")
    if dates[-1] > as_of.isoformat()[: len(dates[-1])]:
        raise ValueError(f"future-dated observation {dates[-1]}")
    if all(o["value"] is None for o in obs):
        raise ValueError("every observation is missing")
    return obs


# ---------------------------------------------------------------- fetching
def resolve_url(template: str, now: datetime, lookback_days: int) -> str:
    """Fill {start_date}, {boe_start} and {year} placeholders."""
    start = (now.date() - timedelta(days=lookback_days))
    return (template.replace("{start_date}", start.isoformat())
            .replace("{boe_start}", start.strftime("%d/%b/%Y"))
            .replace("{year}", str(now.year)))


def default_opener(url: str, timeout: float) -> bytes:
    """TLS-verified GET with a bounded read; raises on HTTP errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("response exceeded bounded read")
    return body


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_all(config: dict, opener: Opener | None = None, timeout: float = 30.0,
              now: datetime | None = None) -> dict:
    """Fetch and parse every configured series. Never raises for one bad source: failures are recorded.

    Series sharing a URL (the Treasury curve) are downloaded once.
    """
    opener = opener or default_opener
    run_at = now or datetime.now(timezone.utc)
    clock = (lambda: run_at) if now else (lambda: datetime.now(timezone.utc))
    lookback = int(config.get("lookback_days", 1100))
    bodies: dict[str, tuple[bytes | None, str | None, str]] = {}
    series, failures = {}, []
    for entry in config.get("series", []):
        url = resolve_url(entry["url"], run_at, lookback)
        if url not in bodies:
            for attempt in (1, 2):  # one retry: slow hosts (World Bank) time out intermittently
                try:
                    bodies[url] = (opener(url, timeout), None, _iso(clock()))
                    break
                except Exception as exc:  # network, HTTP or TLS failure: record and continue
                    bodies[url] = (None, f"fetch failed: {type(exc).__name__}: {exc}"[:300], _iso(clock()))
        body, error, retrieved_at = bodies[url]
        rec = {k: v for k, v in entry.items() if k != "url"}
        rec.update(url=url, retrieved_at=retrieved_at, status="failed", observations=[], latest=None)
        if body is not None:
            rec.update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
            try:
                parser = PARSERS[entry["format"]]
                rec["observations"] = validate(parser(body, entry), run_at.date())
                rec["latest"] = next(o for o in reversed(rec["observations"]) if o["value"] is not None)
                rec["status"] = "ok"
            except Exception as exc:
                error = f"parse failed: {type(exc).__name__}: {exc}"[:300]
        if rec["status"] != "ok":
            rec["error"] = error
            failures.append({"id": entry["id"], "url": url, "error": error})
        series[entry["id"]] = rec
    return {"schema_version": 1, "retrieved_at": _iso(run_at), "series": series, "failures": failures,
            "not_configured": config.get("not_configured", []),
            "summary": {"configured": len(series), "ok": len(series) - len(failures), "failed": len(failures)},
            "note": "Current/revised official data, not first-release vintages. Missing values are None, never filled."}


def write_snapshot(results: dict, out_dir: str | Path) -> dict[str, str]:
    """Write <out_dir>/<YYYY-MM-DD>/macro.json and <out_dir>/latest.json (same content)."""
    out_dir = Path(out_dir)
    day_dir = out_dir / results["retrieved_at"][:10]
    day_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(results, indent=1, ensure_ascii=False) + "\n"
    paths = {"dated": day_dir / "macro.json", "latest": out_dir / "latest.json"}
    for path in paths.values():
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return {k: str(v) for k, v in paths.items()}


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


CARRY_FORWARD_DAYS = 7


def carry_forward(results: dict, previous: dict | None, max_age_days: int = CARRY_FORWARD_DAYS) -> dict:
    """Keep the last good copy of a series that failed this run, so a run with transient failures (e.g. a backup
    run) never overwrites good data. Only copies fetched within max_age_days are kept; each keeps its own
    retrieved_at and is marked carried_forward with this run's error. Older ones are left as failures."""
    if not previous:
        return results
    run_at = datetime.fromisoformat(results["retrieved_at"].replace("Z", "+00:00"))
    series, carried = dict(results["series"]), []
    for sid, rec in results["series"].items():
        old = (previous.get("series") or {}).get(sid)
        if rec.get("status") == "ok" or not old or old.get("status") != "ok" or not old.get("retrieved_at"):
            continue
        age = run_at - datetime.fromisoformat(old["retrieved_at"].replace("Z", "+00:00"))
        if age.days < max_age_days:
            series[sid] = {**old, "carried_forward": True, "last_error": rec.get("error")}
            carried.append(sid)
    failures = [f for f in results["failures"] if f["id"] not in carried]
    summary = {**results["summary"], "ok": results["summary"]["ok"] + len(carried), "failed": len(failures),
               "carried_forward": len(carried)}
    return {**results, "series": series, "failures": failures, "summary": summary,
            "carried_forward": [{"id": c, "error": series[c]["last_error"]} for c in carried]}


def run(config_path: str | Path, out_dir: str | Path, opener: Opener | None = None,
        timeout: float = 30.0, now: datetime | None = None) -> dict:
    """CLI helper: fetch everything, write the snapshot, return a short summary (paths + failures).
    Series that fail this run keep their last good copy from latest.json (see carry_forward)."""
    results = fetch_all(load_config(config_path), opener=opener, timeout=timeout, now=now)
    latest = Path(out_dir) / "latest.json"
    previous = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else None
    results = carry_forward(results, previous)
    paths = write_snapshot(results, out_dir)
    return {**results["summary"], "paths": paths, "failures": results["failures"]}


# ---------------------------------------------------------------- readings
def _points(macro: dict, sid: str) -> tuple[list[tuple[str, float]], str | None]:
    """Non-missing (date, value) pairs for a series, or ([], reason)."""
    s = (macro.get("series") or {}).get(sid)
    if s is None:
        return [], f"{sid} not in the macro snapshot"
    if s.get("status") != "ok":
        return [], f"{sid} unavailable ({s.get('error', 'source failed')})"
    pts = [(o["date"], o["value"]) for o in s.get("observations", []) if o["value"] is not None]
    return (pts, None) if pts else ([], f"{sid} has no non-missing observations")


def _minus_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _at_or_before(pts: list[tuple[str, float]], target: date, max_gap: int = MAX_GAP_DAYS):
    """Index of the last point on/before target, if within max_gap days of it; else None."""
    best = None
    for i, (d, _) in enumerate(pts):
        if d <= target.isoformat():
            best = i
    if best is None or (target - date.fromisoformat(pts[best][0])).days > max_gap:
        return None
    return best


def _three_month_change(pts):
    """(latest, earlier, change) with earlier = point ~3 calendar months before latest, or earlier=None."""
    d, v = pts[-1]
    target = _minus_months(date.fromisoformat(d), 3)
    i = _at_or_before(pts, target)
    return (d, v), (None if i is None else pts[i]), (None if i is None else v - pts[i][1]), target


def _month(d: str) -> str:
    return datetime.strptime(d[:7], "%Y-%m").strftime("%b %Y")


def readings(macro: dict) -> list[dict]:
    """Macro readings in monitor.regime_readings shape: [{topic, reading, evidence}].

    Nothing is computed from missing data: a missing input gives reading "not available"
    with the reason as evidence.
    """
    out = []

    def add(topic, reading, evidence):
        out.append({"topic": topic, "reading": reading, "evidence": evidence})

    # Yield curve
    pts, why = _points(macro, "T10Y3M")
    if not pts:
        add("Yield curve", NA, why)
    else:
        (d, v), prior, chg, target = _three_month_change(pts)
        state = "inverted" if v < 0 else "not inverted"
        ev = f"FRED T10Y3M (10y minus 3m) {v:+.2f} pp on {d}"
        if prior is None:
            label = f"Yield curve (10y–3m) {state}; 3-month change not available"
            ev += f"; no observation within {MAX_GAP_DAYS} days of {target}"
        else:
            move = "steepening" if chg > 0 else "flattening" if chg < 0 else "unchanged"
            label = f"Yield curve (10y–3m) {state}, {move} over 3 months"
            ev += f"; {prior[1]:+.2f} pp on {prior[0]}; change {chg:+.2f} pp"
        p2, _ = _points(macro, "T10Y2Y")
        if p2:
            ev += f". 10y–2y {p2[-1][1]:+.2f} pp on {p2[-1][0]}"
        add("Yield curve", label, ev)

    # US high-yield spread
    pts, why = _points(macro, "BAMLH0A0HYM2")
    if not pts:
        add("US credit spread", NA, why)
    else:
        (d, v), prior, chg, target = _three_month_change(pts)
        band = "tight" if v < HY_TIGHT else "moderate" if v < HY_WIDE else "wide"
        ev = f"ICE BofA US High Yield OAS (FRED BAMLH0A0HYM2) {v:.2f} pp on {d}"
        if prior is None:
            label = f"US high-yield spread {band} ({v:.2f} pp); 3-month change not available"
            ev += f"; no observation within {MAX_GAP_DAYS} days of {target}"
        else:
            move = "widened" if chg > 0 else "narrowed" if chg < 0 else "unchanged"
            label = f"US high-yield spread {band} ({v:.2f} pp), {move} {abs(chg):.2f} pp over 3 months"
            ev += f"; {prior[1]:.2f} pp on {prior[0]}; change {chg:+.2f} pp"
        ev += f". Bands: tight <{HY_TIGHT}, moderate <{HY_WIDE}, wide ≥{HY_WIDE} pp"
        add("US credit spread", label, ev)

    # US inflation: CPI index year on year, exact month 12 months earlier required
    pts, why = _points(macro, "CPIAUCSL")
    if not pts:
        add("US inflation", NA, why)
    else:
        d, v = pts[-1]
        year_ago = _minus_months(date.fromisoformat(d), 12).isoformat()
        base = dict(pts).get(year_ago)
        if base is None:
            add("US inflation", NA, f"CPIAUCSL {v} for {_month(d)} but no index value for {_month(year_ago)}")
        else:
            yoy = v / base - 1
            add("US inflation", f"US CPI inflation {yoy * 100:.1f}% y/y ({_month(d)})",
                f"FRED CPIAUCSL {v:.3f} ({_month(d)}) vs {base:.3f} ({_month(year_ago)})")

    # UK policy rate and CPIH
    br, why_br = _points(macro, "BOE_IUDBEDR")
    cp, why_cp = _points(macro, "ONS_L55O")
    if not br and not cp:
        add("UK rates and inflation", NA, f"{why_br}; {why_cp}")
    else:
        parts, ev = [], []
        if br:
            parts.append(f"Bank Rate {br[-1][1]:.2f}%")
            ev.append(f"BoE IUDBEDR {br[-1][1]:.2f}% on {br[-1][0]}")
        else:
            parts.append(f"Bank Rate {NA}")
            ev.append(why_br)
        if cp:
            parts.append(f"CPIH {cp[-1][1]:.1f}% y/y ({_month(cp[-1][0])})")
            ev.append(f"ONS L55O {cp[-1][1]:.1f}% for {_month(cp[-1][0])}")
        else:
            parts.append(f"CPIH {NA}")
            ev.append(why_cp)
        label = "UK " + ", ".join(parts)
        if br and cp:
            label += f"; real Bank Rate {br[-1][1] - cp[-1][1]:+.2f} pp"
        add("UK rates and inflation", label, "; ".join(ev))

    # US jobless claims: 4-week average vs the 4-week average ending ~13 weeks earlier
    pts, why = _points(macro, "ICSA")
    topic = "US jobless claims"
    if not pts:
        add(topic, NA, why)
    else:
        def avg4(end):
            window = pts[end - 3: end + 1] if end >= 3 else []
            if len(window) < 4 or (date.fromisoformat(window[-1][0]) - date.fromisoformat(window[0][0])).days > 21:
                return None
            return sum(x for _, x in window) / 4, window[0][0], window[-1][0]
        now_avg = avg4(len(pts) - 1)
        target = date.fromisoformat(pts[-1][0]) - timedelta(weeks=13)
        j = _at_or_before(pts, target)
        then_avg = avg4(j) if j is not None else None
        if now_avg is None or then_avg is None:
            add(topic, NA, f"ICSA: need four consecutive weekly values now and 13 weeks earlier (to {target}); not present")
        else:
            chg = now_avg[0] / then_avg[0] - 1
            label = "rising" if chg > CLAIMS_TREND else "falling" if chg < -CLAIMS_TREND else "broadly stable"
            add(topic, f"US initial jobless claims {label} ({chg * 100:+.1f}% over 13 weeks)",
                f"FRED ICSA 4-week average {now_avg[0]:,.0f} (weeks {now_avg[1]} to {now_avg[2]}) vs "
                f"{then_avg[0]:,.0f} ({then_avg[1]} to {then_avg[2]}); trend threshold ±{CLAIMS_TREND * 100:.0f}%")

    # Financial conditions
    pts, why = _points(macro, "NFCI")
    if not pts:
        add("Financial conditions", NA, why)
    else:
        d, v = pts[-1]
        label = "tighter than average" if v > 0 else "looser than average" if v < 0 else "at average"
        add("Financial conditions", f"US financial conditions {label}",
            f"Chicago Fed NFCI {v:+.2f} for week of {d} (0 = average; positive = tighter)")

    # GDP growth by economy, latest available year
    gdp = {sid: s for sid, s in (macro.get("series") or {}).items() if s.get("series_code") == "NY.GDP.MKTP.KD.ZG"}
    if not gdp:
        add("GDP growth", NA, "no World Bank GDP growth series in the macro snapshot")
    for sid, s in sorted(gdp.items()):
        name = s.get("economy") or s.get("country") or sid
        pts, why = _points(macro, sid)
        if not pts:
            add(f"GDP growth — {name}", NA, why)
        else:
            add(f"GDP growth — {name}", f"Real GDP growth {pts[-1][1]:.1f}% in {pts[-1][0]}",
                f"World Bank NY.GDP.MKTP.KD.ZG, {name}, {pts[-1][0]} (latest year with a value)")
    return out
