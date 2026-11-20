"""SVG views of the Upstream Map data: git graph, transit map, orbit, pipeline.

Every glyph that stands for a PR or issue carries data-item="owner/repo#N", so
tests can check that each view shows exactly the items in the data.
Standard library only.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as _esc

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"
W = 760
MAX_LANES = 8
MAX_PIPELINE_ROWS = 12

THEMES = {
    "light": {"bg": "#ffffff", "border": "#d0d7de", "fg": "#1f2328", "muted": "#59636e", "faint": "#d8dee4",
              "zebra": "#f6f8fa", "accent": "#8250df", "lines": ["#8250df", "#bc4c00", "#0969da", "#1a7f37"],
              "ok": "#1a7f37", "wait": "#9a6700", "badge_bg": "#1f2328", "badge_fg": "#ffffff"},
    "dark": {"bg": "#0d1117", "border": "#30363d", "fg": "#e6edf3", "muted": "#9198a1", "faint": "#30363d",
             "zebra": "#151b23", "accent": "#a371f7", "lines": ["#a371f7", "#f0883e", "#4493f8", "#3fb950"],
             "ok": "#3fb950", "wait": "#d29922", "badge_bg": "#e6edf3", "badge_fg": "#0d1117"},
}
SPACE = {"bg": "#0b0f1a", "fg": "#e6edf3", "muted": "#9198a1", "faint": "#30363d", "accent": "#a371f7", "glow": "#c297ff"}


def esc(s) -> str:
    return _esc(str(s), {'"': "&quot;"})


def ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def item_id(p: Dict, it: Dict) -> str:
    return "%s#%d" % (p["repo"], it["number"])


def short_name(p: Dict) -> str:
    return p["repo"].split("/", 1)[1]


def owner(p: Dict) -> str:
    return p["repo"].split("/", 1)[0]


def mark(kind: str, x: float, y: float, s: float, color: str, bg: str, muted: str, ident: str = "") -> str:
    """Status glyph centred on (x, y). Filled = landed, hollow = pending, cross = closed."""
    h = s / 2
    data = ' data-item="%s"' % esc(ident) if ident else ""
    if kind == "merged":
        return '<rect%s x="%.1f" y="%.1f" width="%g" height="%g" rx="1.5" fill="%s"/>' % (data, x - h, y - h, s, s, color)
    if kind in ("open", "draft"):
        dash = ' stroke-dasharray="2 1.5"' if kind == "draft" else ""
        return ('<rect%s x="%.1f" y="%.1f" width="%g" height="%g" rx="1.5" fill="%s" stroke="%s" stroke-width="1.4"%s/>'
                % (data, x - h + .7, y - h + .7, s - 1.4, s - 1.4, bg, color, dash))
    if kind in ("closed", "report-closed"):
        k = s - 2
        return ('<path%s d="M%.1f %.1fl%g %gm0 -%gl-%g %g" stroke="%s" stroke-width="1.5" stroke-linecap="round"/>'
                % (data, x - h + 1, y - h + 1, k, k, k, k, k, muted))
    d = "M%.1f %.1fL%.1f %.1fL%.1f %.1fL%.1f %.1fZ" % (x, y - h - 1, x + h + 1, y, x, y + h + 1, x - h - 1, y)
    if kind == "fixed":
        return '<path%s d="%s" fill="%s"/>' % (data, d, color)
    if kind == "reported":
        return '<path%s d="%s" fill="%s" stroke="%s" stroke-width="1.4"/>' % (data, d, bg, color)
    raise ValueError(kind)


def _svg(w: int, h: int, title: str, desc: str, body: List[str], style: str = "") -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
            'aria-labelledby="t d"><title id="t">%s</title><desc id="d">%s</desc>'
            '<style>text{font-family:%s;font-variant-numeric:tabular-nums}%s'
            '@media (prefers-reduced-motion:reduce){*{animation:none!important}}</style>\n%s\n</svg>\n'
            % (w, h, w, h, esc(title), esc(desc), SANS, style, "\n".join(body)))


def headline(data: Dict) -> str:
    c = data["counts"]
    return "%d merged into %d upstream project%s · %d open · %d fixed" % (
        c["merged"], c["repos_merged"], "" if c["repos_merged"] == 1 else "s", c["open"], c["fixed"])


def describe(data: Dict) -> str:
    c = data["counts"]
    return ("%d pull requests merged into %d upstream projects, %d open, %d of my bug reports fixed upstream. "
            "Rung %d of 4: %s." % (c["merged"], c["repos_merged"], c["open"], c["fixed"], data["rung"], data["rung_name"]))


def _rung_meter(rung: int, x_right: float, y: float, on: str, off: str) -> List[str]:
    seg, gap = 22, 4
    x0 = x_right - (4 * seg + 3 * gap)
    return ['<rect x="%.1f" y="%.1f" width="%d" height="6" rx="3" fill="%s"/>' % (x0 + i * (seg + gap), y, seg, on if i < rung else off)
            for i in range(4)]


def _lanes(projects: List[Dict]) -> Tuple[List[Dict], int]:
    return projects[:MAX_LANES], max(0, len(projects) - MAX_LANES)


# ----------------------------------------------------------------------------- git graph

def _ticks(start: dt.datetime, end: dt.datetime) -> List[Tuple[dt.datetime, str]]:
    """Axis ticks that zoom out from days to weeks to months as history grows."""
    days = (end - start).total_seconds() / 86400
    out = []
    if days <= 24:
        step = 3 if days > 10 else 1
        d = dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
        while d <= end:
            out.append((d, d.strftime("%b ") + str(d.day)))
            d += dt.timedelta(days=step)
    elif days <= 120:
        d = dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc)
        d += dt.timedelta(days=(7 - d.weekday()) % 7 or 7)  # next Monday
        while d <= end:
            out.append((d, d.strftime("%b ") + str(d.day)))
            d += dt.timedelta(days=7)
    else:
        every = 1 if days <= 400 else 3
        y, m = start.year, start.month
        while True:
            m += 1
            if m > 12:
                y, m = y + 1, 1
            d = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
            if d > end:
                break
            if (m - 1) % every == 0:
                out.append((d, d.strftime("%b %Y") if m == 1 else d.strftime("%b")))
    return out


def _tracks(prs: List[Tuple[float, float]]) -> List[int]:
    """Greedy interval packing so overlapping PRs in one project don't overlap on screen."""
    ends: List[float] = []
    out = []
    for xs, xe in prs:
        for k, e in enumerate(ends):
            if xs > e + 14:
                ends[k] = xe
                out.append(k)
                break
        else:
            ends.append(xe)
            out.append(len(ends) - 1)
    return out


