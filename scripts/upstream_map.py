#!/usr/bin/env python3
"""Build the Upstream Map: merged and open work in repositories I don't own.

Discovery uses `gh search prs` and `gh search issues`. Details (stars, merge
state, reviews, what closed an issue) come from one GraphQL query per repo.
Outputs are computed in memory first and written only if every check passes,
so a failed API call can never blank the README.

Standard library only. Method and rung rules: UPSTREAM_MAP.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from collections import OrderedDict
from typing import Callable, Dict, List, Optional
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = "<!-- upstream:start -->"
END = "<!-- upstream:end -->"

RUNGS = [
    ("First merge", "at least 1 merged upstream PR"),
    ("Multi-project", "merged into at least 3 distinct upstream repos"),
    ("Repeat", "at least 3 merged PRs in a single upstream repo"),
    ("Trusted", "GitHub lists you as COLLABORATOR or MEMBER on an upstream repo"),
]

THEMES = {
    "light": {"bg": "#ffffff", "border": "#d0d7de", "fg": "#1f2328",
              "muted": "#59636e", "faint": "#d8dee4", "accent": "#8250df"},
    "dark": {"bg": "#0d1117", "border": "#30363d", "fg": "#e6edf3",
             "muted": "#9198a1", "faint": "#30363d", "accent": "#a371f7"},
}
FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', "
        "Helvetica, Arial, sans-serif")


class MapError(RuntimeError):
    """Raised for any condition where writing output would be unsafe."""


# --------------------------------------------------------------------------
# Fetching

Runner = Callable[[List[str]], str]


def run_gh(args: List[str]) -> str:
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MapError("gh %s failed (%d): %s" % (args[0:2], proc.returncode,
                                                   proc.stderr.strip()[:500]))
    return proc.stdout


PR_FIELDS = "repository,number,title,url,state,createdAt,authorAssociation"
ISSUE_FIELDS = "repository,number,title,url,state,createdAt,authorAssociation,isPullRequest"

CLOSER = """closer { __typename
  ... on PullRequest { number url merged repository { nameWithOwner } }
  ... on Commit { oid url } }"""


def graphql_for_repo(owner: str, name: str, prs: List[int], issues: List[int]) -> str:
    parts = ["nameWithOwner url stargazerCount"]
    for n in prs:
        parts.append(
            "p%d: pullRequest(number: %d) { number title url state merged mergedAt "
            "closedAt isDraft authorAssociation "
            "reviews(first: 100) { nodes { state author { login } } } "
            "closingIssuesReferences(first: 10) { nodes { number repository { nameWithOwner } } } }"
            % (n, n))
    for n in issues:
        parts.append(
            "i%d: issue(number: %d) { number title url state stateReason closedAt "
            "authorAssociation "
            "timelineItems(itemTypes: [CLOSED_EVENT], last: 1) { nodes { ... on ClosedEvent { %s } } } "
            "closedByPullRequestsReferences(first: 10, includeClosedPrs: false) "
            "{ nodes { number url merged repository { nameWithOwner } } } }"
            % (n, n, CLOSER))
    return 'query { repository(owner: %s, name: %s) { %s } }' % (
        json.dumps(owner), json.dumps(name), " ".join(parts))


def fetch(login: str, gh: Runner = run_gh) -> Dict:
    """Return raw API data: search results plus per-repo GraphQL details."""
    prs = json.loads(gh(["search", "prs", "--author", login, "--limit", "1000",
                         "--json", PR_FIELDS]))
    issues = json.loads(gh(["search", "issues", "--author", login, "--limit", "1000",
                            "--json", ISSUE_FIELDS]))
    if not isinstance(prs, list) or not isinstance(issues, list):
        raise MapError("unexpected search response shape")

    def upstream(item: Dict) -> bool:
        return not item["repository"]["nameWithOwner"].lower().startswith(login.lower() + "/")

    prs = [p for p in prs if upstream(p)]
    issues = [i for i in issues if upstream(i) and not i.get("isPullRequest")]

    by_repo: "OrderedDict[str, Dict[str, List[int]]]" = OrderedDict()
    for p in prs:
        by_repo.setdefault(p["repository"]["nameWithOwner"], {"prs": [], "issues": []})["prs"].append(p["number"])
    for i in issues:
        by_repo.setdefault(i["repository"]["nameWithOwner"], {"prs": [], "issues": []})["issues"].append(i["number"])

    details = {}
    for full, nums in by_repo.items():
        owner, name = full.split("/", 1)
        query = graphql_for_repo(owner, name, sorted(nums["prs"]), sorted(nums["issues"]))
        resp = json.loads(gh(["api", "graphql", "-f", "query=" + query]))
        if resp.get("errors") or not (resp.get("data") or {}).get("repository"):
            raise MapError("GraphQL error for %s: %s" % (full, resp.get("errors")))
        details[full] = resp["data"]["repository"]
    return {"login": login, "search_prs": prs, "search_issues": issues, "details": details}


# --------------------------------------------------------------------------
# Computing

def _ref(full_repo: str, home_repo: str, number: int) -> str:
    return "#%d" % number if full_repo == home_repo else "%s#%d" % (full_repo, number)


def _fixer(issue: Dict) -> Optional[Dict]:
    """The merged PR or commit that closed a COMPLETED issue, if any."""
    if issue["state"] != "CLOSED" or issue.get("stateReason") != "COMPLETED":
        return None
    for node in issue["timelineItems"]["nodes"]:
        closer = (node or {}).get("closer")
        if closer and closer["__typename"] == "PullRequest" and closer["merged"]:
            return {"repo": closer["repository"]["nameWithOwner"], "number": closer["number"], "url": closer["url"]}
        if closer and closer["__typename"] == "Commit":
            return {"repo": None, "number": None, "sha": closer["oid"][:7], "url": closer["url"]}
    for pr in issue["closedByPullRequestsReferences"]["nodes"]:
        if pr["merged"]:
            return {"repo": pr["repository"]["nameWithOwner"], "number": pr["number"], "url": pr["url"]}
    return None


def compute(raw: Dict) -> Dict:
    login = raw["login"]
    if not raw["search_prs"]:
        raise MapError("search returned zero upstream PRs; refusing to write")

    # Search is only used for discovery; every PR found must have details.
    for p in raw["search_prs"]:
        full = p["repository"]["nameWithOwner"]
        if "p%d" % p["number"] not in raw["details"].get(full, {}):
            raise MapError("missing details for %s#%d" % (full, p["number"]))

    projects = []
    associations = set()
    for full, repo in raw["details"].items():
        prs = [v for k, v in repo.items() if k.startswith("p") and k[1:].isdigit()]
        issues = [v for k, v in repo.items() if k.startswith("i") and k[1:].isdigit()]
        issue_fix = {i["number"]: _fixer(i) for i in issues}
        items = []
        for pr in prs:
            associations.add(pr["authorAssociation"])
            reviews = [r for r in pr["reviews"]["nodes"]
                       if r.get("author") and r["author"]["login"].lower() != login.lower()]
            item = {"kind": "pr", "number": pr["number"], "title": pr["title"], "url": pr["url"]}
            if pr["merged"]:
                item.update(status="merged", date=pr["mergedAt"][:10])
            elif pr["state"] == "OPEN":
                item.update(status="draft" if pr["isDraft"] else "open", reviews=len(reviews))
            else:
                item.update(status="closed", date=pr["closedAt"][:10])
                for ref in pr["closingIssuesReferences"]["nodes"]:
                    fix = issue_fix.get(ref["number"]) if ref["repository"]["nameWithOwner"] == full else None
                    if fix and fix.get("number") and not (fix["repo"] == full and fix["number"] == pr["number"]):
                        item["superseded_by"] = {"ref": _ref(fix["repo"], full, fix["number"]), "url": fix["url"]}
                        break
            items.append(item)
        for issue in issues:
            associations.add(issue["authorAssociation"])
            fix = issue_fix[issue["number"]]
            item = {"kind": "issue", "number": issue["number"], "title": issue["title"], "url": issue["url"]}
            if fix:
                label = _ref(fix["repo"], full, fix["number"]) if fix.get("number") else fix["sha"]
                item.update(status="fixed", fixed_by={"ref": label, "url": fix["url"]})
            elif issue["state"] == "OPEN":
                item.update(status="reported")
            else:
                item.update(status="report-closed")
            items.append(item)
        order = {"merged": 0, "open": 1, "draft": 1, "closed": 2, "fixed": 3, "reported": 4, "report-closed": 5}
        items.sort(key=lambda it: (order[it["status"]], -it["number"]))
        projects.append({"repo": full, "url": repo["url"], "stars": repo["stargazerCount"], "items": items,
                         "merged": sum(1 for it in items if it["status"] == "merged")})
    projects.sort(key=lambda p: (-p["merged"], -p["stars"], p["repo"].lower()))

    every = [it for p in projects for it in p["items"]]
    merged = sum(p["merged"] for p in projects)
    repos_merged = sum(1 for p in projects if p["merged"])
    best = max([p["merged"] for p in projects] or [0])
    trusted = bool(associations & {"COLLABORATOR", "MEMBER"})
    met = [merged >= 1, repos_merged >= 3, best >= 3, trusted]
    rung = 0
    for ok in met:
        if not ok:
            break
        rung += 1
    return {
        "login": login,
        "counts": {
            "merged": merged,
            "repos_merged": repos_merged,
            "best_single_repo": best,
            "open": sum(1 for it in every if it["status"] in ("open", "draft")),
            "in_review": sum(1 for it in every if it["status"] in ("open", "draft") and it["reviews"] > 0),
            "closed": sum(1 for it in every if it["status"] == "closed"),
            "fixed": sum(1 for it in every if it["status"] == "fixed"),
            "reported_open": sum(1 for it in every if it["status"] == "reported"),
        },
        "rung": rung,
        "rung_name": RUNGS[rung - 1][0] if rung else "Not yet ranked",
        "rungs_met": met,
        "projects": projects,
    }


# --------------------------------------------------------------------------
# Rendering

def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def summary_line(data: Dict) -> str:
    c = data["counts"]
    return "%d merged · %d open · %s fixed upstream" % (
        c["merged"], c["open"], _plural(c["fixed"], "report"))


def alt_text(data: Dict) -> str:
    c = data["counts"]
    return ("Upstream Map: %s merged into %s, %d open, %s fixed upstream. Rung %d of 4, %s." % (
        _plural(c["merged"], "PR"), _plural(c["repos_merged"], "project"), c["open"],
        _plural(c["fixed"], "report"), data["rung"], data["rung_name"]))


def _mark(kind: str, x: float, y: float, t: Dict) -> str:
    """12px marks. Filled = landed, hollow = pending, muted cross = closed."""
    a = t["accent"]
    if kind == "merged":
        return '<rect x="%.1f" y="%.1f" width="12" height="12" rx="2" fill="%s"/>' % (x, y, a)
    if kind in ("open", "draft"):
        dash = ' stroke-dasharray="2 2"' if kind == "draft" else ""
        return ('<rect x="%.1f" y="%.1f" width="10.5" height="10.5" rx="1.5" fill="none" stroke="%s" '
                'stroke-width="1.5"%s/>' % (x + .75, y + .75, a, dash))
    if kind in ("closed", "report-closed"):
        return ('<path d="M%.1f %.1fl9 9m0 -9l-9 9" stroke="%s" stroke-width="1.6" stroke-linecap="round"/>'
                % (x + 1.5, y + 1.5, t["muted"]))
    cx, cy = x + 6, y + 6
    diamond = "M%.1f %.1fL%.1f %.1fL%.1f %.1fL%.1f %.1fZ" % (cx, cy - 6.5, cx + 6.5, cy, cx, cy + 6.5, cx - 6.5, cy)
    if kind == "fixed":
        return '<path d="%s" fill="%s"/>' % (diamond, a)
    if kind == "reported":
        small = "M%.1f %.1fL%.1f %.1fL%.1f %.1fL%.1f %.1fZ" % (cx, cy - 5.6, cx + 5.6, cy, cx, cy + 5.6, cx - 5.6, cy)
        return '<path d="%s" fill="none" stroke="%s" stroke-width="1.5"/>' % (small, a)
    raise ValueError(kind)


def render_svg(data: Dict, theme: str, stamp: str) -> str:
    t = THEMES[theme]
    W, pad, row_h = 560, 24, 30
    rows = data["projects"]
    top = 104
    legend_y = top + len(rows) * row_h + 14
    H = legend_y + 50
    name_x, stars_end, marks_x = pad, 300, 320
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
        'role="img" aria-labelledby="t d">' % (W, H, W, H),
        '<title id="t">Upstream Map</title><desc id="d">%s</desc>' % escape(alt_text(data)),
        '<style>text{font-family:%s;font-variant-numeric:tabular-nums}'
        '.h{font-size:18px;font-weight:600;fill:%s}.s{font-size:13px;fill:%s}'
        '.n{font-size:13.5px;fill:%s}.o{fill:%s}.k{font-size:12.5px;fill:%s}'
        '.r{font-size:13px;font-weight:600;fill:%s}.f{font-size:11px;fill:%s}</style>'
        % (FONT, t["fg"], t["muted"], t["fg"], t["muted"], t["muted"], t["accent"], t["muted"]),
        '<rect x=".5" y=".5" width="%d" height="%d" rx="6" fill="%s" stroke="%s"/>' % (W - 1, H - 1, t["bg"], t["border"]),
        '<text x="%d" y="40" class="h">Upstream Map</text>' % pad,
        '<text x="%d" y="64" class="s">%s</text>' % (pad, escape(summary_line(data))),
        '<text x="%d" y="40" class="r" text-anchor="end">rung %d/4 · %s</text>'
        % (W - pad, data["rung"], escape(data["rung_name"])),
    ]
    # Four-segment rung meter under the rung label.
    seg_w, gap = 26, 4
    x0 = W - pad - (4 * seg_w + 3 * gap)
    for i in range(4):
        filled = i < data["rung"]
        out.append('<rect x="%d" y="54" width="%d" height="6" rx="3" fill="%s"/>'
                   % (x0 + i * (seg_w + gap), seg_w, t["accent"] if filled else t["faint"]))
    out.append('<line x1="%d" y1="84" x2="%d" y2="84" stroke="%s"/>' % (pad, W - pad, t["faint"]))

    for idx, p in enumerate(rows):
        y = top + idx * row_h + 10
        owner, name = p["repo"].split("/", 1)
        label_owner, label_name = owner + "/", name
        if len(p["repo"]) > 32:
            label_name = name[: max(4, 31 - len(label_owner))] + "…"
        out.append('<text x="%d" y="%d" class="n"><tspan class="o">%s</tspan>%s</text>'
                   % (name_x, y, escape(label_owner), escape(label_name)))
        out.append('<text x="%d" y="%d" class="k" text-anchor="end">%s ★</text>' % (stars_end, y, p["stars"]))
        max_marks = (W - pad - marks_x) // 18
        shown = p["items"][:max_marks]
        for j, it in enumerate(shown):
            out.append(_mark(it["status"], marks_x + j * 18, y - 10.5, t))
        if len(p["items"]) > len(shown):
            out.append('<text x="%d" y="%d" class="k">+%d</text>'
                       % (marks_x + len(shown) * 18 + 2, y, len(p["items"]) - len(shown)))

    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>' % (pad, legend_y - 6, W - pad, legend_y - 6, t["faint"]))
    lx, ly = pad, legend_y + 16
    for kind, label in (("merged", "merged"), ("open", "open PR"), ("closed", "closed"),
                        ("fixed", "report → fixed"), ("reported", "report open")):
        out.append(_mark(kind, lx, ly - 10.5, t))
        out.append('<text x="%d" y="%d" class="k">%s</text>' % (lx + 18, ly, escape(label)))
        lx += 18 + int(len(label) * 6.1) + 24
    out.append('<text x="%d" y="%d" class="f" text-anchor="end">%s</text>' % (W - pad, ly + 20, escape(stamp)))
    out.append('</svg>')
    return "\n".join(out) + "\n"


def render_section(data: Dict, stamp: str) -> str:
    c = data["counts"]
    lines = [
        START,
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" srcset="assets/upstream-map-dark.svg">',
        '  <img alt="%s" src="assets/upstream-map-light.svg" width="560">' % escape(alt_text(data), {'"': "&quot;"}),
        "</picture>",
        "",
        "## Upstream work",
        "",
        "**%s merged into %s** · %d open%s · %s fixed upstream · [rung %d of 4](UPSTREAM_MAP.md)" % (
            _plural(c["merged"], "PR"), _plural(c["repos_merged"], "project"), c["open"],
            " (%d in review)" % c["in_review"] if c["in_review"] else "",
            _plural(c["fixed"], "report"), data["rung"]),
    ]
    for p in data["projects"]:
        owner, name = p["repo"].split("/", 1)
        lines += ["", "**[%s](%s)** · %s · %s ★" % (name, p["url"], owner, p["stars"]), ""]
        work = []
        for it in p["items"]:
            s = it["status"]
            if s == "merged":
                status = "**merged** %s" % it["date"]
            elif s in ("open", "draft"):
                status = "**%s** · %s" % (s, _plural(it["reviews"], "review") if it["reviews"] else "awaiting review")
            elif s == "closed":
                status = "**closed**"
                if it.get("superseded_by"):
                    status += " · superseded by [%s](%s)" % (it["superseded_by"]["ref"], it["superseded_by"]["url"])
            elif s == "fixed":
                status = "**report → fixed** in [%s](%s)" % (it["fixed_by"]["ref"], it["fixed_by"]["url"])
            elif s == "reported":
                status = "**report** · open"
            else:
                status = "**report** · closed"
            work.append("%s · [#%d](%s) %s" % (status, it["number"], it["url"], it["title"]))
        lines += ["- " + w.replace("\n", " ") for w in work]
    lines += [
        "",
        "<sub>Generated from the GitHub API by [scripts/upstream_map.py](scripts/upstream_map.py), "
        "refreshed daily. Counts only repositories I don't own. %s.</sub>" % escape(stamp[0].upper() + stamp[1:]),
        END,
    ]
    return "\n".join(lines)


def splice(readme: str, section: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1 or readme.index(START) > readme.index(END):
        raise MapError("README must contain exactly one %s ... %s block" % (START, END))
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    return head + section + tail


# --------------------------------------------------------------------------
# Main

def build(raw: Dict, previous: Optional[Dict], readme: str, today: str, allow_shrink: bool) -> Dict[str, str]:
    """Return {relative path: content}. Raises MapError instead of returning unsafe output."""
    data = compute(raw)
    if previous and not allow_shrink:
        before, after = previous["data"]["counts"]["merged"], data["counts"]["merged"]
        if after < before:
            raise MapError("merged count dropped from %d to %d; rerun with --allow-shrink if real" % (before, after))
    changed = today if not previous or previous["data"] != data else previous["changed"]
    stamp = "last change %s UTC" % changed
    return {
        "data/upstream.json": json.dumps({"changed": changed, "data": data}, indent=2, ensure_ascii=False) + "\n",
        "assets/upstream-map-light.svg": render_svg(data, "light", stamp),
        "assets/upstream-map-dark.svg": render_svg(data, "dark", stamp),
        "README.md": splice(readme, render_section(data, stamp)),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--login", default="sasmith-14")
    ap.add_argument("--allow-shrink", action="store_true", help="accept a lower merged count than last run")
    ap.add_argument("--raw", help="read raw API data from this JSON file instead of calling gh")
    ap.add_argument("--save-raw", help="also write the raw API data to this file")
    ap.add_argument("--check", action="store_true", help="compute and validate, write nothing")
    args = ap.parse_args(argv)

    def read(rel: str) -> Optional[str]:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    try:
        if args.raw:
            with open(args.raw, encoding="utf-8") as fh:
                raw = json.load(fh)
        else:
            raw = fetch(args.login)
        if args.save_raw:
            with open(args.save_raw, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
        prev_text = read("data/upstream.json")
        outputs = build(raw, json.loads(prev_text) if prev_text else None, read("README.md") or "",
                        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"), args.allow_shrink)
    except MapError as err:
        print("upstream-map: %s. Nothing written." % err, file=sys.stderr)
        return 1

    if args.check:
        print(json.dumps(json.loads(outputs["data/upstream.json"])["data"]["counts"]))
        return 0
    for rel, content in outputs.items():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    print("upstream-map: %s; rung %d/4" % (summary_line(json.loads(outputs["data/upstream.json"])["data"]),
                                           json.loads(outputs["data/upstream.json"])["data"]["rung"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
