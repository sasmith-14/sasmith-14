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

import upstream_views as views

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = "<!-- upstream:start -->"
END = "<!-- upstream:end -->"

RUNGS = [
    ("First merge", "at least 1 merged upstream PR"),
    ("Multi-project", "merged into at least 3 distinct upstream repos"),
    ("Repeat", "at least 3 merged PRs in a single upstream repo"),
    ("Trusted", "GitHub lists you as COLLABORATOR or MEMBER on an upstream repo"),
]

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

    created = {(x["repository"]["nameWithOwner"], x["number"]): x["createdAt"]
               for x in raw["search_prs"] + raw["search_issues"]}
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
            item = {"kind": "pr", "number": pr["number"], "title": pr["title"], "url": pr["url"],
                    "created": created[(full, pr["number"])]}
            if pr["merged"]:
                item.update(status="merged", date=pr["mergedAt"][:10], ended=pr["mergedAt"])
            elif pr["state"] == "OPEN":
                item.update(status="draft" if pr["isDraft"] else "open", reviews=len(reviews))
            else:
                item.update(status="closed", date=pr["closedAt"][:10], ended=pr["closedAt"])
                for ref in pr["closingIssuesReferences"]["nodes"]:
                    fix = issue_fix.get(ref["number"]) if ref["repository"]["nameWithOwner"] == full else None
                    if fix and fix.get("number") and not (fix["repo"] == full and fix["number"] == pr["number"]):
                        item["superseded_by"] = {"ref": _ref(fix["repo"], full, fix["number"]), "url": fix["url"]}
                        break
            items.append(item)
        for issue in issues:
            associations.add(issue["authorAssociation"])
            fix = issue_fix[issue["number"]]
            item = {"kind": "issue", "number": issue["number"], "title": issue["title"], "url": issue["url"],
                    "created": created[(full, issue["number"])]}
            if fix:
                label = _ref(fix["repo"], full, fix["number"]) if fix.get("number") else fix["sha"]
                item.update(status="fixed", fixed_by={"ref": label, "url": fix["url"]}, ended=issue["closedAt"])
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
# README section

IMG_WIDTH = 760
MAX_MERGED_LINES = 10


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def summary_line(data: Dict) -> str:
    c = data["counts"]
    return "%d merged · %d open · %s fixed upstream" % (c["merged"], c["open"], _plural(c["fixed"], "report"))


def _md(text: str) -> str:
    """Make an upstream title safe to embed in Markdown: no raw HTML, no line breaks."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _picture(name: str, alt: str) -> List[str]:
    return ["<picture>",
            '  <source media="(prefers-color-scheme: dark)" srcset="assets/%s-dark.svg">' % name,
            '  <img alt="%s" src="assets/%s-light.svg" width="%d">' % (escape(alt, {'"': "&quot;"}), name, IMG_WIDTH),
            "</picture>"]


def _link(p: Dict, it: Dict) -> str:
    return "[%s #%d](%s)" % (p["repo"].split("/", 1)[1], it["number"], it["url"])


def render_section(data: Dict) -> str:
    every = [(p, it) for p in data["projects"] for it in p["items"]]
    merged = sorted((pi for pi in every if pi[1]["status"] == "merged"), key=lambda pi: pi[1]["ended"], reverse=True)
    desc = views.describe(data)
    lines = [START]
    lines += _picture("gitgraph", "Git graph of my upstream pull requests. " + desc)
    lines += ["", "<details>", "<summary>More views: transit · orbit · pipeline</summary>", "<br>", ""]
    lines += _picture("transit", "Transit map of my upstream contributions. " + desc)
    lines += ["", '<img alt="%s" src="assets/orbit.svg" width="%d">' % (escape("Orbit view of my upstream contributions. " + desc, {'"': "&quot;"}), IMG_WIDTH), ""]
    lines += _picture("pipeline", "Pipeline view of my upstream pull requests. " + desc)
    lines += ["", "</details>", ""]
    if merged:
        lines += ["**Merged**", ""]
        lines += ["- %s %s" % (_link(p, it), _md(it["title"])) for p, it in merged[:MAX_MERGED_LINES]]
        if len(merged) > MAX_MERGED_LINES:
            lines.append("- and %d more, all in the graph above" % (len(merged) - MAX_MERGED_LINES))
    lines += ["",
              "<sub>Generated daily from the GitHub API by [scripts/upstream_map.py](scripts/upstream_map.py). "
              "Counts only repositories I don't own. [How this is measured](UPSTREAM_MAP.md).</sub>",
              END]
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
    day = dt.date.fromisoformat(today)
    out = {"data/upstream.json": json.dumps({"generated": today, "data": data}, indent=2, ensure_ascii=False) + "\n"}
    for theme in ("light", "dark"):
        out["assets/gitgraph-%s.svg" % theme] = views.gitgraph(data, theme, day)
        out["assets/transit-%s.svg" % theme] = views.transit(data, theme)
        out["assets/pipeline-%s.svg" % theme] = views.pipeline(data, theme)
    out["assets/orbit.svg"] = views.orbit(data)
    out["README.md"] = splice(readme, render_section(data))
    return out


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
    data = json.loads(outputs["data/upstream.json"])["data"]
    print("upstream-map: %s; rung %d/4" % (summary_line(data), data["rung"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