def gitgraph(data: Dict, theme: str, today: dt.date) -> str:
    t = THEMES[theme]
    lanes, more = _lanes(data["projects"])
    end = dt.datetime(today.year, today.month, today.day, 23, 59, tzinfo=dt.timezone.utc)
    first = min(ts(it["created"]) for p in data["projects"] for it in p["items"])
    start = first - dt.timedelta(days=1)
    x0, x1 = 214, W - 34
    span = (end - start).total_seconds()

    def X(d: dt.datetime) -> float:
        return x0 + (d - start).total_seconds() / span * (x1 - x0)

    plans = []
    for p in lanes:
        prs = sorted((it for it in p["items"] if it["kind"] == "pr"), key=lambda it: it["created"])
        spans = [(X(ts(it["created"])), X(ts(it["ended"])) if it.get("ended") else x1) for it in prs]
        tracks = _tracks(spans)
        plans.append((p, prs, spans, tracks, (max(tracks) + 1) if tracks else 0))
    top = 112
    heights = [34 + max(1, n) * 12 for *_, n in plans]
    H = top + sum(heights) + (62 if more else 46)
    nx = X(end)
    b = ['<rect x=".5" y=".5" width="%d" height="%d" rx="8" fill="%s" stroke="%s"/>' % (W - 1, H - 1, t["bg"], t["border"]),
         '<text x="24" y="34" font-family="%s" font-size="12.5" fill="%s">$ git log --graph --upstream --author=%s</text>' % (MONO, t["muted"], esc(data["login"])),
         '<text x="24" y="62" font-size="19" font-weight="700" fill="%s">%s</text>' % (t["fg"], esc(headline(data))),
         '<text x="%d" y="34" font-size="12.5" font-weight="600" fill="%s" text-anchor="end">rung %d/4 · %s</text>' % (W - 24, t["accent"], data["rung"], esc(data["rung_name"]))]
    b += _rung_meter(data["rung"], W - 24, 46, t["accent"], t["faint"])
    b.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-dasharray="3 3"/>' % (nx, top - 22, nx, H - 40, t["muted"]))
    b.append('<text x="%.1f" y="%d" font-size="10.5" fill="%s" text-anchor="end">today</text>' % (nx, top - 28, t["muted"]))
    for d, label in _ticks(start, end):
        x = X(d)
        if x < nx - 30:
            b.append('<text x="%.1f" y="%d" font-size="10.5" fill="%s" text-anchor="middle">%s</text>' % (x, H - 20, t["muted"], esc(label)))
    y = top
    for (p, prs, spans, tracks, n), lh in zip(plans, heights):
        ly = y + 6
        b.append('<text x="%d" y="%d" font-size="12.5" fill="%s" text-anchor="end">%s</text>' % (x0 - 18, ly + 4, t["fg"], esc(short_name(p))))
        b.append('<text x="%d" y="%d" font-size="10.5" fill="%s" text-anchor="end">%s · %d★</text>' % (x0 - 18, ly + 18, t["muted"], esc(owner(p)), p["stars"]))
        b.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"/>' % (x0 - 6, ly, x1, ly, t["faint"]))
        for it in p["items"]:
            if it["kind"] != "issue":
                continue
            xs = X(ts(it["created"]))
            if it["status"] == "fixed":
                xe = X(ts(it["ended"]))
                b.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1.2" stroke-dasharray="1 3"/>' % (xs, ly - 15, xe, ly - 15, t["accent"]))
                b.append(mark("fixed", xe, ly - 15, 8, t["accent"], t["bg"], t["muted"], item_id(p, it)))
                b.append(mark("reported", xs, ly - 15, 8, t["accent"], t["bg"], t["muted"]))
            else:
                b.append(mark(it["status"], xs, ly - 15, 8, t["accent"], t["bg"], t["muted"], item_id(p, it)))
        for it, (xs, xe), k in zip(prs, spans, tracks):
            yb = ly + 14 + k * 12
            ident = ' data-item="%s"' % esc(item_id(p, it))
            fork = "M%.1f %dC%.1f %d %.1f %d %.1f %d" % (xs, ly, xs + 6, ly, xs + 2, yb, xs + 10, yb)
            st = it["status"]
            if st == "merged":
                b.append('<path%s d="%sL%.1f %dC%.1f %d %.1f %d %.1f %d" fill="none" stroke="%s" stroke-width="2.5"/>'
                         % (ident, fork, max(xs + 10, xe - 10), yb, xe - 2, yb, xe - 6, ly, xe, ly, t["accent"]))
                b.append('<circle cx="%.1f" cy="%d" r="5.5" fill="%s" stroke="%s" stroke-width="2"/>' % (xe, ly, t["accent"], t["bg"]))
            elif st == "closed":
                b.append('<path%s d="%sL%.1f %d" fill="none" stroke="%s" stroke-width="2"/>' % (ident, fork, max(xs + 10, xe), yb, t["muted"]))
                b.append(mark("closed", max(xs + 10, xe) + 5, yb, 9, t["muted"], t["bg"], t["muted"]))
            else:
                b.append('<path%s class="flow" d="%sL%.1f %d" fill="none" stroke="%s" stroke-width="2.5"%s/>'
                         % (ident, fork, nx, yb, t["accent"], ' opacity=".6"' if st == "draft" else ""))
                b.append('<circle cx="%.1f" cy="%d" r="4.5" fill="%s" stroke="%s" stroke-width="2"/>' % (nx, yb, t["bg"], t["accent"]))
            b.append('<circle cx="%.1f" cy="%d" r="3.5" fill="%s"/>' % (xs, ly, t["fg"]))
        y += lh
    if more:
        b.append('<text x="%d" y="%d" font-size="11" fill="%s" text-anchor="end">+%d more project%s</text>' % (x0 - 18, y + 4, t["muted"], more, "" if more == 1 else "s"))
    style = ".flow{stroke-dasharray:6 4;animation:flow 1s linear infinite}@keyframes flow{to{stroke-dashoffset:-10}}"
    return _svg(W, H, "Upstream contributions: git graph", describe(data), b, style)


