import json
from pathlib import Path

import pytest

from global_investment_research import news as N

RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>FRB: Press Releases</title><link>https://www.federalreserve.gov/</link>
<item><title>Federal Reserve issues FOMC statement</title>
<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261012a.htm</link>
<description>&lt;p&gt;Monetary policy &amp;amp; rates&lt;/p&gt;</description>
<pubDate>Mon, 12 Oct 2026 18:00:00 GMT</pubDate></item>
<item><title>Minutes of the Board's discount rate meetings</title>
<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261009a.htm</link>
<pubDate>Fri, 09 Oct 2026 18:00:00 -0400</pubDate></item>
<item><title>No link here</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title>
<entry><title type="html">Bank Rate maintained at 4%</title>
<link rel="self" href="https://example.org/self/1"/>
<link rel="alternate" href="https://www.bankofengland.co.uk/monetary-policy-summary/2026/october-2026"/>
<updated>2026-10-12T11:00:00Z</updated><summary>The MPC voted to maintain Bank Rate.</summary></entry>
</feed>"""


def test_rss_parsing():
    items = N.parse_feed(RSS)
    assert [i["title"] for i in items] == ["Federal Reserve issues FOMC statement",
                                            "Minutes of the Board's discount rate meetings"]
    assert items[0]["published"] == "2026-10-12T18:00:00+00:00"
    assert items[1]["published"] == "2026-10-09T22:00:00+00:00"   # -0400 converted to UTC
    assert items[0]["summary"] == "Monetary policy & rates"
    assert items[0]["link"].endswith("monetary20261012a.htm")


def test_atom_parsing_prefers_alternate_link():
    (item,) = N.parse_feed(ATOM)
    assert item["link"] == "https://www.bankofengland.co.uk/monetary-policy-summary/2026/october-2026"
    assert item["published"] == "2026-10-12T11:00:00+00:00" and item["summary"].startswith("The MPC")


@pytest.mark.parametrize("bomb", [
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel/></rss>',
    b'<?xml version="1.0"?><!doctype rss SYSTEM "file:///etc/passwd"><rss/>',
    b'<rss><channel><!ENTITY x "y"></channel></rss>',
])
def test_doctype_and_entity_refused(bomb):
    with pytest.raises(ValueError, match="Unsafe"):
        N.parse_feed(bomb)


def test_non_feed_refused():
    with pytest.raises(ValueError):
        N.parse_feed(b"<html><body>hi</body></html>")


KW = {"monetary_policy": {"primary": ["unscheduled", "emergency"], "secondary": ["FOMC", "Bank Rate", "discount rate"]},
      "financial_stability": {"primary": ["bank failure"], "secondary": []}}


def test_triage_topic_severity_and_order():
    items = [{"title": "Federal Reserve issues FOMC statement", "published": "2026-10-12T18:00:00+00:00"},
             {"title": "Agencies respond to bank failure", "published": "2026-10-11T10:00:00+00:00"},
             {"title": "Unscheduled meeting of the Board", "published": "2026-10-12T19:00:00+00:00"},
             {"title": "Board announces new staff appointments", "published": "2026-10-12T20:00:00+00:00"},
             {"title": "Minutes of discount rates meetings", "published": None}]
    got = N.triage(items, KW)
    assert [(g["title"][:12], g["topic"], g["severity"]) for g in got] == [
        ("Unscheduled ", "monetary_policy", "primary"),      # primary, newest first
        ("Agencies res", "financial_stability", "primary"),
        ("Federal Rese", "monetary_policy", "secondary"),
        ("Minutes of d", "monetary_policy", "secondary"),    # plural matched, undated sorts last
    ]
    assert got[2]["keyword"] == "FOMC"


def test_fetch_never_raises_on_one_bad_feed():
    feeds = {"https://ok.example/rss": RSS, "https://bad.example/rss": b"<not xml"}

    def opener(url, timeout):
        assert timeout == 15
        if url not in feeds:
            raise OSError("connection refused")
        return feeds[url]

    sources = [{"id": "fed", "url": "https://ok.example/rss", "market": "US", "authority": "primary"},
               {"id": "bad", "url": "https://bad.example/rss", "market": "UK"},
               {"id": "down", "url": "https://down.example/rss"},
               {"id": "plain", "url": "http://ok.example/rss"},
               {"id": "off", "url": "https://ok.example/rss", "enabled": False}]
    items, failures = N.fetch(sources, opener)
    assert len(items) == 2 and items[0]["source"] == "fed" and items[0]["market"] == "US"
    assert [f["source"] for f in failures] == ["bad", "down", "plain"]
    assert failures[1]["reason"].startswith("OSError")


def test_headline_context_attaches_recent_matches_to_urgent_only():
    now = "2026-10-12T20:00:00Z"
    items = [
        {"title": "Nvidia supplier issues profit warning", "link": "https://x/1", "published": "2026-10-12T19:00:00+00:00", "source": "s"},
        {"title": "NVDA export licence update", "link": "https://x/2", "published": "2026-10-12T18:00:00+00:00", "source": "s"},
        {"title": "Nvidia old news", "link": "https://x/3", "published": "2026-10-09T18:00:00+00:00", "source": "s"},
        {"title": "Nvidia undated", "link": "https://x/4", "published": None, "source": "s"},
        {"title": "Nvidia third recent", "link": "https://x/5", "published": "2026-10-12T17:00:00+00:00", "source": "s"},
        {"title": "HKMA: weak-side Convertibility Undertaking triggered", "link": "https://x/6",
         "published": "2026-10-12T08:00:00+00:00", "source": "hkma_press"},
    ]
    alerts = [{"symbol": "NVDA", "rule": "Extreme daily move", "severity": "URGENT"},
              {"symbol": "USD.HKD", "rule": "HKD peg band breached", "severity": "URGENT"},
              {"symbol": "MSFT", "rule": "Large daily move", "severity": "WARN"},
              {"symbol": "XOM", "rule": "Exit level breached", "severity": "URGENT"}]
    out = N.headline_context(alerts, items, {"NVDA": "Nvidia", "MSFT": "Microsoft Corp"}, now_iso=now)
    assert [h["link"] for h in out[0]["context"]] == ["https://x/1", "https://x/2"]   # 2 newest, recent, dated
    assert out[1]["context"][0]["source"] == "hkma_press"                            # market words
    assert "context" not in out[2] and "context" not in out[3]                       # WARN; no match
    assert "context" not in alerts[0]                                                 # input not mutated


def test_config_feeds_are_https_and_unverified():
    cfg = json.loads((Path(__file__).parents[1] / "config" / "news-sources.json").read_text())
    ids = {s["id"] for s in cfg["sources"]}
    assert {"fed_press", "bea_releases", "boe_news", "hkma_press"} <= ids
    assert all(s["url"].startswith("https://") and s["verified"] is False for s in cfg["sources"])
    for lists in cfg["keywords_by_topic"].values():
        assert set(lists) == {"primary", "secondary"}
