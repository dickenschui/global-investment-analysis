"""Self-contained HTML rendering (no CDN, no network). Light/dark aware."""
from __future__ import annotations

import html as _h

CSS = """
/* Layout: single reading column, summary tiles first, then scrollable data tables */
:root{--bg:#f6f7f9;--panel:#ffffff;--ink:#17202a;--muted:#5b6673;--rule:#d9dee5;--accent:#1d5c8a;
--up:#1f7a4d;--down:#b4372f;--warn:#9a6a00;--chip:#eef2f6;
--f-display:"IBM Plex Serif",Georgia,serif;--f-body:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;--f-data:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#11161c;--panel:#18202a;--ink:#e6ebf0;--muted:#9aa6b2;--rule:#2c3946;--accent:#6fb0e0;--up:#5cc28f;--down:#ef7d73;--warn:#e0b44f;--chip:#22303d;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#11161c;--panel:#18202a;--ink:#e6ebf0;--muted:#9aa6b2;--rule:#2c3946;--accent:#6fb0e0;--up:#5cc28f;--down:#ef7d73;--warn:#e0b44f;--chip:#22303d;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font:15px/1.55 var(--f-body);margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-inline:16px;padding-block:28px 56px;display:grid;gap:28px}
header h1{font:600 clamp(1.6rem,3.2vw,2.3rem)/1.15 var(--f-display);margin:0 0 6px;text-wrap:balance}
header p{margin:0;color:var(--muted);max-width:75ch}
.eyebrow{font:500 .72rem/1 var(--f-data);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin-bottom:10px}
section{display:grid;gap:12px;min-width:0}
h2{font:600 1.25rem/1.3 var(--f-display);margin:0;text-wrap:balance}
.intro{color:var(--muted);margin:0;max-width:80ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px 14px;display:grid;gap:4px;min-width:0}
.tile .k{font:500 .72rem/1.2 var(--f-data);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tile .v{font:600 1.3rem/1.2 var(--f-data);font-variant-numeric:tabular-nums}
.tile .s{font-size:.82rem;color:var(--muted)}
.tbl{overflow-x:auto;background:var(--panel);border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{padding:7px 10px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top;white-space:nowrap}
td.wrap-text{white-space:normal;min-width:240px;max-width:420px}
th{font:500 .7rem/1.2 var(--f-data);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);background:var(--panel);position:sticky;top:0}
td.n{font-family:var(--f-data);font-variant-numeric:tabular-nums;text-align:right}
tr:last-child td{border-bottom:0}
.up{color:var(--up)}.down{color:var(--down)}.warn{color:var(--warn)}.mut{color:var(--muted)}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--chip);font:500 .75rem/1.6 var(--f-data)}
.chip.up{color:var(--up)}.chip.down{color:var(--down)}.chip.warn{color:var(--warn)}
ul.notes{margin:0;padding-left:1.1rem;display:grid;gap:4px;max-width:85ch}
.foot{font-size:.8rem;color:var(--muted);border-top:1px solid var(--rule);padding-top:14px;max-width:90ch}
svg.spark{display:block}
svg.spark path.l{fill:none;stroke:var(--accent);stroke-width:1.4}
svg.spark path.a{fill:var(--accent);opacity:.12}
svg.spark circle{fill:var(--accent)}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600'
         '&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@600&display=swap">')


def esc(x) -> str:
    return _h.escape("" if x is None else str(x))


def pct(x, signed=True, digits=1) -> str:
    if x is None:
        return '<span class="mut">n/a</span>'
    cls = "up" if x > 0 else "down" if x < 0 else ""
    s = f"{x * 100:+.{digits}f}%" if signed else f"{x * 100:.{digits}f}%"
    return f'<span class="{cls}">{s}</span>' if signed else s


def num(x, digits=2) -> str:
    return '<span class="mut">n/a</span>' if x is None else f"{x:,.{digits}f}"


def chip(text, tone="") -> str:
    return f'<span class="chip {tone}">{esc(text)}</span>'


def trend_chip(state) -> str:
    return chip(state or "n/a", {"up": "up", "down": "down", "mixed": "warn"}.get(state, ""))


def sparkline(values, w=120, h=28) -> str:
    v = [x for x in values if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1
    pts = [(i * (w - 4) / (len(v) - 1) + 2, h - 3 - (x - lo) * (h - 6) / rng) for i, x in enumerate(v)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = line + f" L{pts[-1][0]:.1f},{h} L{pts[0][0]:.1f},{h} Z"
    ex, ey = pts[-1]
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="1-year price path">'
            f'<path class="a" d="{area}"/><path class="l" d="{line}"/><circle cx="{ex:.1f}" cy="{ey:.1f}" r="2"/></svg>')


def table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset(), wrap: set[int] = frozenset()) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="{"n" if i in numeric else "wrap-text" if i in wrap else ""}">{c}</td>' for i, c in enumerate(r)
        ) + "</tr>" for r in rows)
    return f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def tiles(items: list[tuple[str, str, str]]) -> str:
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><span class="k">{esc(k)}</span><span class="v">{v}</span><span class="s">{s}</span></div>'
        for k, v, s in items) + "</div>"


def section(title: str, body: str, intro: str = "") -> str:
    i = f'<p class="intro">{intro}</p>' if intro else ""
    return f"<section><h2>{esc(title)}</h2>{i}{body}</section>"


def notes(items: list[str]) -> str:
    return '<ul class="notes">' + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def page(title: str, eyebrow: str, lede: str, body: str, footer: str, standalone: bool = True) -> str:
    inner = (f"<title>{esc(title)}</title>{FONTS}<style>{CSS}</style>"
             f'<div class="wrap"><header><div class="eyebrow">{esc(eyebrow)}</div><h1>{esc(title)}</h1>'
             f"<p>{lede}</p></header>{body}<p class=\"foot\">{footer}</p></div>")
    if not standalone:
        return inner
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>'
            + inner + "</body></html>")
