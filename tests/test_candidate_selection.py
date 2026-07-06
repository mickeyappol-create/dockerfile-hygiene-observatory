import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_and_scan.py"
spec = importlib.util.spec_from_file_location("collect_and_scan", MODULE_PATH)
assert spec is not None and spec.loader is not None
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


class CandidateSelectionTest(unittest.TestCase):
    def test_root_dockerfile_code_search_filter_dedupes_and_sorts_by_stars(self):
        items = [
            {"path": "Dockerfile", "repository": {"full_name": "low/root", "stargazers_count": 10, "default_branch": "main", "fork": False, "archived": False}},
            {"path": "docker/Dockerfile", "repository": {"full_name": "skip/nested", "stargazers_count": 999, "default_branch": "main", "fork": False, "archived": False}},
            {"path": "Dockerfile.md", "repository": {"full_name": "skip/doc", "stargazers_count": 999, "default_branch": "main", "fork": False, "archived": False}},
            {"path": "Dockerfile", "repository": {"full_name": "high/root", "stargazers_count": 100, "default_branch": "master", "fork": False, "archived": False}},
            {"path": "Dockerfile", "repository": {"full_name": "high/root", "stargazers_count": 100, "default_branch": "master", "fork": False, "archived": False}},
            {"path": "Dockerfile", "repository": {"full_name": "skip/fork", "stargazers_count": 200, "default_branch": "main", "fork": True, "archived": False}},
            {"path": "Dockerfile", "repository": {"full_name": "skip/archived", "stargazers_count": 300, "default_branch": "main", "fork": False, "archived": True}},
        ]

        repos = collect.root_dockerfile_repos_from_code_items(items)

        self.assertEqual([r["full_name"] for r in repos], ["high/root", "low/root"])
        self.assertEqual(repos[0]["default_branch"], "master")

    def test_graphql_repo_search_keeps_only_root_dockerfile_blob_nodes(self):
        nodes = [
            {"nameWithOwner": "has/root", "stargazerCount": 50, "isFork": False, "isArchived": False, "defaultBranchRef": {"name": "main"}, "dockerfile": {"__typename": "Blob"}},
            {"nameWithOwner": "skip/missing", "stargazerCount": 100, "isFork": False, "isArchived": False, "defaultBranchRef": {"name": "main"}, "dockerfile": None},
            {"nameWithOwner": "skip/tree", "stargazerCount": 90, "isFork": False, "isArchived": False, "defaultBranchRef": {"name": "main"}, "dockerfile": {"__typename": "Tree"}},
            {"nameWithOwner": "skip/fork", "stargazerCount": 80, "isFork": True, "isArchived": False, "defaultBranchRef": {"name": "main"}, "dockerfile": {"__typename": "Blob"}},
        ]

        repos = collect.root_dockerfile_repos_from_graphql_nodes(nodes)

        self.assertEqual(repos, [{"full_name": "has/root", "stargazers_count": 50, "default_branch": "main", "fork": False, "archived": False}])

    def test_merge_candidates_preserves_prefiltered_hits_before_fallback_misses(self):
        preferred = [
            {"full_name": "has/dockerfile", "stargazers_count": 50},
            {"full_name": "also/has", "stargazers_count": 40},
        ]
        fallback = [
            {"full_name": "no/dockerfile", "stargazers_count": 1000},
            {"full_name": "has/dockerfile", "stargazers_count": 50},
        ]

        merged = collect.merge_repository_candidates(preferred, fallback)

        self.assertEqual([r["full_name"] for r in merged], ["has/dockerfile", "also/has", "no/dockerfile"])


if __name__ == "__main__":
    unittest.main()
