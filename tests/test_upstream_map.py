"""Tests for scripts/upstream_map.py. Standard library only: python3 -m unittest discover -s tests"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import upstream_map as um  # noqa: E402
import upstream_views as views  # noqa: E402
import datetime as dt

with open(os.path.join(HERE, "fixtures", "raw-2026-11-20.json"), encoding="utf-8") as _fh:
    RAW = json.load(_fh)
README = "# Me\n\n%s\nold\n%s\n\n## Projects\n" % (um.START, um.END)


def fake_gh(raw, extra_prs=(), fail_on=None):
    """A stand-in for the gh CLI that answers from a recorded fixture."""
    calls = []

    def gh(args):
        calls.append(args)
        if fail_on and fail_on in args:
            raise um.MapError("simulated failure")
        if args[:2] == ["search", "prs"]:
            return json.dumps(raw["search_prs"] + list(extra_prs))
        if args[:2] == ["search", "issues"]:
            return json.dumps(raw["search_issues"])
        if args[:2] == ["api", "graphql"]:
            m = re.search(r'repository\(owner: "([^"]+)", name: "([^"]+)"\)', args[-1])
            return json.dumps({"data": {"repository": raw["details"]["%s/%s" % m.groups()]}})
        raise AssertionError(args)

    gh.calls = calls
    return gh


class Compute(unittest.TestCase):
    def setUp(self):
        self.data = um.compute(RAW)

    def test_counts_match_snapshot(self):
        self.assertEqual(self.data["counts"], {
            "merged": 3, "repos_merged": 3, "best_single_repo": 1, "open": 6,
            "in_review": 1, "closed": 1, "fixed": 2, "reported_open": 3})
        self.assertEqual((self.data["rung"], self.data["rung_name"]), (2, "Multi-project"))

    def test_every_search_result_appears_exactly_once(self):
        found = {(p["repo"], it["number"]) for p in self.data["projects"] for it in p["items"]}
        expected = {(x["repository"]["nameWithOwner"], x["number"])
                    for x in RAW["search_prs"] + RAW["search_issues"]}
        self.assertEqual(found, expected)
        self.assertEqual(sum(len(p["items"]) for p in self.data["projects"]), len(expected))

    def test_self_replies_are_not_reviews(self):
        ka = next(p for p in self.data["projects"] if p["repo"] == "JuliaGPU/KernelAbstractions.jl")
        pr = next(it for it in ka["items"] if it["number"] == 957)
        self.assertEqual(pr["reviews"], 3)  # 3 maintainer reviews; 3 replies by the author excluded

    def test_superseded_and_cross_repo_fix(self):
        gc = next(p for p in self.data["projects"] if p["repo"] == "JuliaGPU/GPUCompiler.jl")
        self.assertEqual(gc["items"][0]["superseded_by"]["ref"], "#1094")
        ka = next(p for p in self.data["projects"] if p["repo"] == "JuliaGPU/KernelAbstractions.jl")
        self.assertEqual(ka["items"][-1]["fixed_by"]["ref"], "JuliaPackaging/Yggdrasil#15480")

    def test_closed_without_fix_is_not_counted_as_fixed(self):
        raw = copy.deepcopy(RAW)
        issue = raw["details"]["JuliaGPU/GPUCompiler.jl"]["i1088"]
        issue["stateReason"] = "NOT_PLANNED"
        data = um.compute(raw)
        self.assertEqual(data["counts"]["fixed"], 1)

    def test_trusted_rung_needs_github_association(self):
        raw = copy.deepcopy(RAW)
        for d in raw["details"].values():
            for k, v in d.items():
                if k.startswith("p"):
                    v["authorAssociation"] = "COLLABORATOR"
        # Rungs are sequential: Trusted alone does not skip the unmet Repeat rung.
        self.assertEqual(um.compute(raw)["rung"], 2)

    def test_zero_results_refuses(self):
        raw = dict(RAW, search_prs=[])
        with self.assertRaises(um.MapError):
            um.compute(raw)


class Fetch(unittest.TestCase):
    def test_own_repos_are_excluded(self):
        own = {"repository": {"nameWithOwner": "sasmith-14/KernelAbstractions.jl", "name": "x"},
               "number": 1, "title": "t", "url": "u", "state": "closed", "createdAt": "", "authorAssociation": "OWNER"}
        gh = fake_gh(RAW, extra_prs=[own])
        raw = um.fetch("sasmith-14", gh)
        self.assertNotIn("sasmith-14/KernelAbstractions.jl", raw["details"])
        self.assertFalse(any("sasmith-14" in c[-1] for c in gh.calls if c[:2] == ["api", "graphql"]))

    def test_api_failure_propagates(self):
        with self.assertRaises(um.MapError):
            um.fetch("sasmith-14", fake_gh(RAW, fail_on="graphql"))

    def test_graphql_errors_refuse(self):
        def gh(args):
            if args[:2] == ["api", "graphql"]:
                return json.dumps({"data": {"repository": None}, "errors": [{"message": "boom"}]})
            return fake_gh(RAW)(args)
        with self.assertRaises(um.MapError):
            um.fetch("sasmith-14", gh)


class Build(unittest.TestCase):
    def test_shrink_is_refused_unless_allowed(self):
        prev = {"changed": "2026-11-01", "data": copy.deepcopy(um.compute(RAW))}
        prev["data"]["counts"]["merged"] = 4
        with self.assertRaises(um.MapError):
            um.build(RAW, prev, README, "2026-11-21", allow_shrink=False)
        um.build(RAW, prev, README, "2026-11-21", allow_shrink=True)

    def test_same_day_rerun_is_identical(self):
        first = um.build(RAW, None, README, "2026-11-20", False)
        prev = json.loads(first["data/upstream.json"])
        self.assertEqual(first, um.build(RAW, prev, first["README.md"], "2026-11-20", False))

    def test_next_day_only_moves_the_timeline(self):
        first = um.build(RAW, None, README, "2026-11-20", False)
        later = um.build(RAW, json.loads(first["data/upstream.json"]), first["README.md"], "2026-11-21", False)
        changed = {k for k in first if first[k] != later[k]}
        self.assertEqual(changed, {"data/upstream.json", "assets/gitgraph-light.svg", "assets/gitgraph-dark.svg"})

    def test_titles_cannot_inject_html(self):
        raw = copy.deepcopy(RAW)
        raw["details"]["apache/wayang"]["p873"]["title"] = "Fix <script>alert(1)</script> & co"
        out = um.build(raw, None, README, "2026-11-20", False)["README.md"]
        self.assertIn("Fix &lt;script&gt;alert(1)&lt;/script&gt; &amp; co", out)
        self.assertNotIn("<script>", out)

    def test_readme_needs_markers(self):
        with self.assertRaises(um.MapError):
            um.build(RAW, None, "# no markers\n", "2026-11-20", False)

    def test_readme_outside_markers_untouched(self):
        out = um.build(RAW, None, README, "2026-11-20", False)["README.md"]
        self.assertTrue(out.startswith("# Me\n\n" + um.START))
        self.assertTrue(out.endswith(um.END + "\n\n## Projects\n"))
        self.assertNotIn("old", out)



class Views(unittest.TestCase):
    def setUp(self):
        self.data = um.compute(RAW)
        self.all_items = sorted("%s#%d" % (p["repo"], it["number"]) for p in self.data["projects"] for it in p["items"])

    def items_in(self, svg):
        return sorted(re.findall(r'data-item="([^"]+)"', svg))

    def test_every_view_shows_every_item_exactly_once(self):
        day = dt.date(2026, 11, 20)
        for name, svg in (("gitgraph", views.gitgraph(self.data, "light", day)),
                          ("gitgraph-dark", views.gitgraph(self.data, "dark", day)),
                          ("transit", views.transit(self.data, "light")),
                          ("orbit", views.orbit(self.data)),
                          ("pipeline", views.pipeline(self.data, "dark"))):
            with self.subTest(view=name):
                self.assertEqual(self.items_in(svg), self.all_items)

    def test_only_open_work_animates(self):
        svg = views.gitgraph(self.data, "light", dt.date(2026, 11, 20))
        self.assertEqual(svg.count('class="flow"'), self.data["counts"]["open"])
        waiting = sum(1 for p in self.data["projects"] for it in p["items"]
                      if it["status"] in ("open", "draft") and not it.get("reviews"))
        self.assertEqual(views.pipeline(self.data, "light").count("<animateTransform"), waiting)

    def test_lanes_are_capped(self):
        data = copy.deepcopy(self.data)
        proto = data["projects"][0]
        data["projects"] = [dict(copy.deepcopy(proto), repo="org/p%d" % i) for i in range(10)]
        svg = views.gitgraph(data, "light", dt.date(2026, 11, 20))
        self.assertIn("+2 more projects", svg)
        self.assertEqual(len(set(re.findall(r'data-item="org/p(\d+)#', svg))), views.MAX_LANES)

    def test_axis_zooms_out(self):
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        days = lambda n: [label for _, label in views._ticks(start, start + dt.timedelta(days=n))]
        self.assertIn("Jan 4", days(10))
        self.assertTrue(all(len(l.split()) == 2 for l in days(60)))   # weekly "Jan 5"
        self.assertIn("Feb", days(200))
        self.assertIn("Jan 2027", days(400))

    def test_views_are_valid_xml(self):
        import xml.dom.minidom
        for svg in (views.gitgraph(self.data, "dark", dt.date(2026, 11, 20)), views.transit(self.data, "dark"),
                    views.orbit(self.data), views.pipeline(self.data, "light")):
            xml.dom.minidom.parseString(svg)


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "README.md"), "w") as fh:
            fh.write(README)
        self._root, self._fetch = um.ROOT, um.fetch
        um.ROOT = self.tmp

    def tearDown(self):
        um.ROOT, um.fetch = self._root, self._fetch
        shutil.rmtree(self.tmp)

    def test_failure_writes_nothing(self):
        def boom(login):
            raise um.MapError("simulated API outage")
        um.fetch = boom
        self.assertEqual(um.main(["--login", "sasmith-14"]), 1)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["README.md"])
        with open(os.path.join(self.tmp, "README.md")) as fh:
            self.assertEqual(fh.read(), README)

    def test_success_writes_all_outputs(self):
        um.fetch = lambda login: RAW
        self.assertEqual(um.main(["--login", "sasmith-14"]), 0)
        for rel in ("README.md", "data/upstream.json", "assets/orbit.svg",
                    "assets/gitgraph-light.svg", "assets/gitgraph-dark.svg", "assets/transit-light.svg",
                    "assets/transit-dark.svg", "assets/pipeline-light.svg", "assets/pipeline-dark.svg"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), rel)


if __name__ == "__main__":
    unittest.main()
