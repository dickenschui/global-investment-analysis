#!/usr/bin/env python3
"""Fetch filing-dated fundamentals from SEC EDGAR for the Desk's SEC-filing names.

Run this on your own computer: the SEC refuses requests from cloud runners (GitHub Actions
and the Claude container both get its "undeclared automated tool" page). Standard library only.

    EDGAR_CONTACT="Your Name you@example.com" python3 scripts/edgar_fetch.py

The SEC's fair-access policy requires a User-Agent naming the person and a contact address;
the script will not run without EDGAR_CONTACT. It sends at most 5 requests a second (the
limit is 10) and caches each raw reply under ~/.cache/edgar so a re-run costs nothing.

What it writes (commit these):
    data/fundamentals/edgar/<desk symbol>.json   compact point-in-time facts, one file per company
    data/fundamentals/edgar/manifest.json        sha256 and counts per file, for verification

Every fact keeps the date it was *filed*, so a backtest can use only what was known at the time.
Nothing is restated: if a figure was later revised, both versions are present with their own
filing dates. Values are copied exactly as EDGAR returns them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "fundamentals" / "edgar"
CACHE = Path.home() / ".cache" / "edgar"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
PACE_SECONDS = 0.2          # 5 requests a second; the SEC allows 10

# Desk symbol -> SEC ticker. US names file 10-Q/10-K; the UK and HK names below have US-listed
# ADRs that file an annual 20-F, so they carry annual data only.
COMPANIES = {
    "NVDA": "NVDA", "MSFT": "MSFT", "AVGO": "AVGO", "ANET": "ANET", "NOW": "NOW", "GOOGL": "GOOGL",
    "META": "META", "AMZN": "AMZN", "AAPL": "AAPL", "CRWD": "CRWD", "PANW": "PANW", "ORCL": "ORCL",
    "JPM": "JPM", "XOM": "XOM",
    "AZN": "AZN", "GSK": "GSK", "SHEL": "SHEL", "RIO": "RIO", "HSBA": "HSBC", "BARC": "BCS",
    "LLOY": "LYG", "ULVR": "UL",
    "9988": "BABA",
}

# Concept -> the XBRL tags that carry it, in order of preference (US GAAP first, then IFRS for 20-F filers).
CONCEPTS = {
    "revenue": ["us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                "us-gaap:SalesRevenueNet", "ifrs-full:Revenue"],
    "net_income": ["us-gaap:NetIncomeLoss", "ifrs-full:ProfitLossAttributableToOwnersOfParent", "ifrs-full:ProfitLoss"],
    "eps_diluted": ["us-gaap:EarningsPerShareDiluted", "ifrs-full:DilutedEarningsLossPerShare"],
    "equity": ["us-gaap:StockholdersEquity", "ifrs-full:EquityAttributableToOwnersOfParent", "ifrs-full:Equity"],
    "operating_cash_flow": ["us-gaap:NetCashProvidedByUsedInOperatingActivities",
                            "ifrs-full:CashFlowsFromUsedInOperatingActivities"],
    "shares_outstanding": ["dei:EntityCommonStockSharesOutstanding", "us-gaap:CommonStockSharesOutstanding",
                           "ifrs-full:NumberOfSharesOutstanding"],
}
KEEP = ("start", "end", "val", "fy", "fp", "form", "filed", "frame")


def user_agent() -> str:
    contact = os.environ.get("EDGAR_CONTACT", "").strip()
    if "@" not in contact:
        sys.exit('Set EDGAR_CONTACT to your name and email, e.g. EDGAR_CONTACT="Jane Doe jane@example.com" '
                 "(the SEC requires it; see https://www.sec.gov/os/accessing-edgar-data).")
    return f"global-investment-analysis {contact}"


def fetch(url: str, ua: str) -> bytes:
    """GET with the SEC headers, one retry on 429 or a 5xx, raw reply cached by URL."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / hashlib.sha256(url.encode()).hexdigest()
    if cached.exists():
        return cached.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(5)
                continue
            if e.code == 403:
                sys.exit(f"{url}: HTTP 403. The SEC refused the request; check EDGAR_CONTACT and that you are "
                         "not on a VPN or cloud machine.")
            raise
        time.sleep(PACE_SECONDS)
        cached.write_bytes(body)
        return body
    raise RuntimeError(f"{url}: gave up after two attempts")


def cik_map(ua: str) -> dict[str, int]:
    rows = json.loads(fetch(TICKERS_URL, ua)).values()
    return {r["ticker"].upper(): int(r["cik_str"]) for r in rows}


def extract(facts: dict) -> dict[str, list[dict]]:
    """Pick the Desk's concepts out of a companyfacts reply, keeping every filing of every fact.

    For each concept the first tag (in CONCEPTS order) that the company reports is used, so one
    company's series comes from one tag. The unit is recorded; values are not converted.
    """
    out = {}
    taxonomies = facts.get("facts", {})
    for concept, tags in CONCEPTS.items():
        for tag in tags:
            ns, name = tag.split(":")
            node = taxonomies.get(ns, {}).get(name)
            if not node:
                continue
            unit, rows = max(node["units"].items(), key=lambda kv: len(kv[1]))   # e.g. USD, USD/shares, shares
            out[concept] = {"tag": tag, "unit": unit,
                            "facts": [{k: r[k] for k in KEEP if k in r} for r in rows]}
            break
    return out


def main() -> int:
    ua = user_agent()
    ciks = cik_map(ua)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "SEC EDGAR companyfacts API (data.sec.gov), values as returned",
                "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "files": {}}
    missing = []
    for symbol, sec_ticker in COMPANIES.items():
        cik = ciks.get(sec_ticker)
        if cik is None:
            missing.append(f"{symbol} ({sec_ticker}: not in company_tickers.json)")
            continue
        facts = json.loads(fetch(FACTS_URL.format(cik=cik), ua))
        doc = {"symbol": symbol, "sec_ticker": sec_ticker, "cik": cik, "entity": facts.get("entityName"),
               "retrieved_at_utc": manifest["retrieved_at_utc"], "concepts": extract(facts)}
        if not doc["concepts"]:
            missing.append(f"{symbol} (no recognised concepts)")
            continue
        text = json.dumps(doc, separators=(",", ":"), sort_keys=True)
        path = OUT_DIR / f"{symbol}.json"
        path.write_text(text)
        filed = sorted(f["filed"] for c in doc["concepts"].values() for f in c["facts"])
        n = sum(len(c["facts"]) for c in doc["concepts"].values())
        manifest["files"][path.name] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "facts": n,
                                        "concepts": sorted(doc["concepts"]), "filed_from": filed[0], "filed_to": filed[-1]}
        print(f"{symbol:6} {doc['entity'][:34]:34} {n:6,} facts  filed {filed[0]} to {filed[-1]}  "
              f"{', '.join(sorted(doc['concepts']))}")
    manifest["missing"] = missing
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print(f"\n{len(manifest['files'])} companies written to {OUT_DIR.relative_to(ROOT)}; "
          f"missing: {missing or 'none'}")
    print("Next: git add data/fundamentals/edgar && git commit -m 'EDGAR fundamentals' && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
