import json
from pathlib import Path

import smartassist.tools.github_growth_snapshot as growth


def test_build_snapshot_collects_growth_metrics(monkeypatch):
    def fake_rest(url: str, token: str):
        if url.endswith("/traffic/views"):
            return {
                "count": 42,
                "uniques": 24,
                "views": [
                    {"timestamp": "2026-04-07T00:00:00Z", "count": 42, "uniques": 24}
                ],
            }
        if url.endswith("/traffic/clones"):
            return {
                "count": 8,
                "uniques": 6,
                "clones": [
                    {"timestamp": "2026-04-07T00:00:00Z", "count": 8, "uniques": 6}
                ],
            }
        if url.endswith("/traffic/popular/referrers"):
            return [{"referrer": "Hacker News", "count": 12, "uniques": 10}]
        if url.endswith("/traffic/popular/paths"):
            return [{"path": "/jnrahme/SmartAssist", "count": 20, "uniques": 15}]
        if "/search/issues" in url:
            if "label%3A%22good+first+issue%22" in url:
                return {"total_count": 3}
            if "label%3A%22help+wanted%22" in url:
                return {"total_count": 5}
            if "label%3A%22setup%22" in url:
                return {"total_count": 2}
            if "label%3A%22feedback%22" in url:
                return {"total_count": 4}
            if "label%3A%22community%22" in url:
                return {"total_count": 1}
            if "is%3Apr+is%3Aopen" in url:
                return {"total_count": 7}
            if "is%3Aissue+is%3Aopen" in url:
                return {"total_count": 11}
        raise AssertionError(f"Unexpected URL: {url}")

    def fake_graphql(query: str, variables: dict, token: str):
        assert variables == {"owner": "jnrahme", "name": "SmartAssist"}
        return {
            "repository": {
                "nameWithOwner": "jnrahme/SmartAssist",
                "url": "https://github.com/jnrahme/SmartAssist",
                "stargazerCount": 99,
                "forkCount": 12,
                "watchers": {"totalCount": 7},
                "discussions": {
                    "totalCount": 3,
                    "nodes": [
                        {
                            "category": {
                                "slug": "announcements",
                                "name": "Announcements",
                            }
                        },
                        {
                            "category": {
                                "slug": "show-and-tell",
                                "name": "Show and tell",
                            }
                        },
                        {
                            "category": {
                                "slug": "show-and-tell",
                                "name": "Show and tell",
                            }
                        },
                    ],
                },
            }
        }

    monkeypatch.setattr(growth, "_rest_json", fake_rest)
    monkeypatch.setattr(growth, "_graphql_json", fake_graphql)

    snapshot = growth.build_snapshot(
        "jnrahme/SmartAssist", "token", generated_at="2026-04-08T00:00:00+00:00"
    )

    assert snapshot["repo"]["name_with_owner"] == "jnrahme/SmartAssist"
    assert snapshot["discovery"]["stars"] == 99
    assert snapshot["discovery"]["views"]["count"] == 42
    assert snapshot["funnel"]["open_issues"] == 11
    assert snapshot["funnel"]["good_first_issues"] == 3
    assert snapshot["funnel"]["discussions_total"] == 3
    assert snapshot["funnel"]["discussions_by_category"][0] == {
        "slug": "show-and-tell",
        "count": 2,
    }
    assert snapshot["warnings"] == []


def test_write_snapshot_artifacts_renders_markdown(tmp_path: Path):
    snapshot = {
        "generated_at": "2026-04-08T00:00:00+00:00",
        "repo": {
            "owner": "jnrahme",
            "name": "SmartAssist",
            "name_with_owner": "jnrahme/SmartAssist",
            "url": "https://github.com/jnrahme/SmartAssist",
        },
        "discovery": {
            "traffic_window_days": 14,
            "stars": 10,
            "forks": 2,
            "watchers": 3,
            "views": {"count": 20, "uniques": 11, "series": []},
            "clones": {"count": 4, "uniques": 3, "series": []},
            "top_referrers": [{"referrer": "X", "count": 9, "uniques": 8}],
            "top_paths": [{"path": "/jnrahme/SmartAssist", "count": 12, "uniques": 10}],
        },
        "funnel": {
            "open_issues": 5,
            "open_pull_requests": 1,
            "setup_issues": 1,
            "feedback_issues": 2,
            "community_issues": 1,
            "good_first_issues": 2,
            "help_wanted_issues": 3,
            "discussions_total": 2,
            "discussions_sampled": 2,
            "discussions_by_category": [{"slug": "q-a", "count": 2}],
        },
        "warnings": ["traffic views: HTTP 403 for test"],
    }

    json_path, summary_path = growth.write_snapshot_artifacts(snapshot, tmp_path)

    assert json.loads(json_path.read_text())["repo"]["name"] == "SmartAssist"
    summary = summary_path.read_text()
    assert "# GitHub Growth Snapshot" in summary
    assert "## Contributor + feedback funnel" in summary
    assert "traffic views: HTTP 403 for test" in summary
