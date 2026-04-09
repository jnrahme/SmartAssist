from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
TRAFFIC_WINDOW_DAYS = 14
USER_AGENT = "SmartAssist-Growth-Snapshot"


class GitHubApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _headers(token: str, *, json_body: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _rest_json(url: str, token: str) -> Any:
    request = Request(url, headers=_headers(token), method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GitHubApiError(f"HTTP {exc.code} for {url}", status=exc.code) from exc
    except URLError as exc:
        raise GitHubApiError(f"Request failed for {url}: {exc.reason}") from exc


def _graphql_json(query: str, variables: dict[str, Any], token: str) -> Any:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        f"{GITHUB_API_BASE}/graphql",
        data=payload,
        headers=_headers(token, json_body=True),
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GitHubApiError("HTTP %s for GraphQL" % exc.code, status=exc.code) from exc
    except URLError as exc:
        raise GitHubApiError(f"GraphQL request failed: {exc.reason}") from exc
    errors = parsed.get("errors") or []
    if errors:
        message = "; ".join(
            str(error.get("message") or "unknown GraphQL error") for error in errors
        )
        raise GitHubApiError(f"GraphQL error: {message}")
    return parsed.get("data") or {}


def _search_issue_count(owner: str, repo: str, qualifier: str, token: str) -> int:
    query = quote_plus(f"repo:{owner}/{repo} {qualifier}")
    payload = _rest_json(f"{GITHUB_API_BASE}/search/issues?q={query}&per_page=1", token)
    return int(payload.get("total_count", 0) or 0)


def _safe(label: str, warnings: list[str], func, default: Any) -> Any:
    try:
        return func()
    except GitHubApiError as exc:
        warnings.append(f"{label}: {exc}")
        return default


def _parse_repo(repo: str) -> tuple[str, str]:
    owner, name = repo.split("/", 1)
    owner = owner.strip()
    name = name.strip()
    if not owner or not name:
        raise ValueError("Repository must look like OWNER/REPO")
    return owner, name


def build_snapshot(
    repo: str,
    token: str,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    owner, name = _parse_repo(repo)
    if not token:
        raise ValueError("A GitHub token is required")

    warnings: list[str] = []
    generated = generated_at or _now_iso()

    repo_query = """
    query RepoSnapshot($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        nameWithOwner
        url
        stargazerCount
        forkCount
        watchers {
          totalCount
        }
        discussions(first: 100) {
          totalCount
          nodes {
            category {
              name
              slug
            }
          }
        }
      }
    }
    """

    repo_data = _safe(
        "repo summary",
        warnings,
        lambda: _graphql_json(repo_query, {"owner": owner, "name": name}, token),
        {"repository": None},
    )
    repository = (repo_data or {}).get("repository") or {}
    discussions = repository.get("discussions") or {}
    discussion_nodes = discussions.get("nodes") or []
    discussion_counter: Counter[str] = Counter()
    for node in discussion_nodes:
        category = node.get("category") or {}
        slug = str(category.get("slug") or "unknown")
        discussion_counter[slug] += 1

    views = _safe(
        "traffic views",
        warnings,
        lambda: _rest_json(
            f"{GITHUB_API_BASE}/repos/{owner}/{name}/traffic/views", token
        ),
        {},
    )
    clones = _safe(
        "traffic clones",
        warnings,
        lambda: _rest_json(
            f"{GITHUB_API_BASE}/repos/{owner}/{name}/traffic/clones", token
        ),
        {},
    )
    referrers = _safe(
        "popular referrers",
        warnings,
        lambda: _rest_json(
            f"{GITHUB_API_BASE}/repos/{owner}/{name}/traffic/popular/referrers", token
        ),
        [],
    )
    paths = _safe(
        "popular paths",
        warnings,
        lambda: _rest_json(
            f"{GITHUB_API_BASE}/repos/{owner}/{name}/traffic/popular/paths", token
        ),
        [],
    )

    funnel_counts = {
        "open_issues": _safe(
            "open issues",
            warnings,
            lambda: _search_issue_count(owner, name, "is:issue is:open", token),
            0,
        ),
        "open_pull_requests": _safe(
            "open pull requests",
            warnings,
            lambda: _search_issue_count(owner, name, "is:pr is:open", token),
            0,
        ),
        "setup_issues": _safe(
            "setup issues",
            warnings,
            lambda: _search_issue_count(
                owner, name, 'is:issue is:open label:"setup"', token
            ),
            0,
        ),
        "feedback_issues": _safe(
            "feedback issues",
            warnings,
            lambda: _search_issue_count(
                owner, name, 'is:issue is:open label:"feedback"', token
            ),
            0,
        ),
        "community_issues": _safe(
            "community issues",
            warnings,
            lambda: _search_issue_count(
                owner, name, 'is:issue is:open label:"community"', token
            ),
            0,
        ),
        "good_first_issues": _safe(
            "good first issues",
            warnings,
            lambda: _search_issue_count(
                owner, name, 'is:issue is:open label:"good first issue"', token
            ),
            0,
        ),
        "help_wanted_issues": _safe(
            "help wanted issues",
            warnings,
            lambda: _search_issue_count(
                owner, name, 'is:issue is:open label:"help wanted"', token
            ),
            0,
        ),
    }

    return {
        "generated_at": generated,
        "repo": {
            "owner": owner,
            "name": name,
            "name_with_owner": str(repository.get("nameWithOwner") or repo),
            "url": str(repository.get("url") or f"https://github.com/{repo}"),
        },
        "discovery": {
            "traffic_window_days": TRAFFIC_WINDOW_DAYS,
            "stars": int(repository.get("stargazerCount", 0) or 0),
            "forks": int(repository.get("forkCount", 0) or 0),
            "watchers": int(
                (repository.get("watchers") or {}).get("totalCount", 0) or 0
            ),
            "views": {
                "count": int((views or {}).get("count", 0) or 0),
                "uniques": int((views or {}).get("uniques", 0) or 0),
                "series": (views or {}).get("views") or [],
            },
            "clones": {
                "count": int((clones or {}).get("count", 0) or 0),
                "uniques": int((clones or {}).get("uniques", 0) or 0),
                "series": (clones or {}).get("clones") or [],
            },
            "top_referrers": referrers or [],
            "top_paths": paths or [],
        },
        "funnel": {
            **funnel_counts,
            "discussions_total": int(discussions.get("totalCount", 0) or 0),
            "discussions_sampled": len(discussion_nodes),
            "discussions_by_category": [
                {"slug": slug, "count": count}
                for slug, count in discussion_counter.most_common()
            ],
        },
        "warnings": warnings,
    }


def render_markdown(snapshot: dict[str, Any]) -> str:
    discovery = snapshot["discovery"]
    funnel = snapshot["funnel"]
    lines = [
        "# GitHub Growth Snapshot",
        "",
        f"- Generated: `{snapshot['generated_at']}`",
        f"- Repo: `{snapshot['repo']['name_with_owner']}`",
        f"- Traffic window: last {discovery['traffic_window_days']} days from GitHub's repository traffic API",
        "",
        "## Discovery",
        "",
        f"- Stars: **{discovery['stars']}**",
        f"- Forks: **{discovery['forks']}**",
        f"- Watchers: **{discovery['watchers']}**",
        f"- Views: **{discovery['views']['count']}** total / **{discovery['views']['uniques']}** unique",
        f"- Clones: **{discovery['clones']['count']}** total / **{discovery['clones']['uniques']}** unique",
        "",
        "## Contributor + feedback funnel",
        "",
        f"- Open issues: **{funnel['open_issues']}**",
        f"- Open pull requests: **{funnel['open_pull_requests']}**",
        f"- Setup issues: **{funnel['setup_issues']}**",
        f"- Product feedback issues: **{funnel['feedback_issues']}**",
        f"- Contributor-interest issues: **{funnel['community_issues']}**",
        f"- Good first issues: **{funnel['good_first_issues']}**",
        f"- Help wanted issues: **{funnel['help_wanted_issues']}**",
        f"- Discussions: **{funnel['discussions_total']}** total",
        "",
    ]

    if funnel["discussions_by_category"]:
        lines.extend(["### Discussions by category", ""])
        for row in funnel["discussions_by_category"]:
            lines.append(f"- `{row['slug']}`: **{row['count']}**")
        if funnel["discussions_total"] > funnel["discussions_sampled"]:
            lines.append(
                f"- Note: category counts are sampled from the first {funnel['discussions_sampled']} discussions returned by GraphQL."
            )
        lines.append("")

    if discovery["top_referrers"]:
        lines.extend(
            [
                "## Top referrers",
                "",
                "| Referrer | Count | Unique |",
                "| --- | ---: | ---: |",
            ]
        )
        for row in discovery["top_referrers"]:
            lines.append(
                f"| {row.get('referrer', '?')} | {int(row.get('count', 0) or 0)} | {int(row.get('uniques', 0) or 0)} |"
            )
        lines.append("")

    if discovery["top_paths"]:
        lines.extend(
            [
                "## Top content paths",
                "",
                "| Path | Count | Unique |",
                "| --- | ---: | ---: |",
            ]
        )
        for row in discovery["top_paths"]:
            lines.append(
                f"| {row.get('path', '?')} | {int(row.get('count', 0) or 0)} | {int(row.get('uniques', 0) or 0)} |"
            )
        lines.append("")

    if snapshot["warnings"]:
        lines.extend(["## Warnings", ""])
        for warning in snapshot["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_snapshot_artifacts(
    snapshot: dict[str, Any], output_dir: Path | str
) -> tuple[Path, Path]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "snapshot.json"
    summary_path = root / "summary.md"
    json_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary_path.write_text(render_markdown(snapshot), encoding="utf-8")
    return json_path, summary_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a GitHub-native growth analytics snapshot for a repository."
    )
    parser.add_argument("--repo", required=True, help="Repository in OWNER/REPO form")
    parser.add_argument(
        "--output-dir",
        default="growth-artifacts/github",
        help="Directory where snapshot.json and summary.md will be written",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        print("Error: set GH_TOKEN or GITHUB_TOKEN before running this command.")
        return 1

    snapshot = build_snapshot(args.repo, token)
    json_path, summary_path = write_snapshot_artifacts(snapshot, args.output_dir)
    print(json_path)
    print(summary_path)
    if snapshot["warnings"]:
        print(f"Warnings: {len(snapshot['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