# ----------------------------------------------------------------------------- transit map

def transit(data: Dict, theme: str) -> str:
    t = THEMES[theme]
    lanes, more = _lanes(data["projects"])
    orgs: Dict[str, List[Dict]] = {}
    for p in lanes:
        orgs.setdefault(owner(p), []).append(p)
    names = list(orgs)[:4]
    hidden = sum(len(orgs[o]) for o in list(orgs)[4:])
    n_lines = len(names)
    first_y, gap = 170, 100
    ys = [first_y + i * gap for i in range(n_lines)]
    oy = ys[(n_lines - 1) // 2] if n_lines % 2 else (ys[n_lines // 2 - 1] + ys[n_lines // 2]) / 2
    ox = 70
    H = ys[-1] + 100
    widest = max(len(orgs[o]) for o in names)
    step = min(82, (W - 360) / max(1, widest))
    b = ['<rect x=".5" y=".5" width="%d" height="%d" rx="8" fill="%s" stroke="%s"/>' % (W - 1, H - 1, t["bg"], t["border"]),
         '<text x="24" y="38" font-size="18" font-weight="800" letter-spacing="3" fill="%s">UPSTREAM TRANSIT</text>' % t["fg"],
         '<text x="24" y="60" font-size="12.5" fill="%s">%d line%s · %d stations · %d merged · %d open · %d fixed</text>'
         % (t["muted"], n_lines, "" if n_lines == 1 else "s", len(lanes), data["counts"]["merged"], data["counts"]["open"], data["counts"]["fixed"]),
         '<rect x="%d" y="20" width="200" height="48" rx="6" fill="%s"/>' % (W - 224, t["badge_bg"]),
         '<text x="%d" y="38" font-size="10" letter-spacing="1.5" fill="%s" text-anchor="middle" opacity=".7">YOU ARE IN</text>' % (W - 124, t["badge_fg"]),
         '<text x="%d" y="57" font-size="14" font-weight="700" fill="%s" text-anchor="middle">Zone %d · %s</text>' % (W - 124, t["badge_fg"], data["rung"], esc(data["rung_name"]))]
    for li, org in enumerate(names):
        col, ly = t["lines"][li % 4], ys[li]
        stations = orgs[org]
        end = 250 + len(stations) * step
        if ly == oy:
            path = "M%d %.1fL%.1f %.1f" % (ox, oy, end, ly)
        else:
            dy = abs(ly - oy)
            path = "M%d %.1fL%d %.1fL%.1f %.1fL%.1f %.1f" % (ox, oy, ox + 40, oy, ox + 40 + dy, ly, end, ly)
        b.append('<path d="%s" fill="none" stroke="%s" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>' % (path, col))
        b.append('<rect x="%.1f" y="%.1f" width="6" height="22" rx="2" fill="%s"/>' % (end - 3, ly - 11, col))
        b.append('<text x="%.1f" y="%.1f" font-size="11" font-weight="700" fill="%s">%s line</text>' % (end + 12, ly + 4, col, esc(org)))
        for i, p in enumerate(stations):
            sx = 250 + i * step + 26
            if p["merged"]:
                b.append('<circle cx="%.1f" cy="%.1f" r="11" fill="%s" stroke="%s" stroke-width="5"/>' % (sx, ly, t["bg"], t["fg"]))
            else:
                b.append('<rect x="%.1f" y="%.1f" width="5" height="14" fill="%s"/>' % (sx - 2.5, ly - 18, col))
            b.append('<text transform="translate(%.1f %.1f) rotate(-32)" font-size="12" font-weight="%d" fill="%s">%s</text>'
                     % (sx + 4, ly - 22, 700 if p["merged"] else 400, t["fg"], esc(short_name(p))))
            k = len(p["items"])
            for j, it in enumerate(p["items"]):
                b.append(mark(it["status"], sx - (k - 1) * 6.5 + j * 13, ly + 26, 9, col, t["bg"], t["muted"], item_id(p, it)))
    b.append('<circle cx="%d" cy="%.1f" r="16" fill="%s" stroke="%s" stroke-width="6"/>' % (ox, oy, t["bg"], t["fg"]))
    b.append('<text x="%d" y="%.1f" font-size="12" font-weight="700" fill="%s" text-anchor="middle">%s</text>' % (ox, oy + 36, t["fg"], esc(data["login"])))
    ly = H - 26
    b.append('<circle cx="34" cy="%d" r="7" fill="%s" stroke="%s" stroke-width="4"/><text x="48" y="%d" font-size="11.5" fill="%s">interchange = code merged</text>' % (ly, t["bg"], t["fg"], ly + 4, t["muted"]))
    b.append('<rect x="232" y="%d" width="4" height="12" fill="%s"/><text x="244" y="%d" font-size="11.5" fill="%s">stop = work in progress</text>' % (ly - 6, t["muted"], ly + 4, t["muted"]))
    for j, (k, label) in enumerate((("merged", "merged"), ("open", "open"), ("fixed", "fixed"), ("reported", "report"), ("closed", "closed"))):
        b.append(mark(k, 420 + j * 66, ly, 9, t["accent"], t["bg"], t["muted"]))
        b.append('<text x="%d" y="%d" font-size="11.5" fill="%s">%s</text>' % (430 + j * 66, ly + 4, t["muted"], label))
    if more + hidden:
        b.append('<text x="%d" y="%d" font-size="11" fill="%s" text-anchor="end">+%d more stations</text>' % (W - 24, H - 46, t["muted"], more + hidden))
    return _svg(W, H, "Upstream contributions: transit map", describe(data), b)


# ----------------------------------------------------------------------------- orbit

def orbit(data: Dict) -> str:
    s = SPACE
    H, cx, cy = 500, 380, 268
    lanes, more = _lanes(data["projects"])
    rnd = random.Random(7)
    b = ['<rect width="%d" height="%d" rx="10" fill="%s"/>' % (W, H, s["bg"])]
    for _ in range(110):
        b.append('<circle cx="%.1f" cy="%.1f" r="%g" fill="#fff" opacity=".45"/>'
                 % (rnd.uniform(0, W), rnd.uniform(0, H), rnd.choice((.5, .7, .9, 1.2))))
    c = data["counts"]
    b += ['<text x="24" y="38" font-size="18" font-weight="800" letter-spacing="3" fill="#fff">UPSTREAM ORBIT</text>',
          '<text x="24" y="60" font-size="12.5" fill="%s">%d worlds · code landed on %d · %d PRs in flight · %d reports fixed</text>'
          % (s["muted"], len(data["projects"]), c["repos_merged"], c["open"], c["fixed"]),
          '<text x="%d" y="38" font-size="13" font-weight="700" fill="%s" text-anchor="end">rung %d/4 · %s</text>' % (W - 24, s["glow"], data["rung"], esc(data["rung_name"]))]
    b += _rung_meter(data["rung"], W - 24, 50, s["glow"], s["faint"])
    n = len(lanes)
    step = min(44, (350 - 78) / max(1, n - 1))
    angles = [200, 335, 30, 160, 255, 100, 300, 60]
    for i, p in enumerate(lanes):
        rx = 78 + i * step
        b.append('<ellipse cx="%d" cy="%d" rx="%.1f" ry="%.1f" fill="none" stroke="%s"%s/>'
                 % (cx, cy, rx, rx * .42, s["faint"], "" if p["merged"] else ' stroke-dasharray="2 4"'))
    b.append('<circle cx="%d" cy="%d" r="22" fill="%s" opacity=".25"/><circle cx="%d" cy="%d" r="13" fill="%s"/>' % (cx, cy, s["accent"], cx, cy, s["glow"]))
    b.append('<text x="%d" y="%d" font-size="11" fill="%s" text-anchor="middle">%s</text>' % (cx, cy + 38, s["glow"], esc(data["login"])))
    for i, p in enumerate(lanes):
        rx = 78 + i * step
        a = math.radians(angles[i % len(angles)])
        px, py = cx + rx * math.cos(a), cy + rx * .42 * math.sin(a)
        pr = 4 + math.sqrt(p["stars"]) / 3.2
        if p["merged"]:
            b.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"/>' % (px, py, pr, s["accent"]))
        else:
            b.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" stroke-width="2"/>' % (px, py, pr, s["bg"], s["accent"]))
        moons = []
        k = len(p["items"])
        for j, it in enumerate(p["items"]):
            ma = 2 * math.pi * j / k
            moons.append(mark(it["status"], px + (pr + 9) * math.cos(ma), py + (pr + 9) * math.sin(ma), 6,
                              s["glow"], s["bg"], s["muted"], item_id(p, it)))
        moving = any(it["status"] in ("open", "draft", "reported") for it in p["items"])
        spin = ('<animateTransform attributeName="transform" type="rotate" from="0 %.1f %.1f" to="360 %.1f %.1f" dur="%ds" repeatCount="indefinite"/>'
                % (px, py, px, py, 14 + i * 4)) if moving else ""
        b.append('<g>%s%s</g>' % ("".join(moons), spin))
        left = math.cos(a) < 0
        b.append('<text x="%.1f" y="%.1f" font-size="11.5" fill="%s" text-anchor="%s">%s <tspan fill="%s">%d★</tspan></text>'
                 % (px - pr - 16 if left else px + pr + 16, py + 4, s["fg"], "end" if left else "start",
                    esc(short_name(p)), s["muted"], p["stars"]))
    foot = "planet size = project stars · solid = my code merged there · moons = my PRs and reports (filled = landed)"
    if more:
        foot += " · +%d more" % more
    b.append('<text x="24" y="%d" font-size="11" fill="%s">%s</text>' % (H - 18, s["muted"], esc(foot)))
    return _svg(W, H, "Upstream contributions: orbit", describe(data), b)


# ----------------------------------------------------------------------------- pipeline

def pipeline(data: Dict, theme: str) -> str:
    t = THEMES[theme]
    prs = [(p, it) for p in data["projects"] for it in p["items"] if it["kind"] == "pr"]
    rank = {"open": 0, "draft": 0, "merged": 1, "closed": 2}
    prs.sort(key=lambda pi: (rank[pi[1]["status"]], -ts(pi[1].get("ended") or pi[1]["created"]).timestamp()))
    shown, hidden = prs[:MAX_PIPELINE_ROWS], len(prs) - MAX_PIPELINE_ROWS
    reports = [(p, it) for p in data["projects"] for it in p["items"] if it["kind"] == "issue"]
    top, rh = 112, 34
    H = top + len(shown) * rh + (78 if hidden > 0 else 60)
    sx = [372, 482, 592]
    reviewed = sum(1 for _, it in prs if it["status"] == "merged" or it.get("reviews"))
    c = data["counts"]
    b = ['<rect x=".5" y=".5" width="%d" height="%d" rx="8" fill="%s" stroke="%s"/>' % (W - 1, H - 1, t["bg"], t["border"]),
         '<circle cx="34" cy="34" r="9" fill="%s"/><path d="M29.5 34l3 3 6-6" stroke="%s" stroke-width="2" fill="none" stroke-linecap="round"/>' % (t["ok"], t["bg"]),
         '<text x="52" y="39" font-size="16" font-weight="700" fill="%s">upstream / contribution pipeline</text>' % t["fg"],
         '<text x="24" y="66" font-size="12.5" fill="%s">%d opened → %d reviewed → <tspan font-weight="700" fill="%s">%d merged</tspan> · %d reports fixed · rung %d/4 %s</text>'
         % (t["muted"], len(prs), reviewed, t["fg"], c["merged"], c["fixed"], data["rung"], esc(data["rung_name"]))]
    for x, label in zip(sx, ("OPENED", "REVIEWED", "MERGED")):
        b.append('<text x="%d" y="%d" font-size="11" font-weight="600" fill="%s" text-anchor="middle">%s</text>' % (x, top - 18, t["muted"], label))

    def node(state: str, x: float, y: float) -> str:
        if state == "ok":
            return ('<circle cx="%.1f" cy="%.1f" r="9" fill="%s"/><path d="M%.1f %.1fl3 3 6-6" stroke="%s" stroke-width="2" fill="none" stroke-linecap="round"/>'
                    % (x, y, t["ok"], x - 4.5, y, t["bg"]))
        if state == "fail":
            return ('<circle cx="%.1f" cy="%.1f" r="9" fill="%s"/><path d="M%.1f %.1fl7 7m0 -7l-7 7" stroke="%s" stroke-width="2" stroke-linecap="round"/>'
                    % (x, y, t["muted"], x - 3.5, y - 3.5, t["bg"]))
        if state == "wait":
            return ('<circle cx="%.1f" cy="%.1f" r="8" fill="none" stroke="%s" stroke-width="2" stroke-dasharray="12 38">'
                    '<animateTransform attributeName="transform" type="rotate" from="0 %.1f %.1f" to="360 %.1f %.1f" dur="1.2s" repeatCount="indefinite"/>'
                    '</circle><circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>' % (x, y, t["wait"], x, y, x, y, x, y, t["wait"]))
        return '<circle cx="%.1f" cy="%.1f" r="8" fill="none" stroke="%s" stroke-width="1.5"/>' % (x, y, t["faint"])

    for i, (p, it) in enumerate(shown):
        y = top + i * rh
        if i % 2 == 0:
            b.append('<rect x="12" y="%.1f" width="%d" height="%d" rx="4" fill="%s"/>' % (y - rh / 2, W - 24, rh, t["zebra"]))
        b.append('<text data-item="%s" x="24" y="%d" font-size="12.5" fill="%s"><tspan font-family="%s" fill="%s">#%d</tspan>  %s</text>'
                 % (esc(item_id(p, it)), y + 4, t["fg"], MONO, t["muted"], it["number"], esc(short_name(p))))
        st = it["status"]
        if st == "merged":
            d = dt.date.fromisoformat(it["date"])
            states, note = ["ok", "ok", "ok"], "merged %s %d" % (d.strftime("%b"), d.day)
        elif st == "closed":
            states = ["ok", "todo", "fail"]
            note = "superseded by " + it["superseded_by"]["ref"] if it.get("superseded_by") else "closed"
        elif it.get("reviews"):
            states, note = ["ok", "ok", "todo"], "%d review%s" % (it["reviews"], "" if it["reviews"] == 1 else "s")
        else:
            states, note = ["ok", "wait", "todo"], "draft" if st == "draft" else "awaiting review"
        for j in range(2):
            b.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"/>'
                     % (sx[j] + 10, y, sx[j + 1] - 10, y, t["ok"] if states[j + 1] == "ok" else t["faint"]))
        b += [node(s_, x, y) for s_, x in zip(states, sx)]
        b.append('<text x="626" y="%d" font-size="11.5" fill="%s">%s</text>' % (y + 4, t["muted"], esc(note)))
    fy = top + len(shown) * rh + 8
    if hidden > 0:
        b.append('<text x="24" y="%d" font-size="11" fill="%s">+%d more PRs</text>' % (fy + 6, t["muted"], hidden))
        fy += 18
    b.append('<text x="24" y="%d" font-size="11.5" font-weight="600" fill="%s">REPORTS</text>' % (fy + 16, t["muted"]))
    x = 92.0
    for p, it in reports:
        label = "#%d %s" % (it["number"], {"fixed": "fixed", "reported": "open"}.get(it["status"], "closed"))
        width = 22 + len(label) * 6.4
        if x + width > W - 24:
            break
        b.append(mark(it["status"], x, fy + 12, 9, t["accent"], t["bg"], t["muted"], item_id(p, it)))
        b.append('<text x="%.1f" y="%d" font-size="11.5" fill="%s">%s</text>' % (x + 10, fy + 16, t["fg"], esc(label)))
        x += width
    return _svg(W, H, "Upstream contributions: pipeline", describe(data), b)
