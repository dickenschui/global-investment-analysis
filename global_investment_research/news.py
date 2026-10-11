"""Primary-source headline triage for the hourly check (standard library only).

Reads official RSS 2.0 / Atom feeds (central banks, statistics offices), flags headlines that match configured
keywords, and attaches the most recent relevant ones to URGENT price alerts as context. Keyword matches triage a
headline for review; they do not verify the release, its magnitude, a surprise or any causal link to a price move.
Ported from the reference hosting/hourly_monitor.py (parse_feed, fetch_source) and config/hourly-monitor.json.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

MAX_FEED_BYTES = 2_000_000
USER_AGENT = "GlobalInvestmentResearch/0.1 public-source monitoring"
SEVERITY_ORDER = {"primary": 0, "secondary": 1}

# Words that tie a headline to a market-level instrument (its own ticker or name rarely appears in news).
MARKET_WORDS = {
    "SPY": ["S&P 500", "Wall Street", "Federal Reserve", "FOMC"],
    "QQQ": ["Nasdaq", "Federal Reserve", "FOMC"],
    "ISF": ["FTSE", "Bank of England", "Monetary Policy Committee"],
    "ES3": ["Straits Times Index", "Monetary Authority of Singapore", "Singapore"],
    "2800": ["Hang Seng", "HKMA", "Hong Kong"],
    "3188": ["CSI 300", "People's Bank of China", "China"],
    "VIX": ["volatility", "Federal Reserve", "FOMC"],
    "TLT": ["Treasury", "Federal Reserve", "FOMC"],
    "IEF": ["Treasury", "Federal Reserve", "FOMC"],
    "GLD": ["gold"],
    "USD.HKD": ["Hong Kong dollar", "HKMA", "Convertibility Undertaking", "Linked Exchange Rate"],
    "GBP.USD": ["sterling", "Bank of England", "Federal Reserve"],
    "EUR.USD": ["euro", "European Central Bank", "ECB", "Federal Reserve"],
    "EUR.GBP": ["euro", "sterling", "European Central Bank", "Bank of England"],
    "USD.SGD": ["Singapore dollar", "Monetary Authority of Singapore"],
    "USD.CNH": ["renminbi", "yuan", "People's Bank of China"],
    "USD.JPY": ["yen", "Bank of Japan"],
}
_NAME_SUFFIX = re.compile(r"\s+(ETF|plc|PLC|Inc\.?|Ltd\.?|Limited|Holdings|Group|Corp\.?|Corporation|Trust|REIT)$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(s: str | None, limit: int) -> str:
    """Plain text: tags stripped, entities decoded, whitespace collapsed."""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _iso(raw: str | None) -> str | None:
    """RFC 822 (RSS) or ISO 8601 (Atom, dc:date) -> ISO UTC; None if absent or unreadable. Naive times read as UTC."""
    if not raw:
        return None
    raw = raw.strip()
    for parse in (lambda r: datetime.fromisoformat(r.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            d = parse(raw)
        except (ValueError, TypeError, IndexError):
            continue
        if d is None:
            continue
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return None


def parse_feed(xml_bytes: bytes) -> list[dict]:
    """Items of an RSS 2.0 / RSS 1.0 / Atom document as [{title, link, published, summary}].

    Refuses any document carrying a DOCTYPE or ENTITY declaration (entity-expansion 'XML bomb' guard): no
    legitimate feed needs one. Items without a title or an http(s) link are skipped; duplicates dropped."""
    data = xml_bytes.encode() if isinstance(xml_bytes, str) else xml_bytes
    up = data.upper()
    if b"<!DOCTYPE" in up or b"<!ENTITY" in up:
        raise ValueError("Unsafe XML declarations (DOCTYPE/ENTITY) refused")
    root = ET.fromstring(data)
    if _local(root.tag) not in ("rss", "feed", "RDF"):
        raise ValueError("Not an RSS or Atom feed")
    out, seen = [], set()
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        link = None
        for child in node:
            name = _local(child.tag)
            if name == "link" and child.attrib.get("href"):  # Atom: prefer rel="alternate" (the default)
                if child.attrib.get("rel", "alternate") == "alternate" and link is None:
                    link = child.attrib["href"].strip()
                continue
            fields.setdefault(name, (child.text or "").strip())
        link = link or fields.get("link")
        title = _text(fields.get("title"), 1000)
        if not title or not link or urlparse(link).scheme not in ("http", "https"):
            continue
        if (title, link) in seen:
            continue
        seen.add((title, link))
        published = _iso(fields.get("pubDate") or fields.get("published") or fields.get("updated") or fields.get("date"))
        summary = _text(fields.get("description") or fields.get("summary") or fields.get("content"), 500)
        out.append({"title": title, "link": link, "published": published, "summary": summary})
    return out


def _urlopen(url: str, timeout: float) -> bytes:
    from urllib.request import Request, urlopen
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as r:  # noqa: S310 (https only)
        data = r.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("Feed exceeds size limit")
    return data


def fetch(sources: list[dict], opener=None, timeout: float = 15) -> tuple[list[dict], list[dict]]:
    """Fetch and parse each enabled source. One bad feed is recorded in `failures`, never raised.

    `opener(url, timeout) -> bytes` is injectable for tests; the default uses urllib. Items carry the source's
    id, market and authority. Failure reasons name the error type only (no transport text)."""
    opener = opener or _urlopen
    items, failures = [], []
    for src in sources:
        if src.get("enabled", True) is False:
            continue
        try:
            if urlparse(src["url"]).scheme != "https":
                raise ValueError("HTTPS source required")
            parsed = parse_feed(opener(src["url"], timeout))
        except Exception as exc:  # noqa: BLE001 - any failure of one feed must not stop the others
            failures.append({"source": src.get("id"), "url": src.get("url"),
                             "reason": f"{type(exc).__name__}: unavailable or not a valid feed"})
            continue
        for it in parsed:
            items.append({**it, "source": src.get("id"), "market": src.get("market"),
                          "authority": src.get("authority", "primary")})
    return items, failures


def _pattern(term: str, case_sensitive: bool = False) -> re.Pattern:
    """Whole-word match for a word or phrase (optional plural 's'), safe for terms like 'S&P 500'."""
    return re.compile(r"(?<!\w)" + re.escape(term) + r"s?(?!\w)", 0 if case_sensitive else re.I)


def triage(items: list[dict], keywords_by_topic: dict) -> list[dict]:
    """Headlines whose title matches a keyword, each tagged with topic, severity and the keyword.

    keywords_by_topic = {topic: {"primary": [...], "secondary": [...]}}; severity is "primary" when any primary
    keyword matches (the first such topic wins), else "secondary". Titles only, as in the reference: summaries carry
    boilerplate that would over-match. Sorted primary first, then newest first."""
    out = []
    for it in items:
        hit = None
        for sev in ("primary", "secondary"):
            for topic, lists in keywords_by_topic.items():
                kw = next((k for k in lists.get(sev, []) if _pattern(k).search(it["title"])), None)
                if kw:
                    hit = {"topic": topic, "severity": sev, "keyword": kw}
                    break
            if hit:
                break
        if hit:
            out.append({**it, **hit})
    newest = sorted(out, key=lambda x: x.get("published") or "", reverse=True)
    return sorted(newest, key=lambda x: SEVERITY_ORDER[x["severity"]])


def _terms(symbol: str, symbol_names: dict) -> list[re.Pattern]:
    pats = []
    if re.fullmatch(r"[A-Z]{3,5}", symbol):  # short or punctuated tickers ("5", "BA.") would match noise
        pats.append(_pattern(symbol, case_sensitive=True))
    name = _NAME_SUFFIX.sub("", (symbol_names.get(symbol) or "").strip())
    if len(name) >= 3:
        pats.append(_pattern(name))
    pats += [_pattern(w) for w in MARKET_WORDS.get(symbol, [])]
    return pats


def headline_context(alerts: list[dict], items: list[dict], symbol_names: dict, *, now_iso: str | None = None,
                     max_age_hours: float = 48, per_alert: int = 2) -> list[dict]:
    """Copy of `alerts` with up to `per_alert` recent matching headlines attached to each URGENT alert as `context`.

    A headline matches on the alert's ticker (3-5 capital letters, case-sensitive), its company name, or the market
    words for index/FX/macro lines. Undated headlines and those older than max_age_hours are not used, since their
    recency cannot be shown. Context is for review only; it does not explain the move."""
    now = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) if now_iso else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    recent = []
    for it in items:
        if not it.get("published"):
            continue
        age = (now - datetime.fromisoformat(it["published"])).total_seconds() / 3600
        if 0 <= age <= max_age_hours:
            recent.append(it)
    recent.sort(key=lambda x: x["published"], reverse=True)
    out = []
    for a in alerts:
        a = dict(a)
        if a.get("severity") == "URGENT":
            pats = _terms(a["symbol"], symbol_names)
            text = lambda it: f"{it['title']} {it.get('summary') or ''}"
            hits = [it for it in recent if any(p.search(text(it)) for p in pats)][:per_alert]
            if hits:
                a["context"] = [{"title": h["title"], "link": h["link"], "published": h["published"],
                                 "source": h.get("source")} for h in hits]
        out.append(a)
    return out
