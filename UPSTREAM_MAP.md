# Upstream Map: method

The Upstream Map on my profile shows what I have contributed to repositories I don't own. Every mark comes from the GitHub API. Nothing is typed in by hand.

## Sources

1. `gh search prs --author sasmith-14` and `gh search issues --author sasmith-14` find the work.
2. One GraphQL query per repository fetches the details: star count, merge state, reviews, and what closed each issue.
3. Repositories owned by `sasmith-14`, including my forks, are excluded.

The [workflow](.github/workflows/upstream-map.yml) runs daily, on demand, and when the script changes. The last result is stored in [`data/upstream.json`](data/upstream.json).

## Marks

| Mark | Meaning |
|:--|:--|
| filled square | PR merged |
| hollow square | PR open (dashed if draft) |
| cross | PR closed without merge, or report closed without a fix |
| filled diamond | Issue I filed, closed as *completed* by a merged PR or commit |
| hollow diamond | Issue I filed, still open |

A PR counts as "in review" when at least one review comes from someone other than me. My own replies in a review thread don't count.

## Rungs

Rungs are sequential. Each one is a fact anyone can check:

| Rung | Name | Rule |
|:--|:--|:--|
| 1 | First merge | at least 1 merged upstream PR |
| 2 | Multi-project | merged into at least 3 distinct upstream repositories |
| 3 | Repeat | at least 3 merged PRs in a single upstream repository |
| 4 | Trusted | GitHub lists me as `COLLABORATOR` or `MEMBER` on an upstream repository |

Open PRs and issues are shown, but they never count toward a rung. Stars appear per project and are never added up, because stars measure the project, not me.

## Safety

The script computes everything before writing anything. It exits without changing a file if any of these happen:

- an API call fails,
- the search returns no upstream PRs, or
- the merged count drops below the last stored run.

A drop can be real, for example if a repository is deleted. In that case, run the workflow manually with `allow_shrink`. The workflow commits only when the output changes, and commits as `github-actions[bot]`.
