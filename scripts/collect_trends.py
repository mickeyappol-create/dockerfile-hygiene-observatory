#!/usr/bin/env python3
"""Collect deterministic GitHub trend inputs for Apex candidate synthesis.

The collector intentionally uses GitHub's JSON API through `gh api`, not the
unofficial Trending HTML page. That keeps the daily input reproducible enough
for an agent pipeline while still capturing recent activity, new repositories,
and recent releases.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_and_scan import gh_json  # noqa: E402

SCHEMA = "apex-github-trends/1"
MAX_GH_REQUESTS = 18
ICT = dt.timezone(dt.timedelta(hours=7), name="ICT")


class Budget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def call(self, args: list[str]) -> Any:
        if self.used >= self.limit:
            raise RuntimeError(f"GitHub API request budget exhausted: {self.used}/{self.limit}")
        self.used += 1
        return gh_json(args)


def pipeline_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone(ICT)


def pipeline_today() -> str:
    return pipeline_now().date().isoformat()


def days_before(date_s: str, days: int) -> str:
    return (dt.date.fromisoformat(date_s) - dt.timedelta(days=days)).isoformat()


def repo_summary(repo: dict[str, Any], *, signal: str) -> dict[str, Any]:
    return {
        "full_name": repo.get("full_name"),
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "language": repo.get("language"),
        "topics": sorted(repo.get("topics") or []),
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "open_issues": int(repo.get("open_issues_count") or 0),
        "default_branch": repo.get("default_branch"),
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "signal": signal,
    }


def search_repos(budget: Budget, query: str, *, sort: str, per_page: int, signal: str) -> list[dict[str, Any]]:
    data = budget.call([
        "search/repositories",
        "--method", "GET",
        "-f", f"q={query}",
        "-f", f"sort={sort}",
        "-f", "order=desc",
        "-f", f"per_page={per_page}",
        "-f", "page=1",
    ])
    return [repo_summary(repo, signal=signal) for repo in data.get("items", [])]


def latest_release(budget: Budget, full_name: str) -> dict[str, Any] | None:
    try:
        data = budget.call([f"repos/{full_name}/releases/latest", "--method", "GET"])
    except subprocess.CalledProcessError:
        return None
    return {
        "repo": full_name,
        "tag_name": data.get("tag_name"),
        "name": data.get("name"),
        "html_url": data.get("html_url"),
        "published_at": data.get("published_at"),
        "prerelease": bool(data.get("prerelease")),
        "draft": bool(data.get("draft")),
    }


def candidate_score(repo: dict[str, Any], date_s: str) -> float:
    stars = float(repo.get("stars") or 0)
    pushed_at = str(repo.get("pushed_at") or repo.get("updated_at") or "")[:10]
    created_at = str(repo.get("created_at") or "")[:10]
    freshness = 0.0
    try:
        freshness = max(0.0, 30.0 - (dt.date.fromisoformat(date_s) - dt.date.fromisoformat(pushed_at)).days)
    except Exception:
        freshness = 0.0
    new_bonus = 0.0
    try:
        new_bonus = max(0.0, 60.0 - (dt.date.fromisoformat(date_s) - dt.date.fromisoformat(created_at)).days)
    except Exception:
        new_bonus = 0.0
    return round(stars ** 0.5 + freshness * 3.0 + new_bonus * 1.5, 3)


def merge_candidates(date_s: str, sections: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    signals: dict[str, set[str]] = {}
    for rows in sections.values():
        for repo in rows:
            full_name = repo.get("full_name")
            if not full_name:
                continue
            if full_name not in merged:
                merged[full_name] = dict(repo)
                signals[full_name] = set()
            signals[full_name].add(str(repo.get("signal") or "unknown"))
    candidates = []
    for full_name, repo in merged.items():
        item = dict(repo)
        item["signals"] = sorted(signals.get(full_name, set()))
        item["trend_score"] = candidate_score(item, date_s) + 10.0 * max(0, len(item["signals"]) - 1)
        item["trend_score"] = round(item["trend_score"], 3)
        candidates.append(item)
    candidates.sort(key=lambda r: (-float(r.get("trend_score") or 0), -int(r.get("stars") or 0), str(r.get("full_name") or "")))
    return candidates


def git_commit(path: Path, date_s: str) -> str:
    rel = path.relative_to(ROOT)
    subprocess.run(["git", "add", str(rel)], cwd=ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", str(rel)], cwd=ROOT)
    if diff.returncode == 0:
        return "unchanged"
    subprocess.run(["git", "commit", "-m", f"Collect Apex trends for {date_s}", "--only", "--", str(rel)], cwd=ROOT, check=True)
    return "committed"


def collect(date_s: str, *, max_requests: int) -> tuple[dict[str, Any], Path]:
    budget = Budget(max_requests)
    day_1 = days_before(date_s, 1)
    day_7 = days_before(date_s, 7)
    day_30 = days_before(date_s, 30)
    out_dir = ROOT / "trends"
    out_path = out_dir / f"{date_s}.json"
    prior_generated_at: str | None = None
    if out_path.exists():
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8"))
            if prior.get("date") == date_s and isinstance(prior.get("generated_at"), str):
                prior_generated_at = prior["generated_at"]
        except Exception:
            prior_generated_at = None

    queries = [
        {
            "name": "high_star_recent_activity",
            "query": f"stars:>5000 fork:false archived:false pushed:>={day_1}",
            "sort": "stars",
            "per_page": 30,
            "signal": "recent_activity",
        },
        {
            "name": "new_repositories",
            "query": f"created:>={day_30} stars:>50 fork:false archived:false",
            "sort": "stars",
            "per_page": 30,
            "signal": "new_repository",
        },
        {
            "name": "ai_data_tooling",
            "query": f"topic:ai stars:>500 fork:false archived:false pushed:>={day_7}",
            "sort": "stars",
            "per_page": 30,
            "signal": "ai_tooling_activity",
        },
        {
            "name": "typescript_python_tooling",
            "query": f"language:TypeScript stars:>1000 fork:false archived:false pushed:>={day_7}",
            "sort": "updated",
            "per_page": 30,
            "signal": "implementation_tooling_activity",
        },
    ]

    sections: dict[str, list[dict[str, Any]]] = {}
    for q in queries:
        sections[q["name"]] = search_repos(
            budget,
            q["query"],
            sort=q["sort"],
            per_page=int(q["per_page"]),
            signal=q["signal"],
        )

    release_cutoff = dt.datetime.combine(dt.date.fromisoformat(day_7), dt.time(), tzinfo=ICT).astimezone(dt.timezone.utc)
    release_repos = []
    for repo in merge_candidates(date_s, sections)[: min(10, max(0, max_requests - budget.used))]:
        full_name = str(repo.get("full_name") or "")
        if not full_name:
            continue
        release = latest_release(budget, full_name)
        if not release or not release.get("published_at"):
            continue
        try:
            published = dt.datetime.fromisoformat(str(release["published_at"]).replace("Z", "+00:00"))
        except Exception:
            continue
        if published >= release_cutoff and not release["draft"]:
            release_repos.append(release)

    candidates = merge_candidates(date_s, sections)
    for item in candidates:
        item["recent_release"] = next((r for r in release_repos if r["repo"] == item["full_name"]), None)

    payload = {
        "schema": SCHEMA,
        "date": date_s,
        "date_basis": "ICT/UTC+7 pipeline date; filenames, cache keys, commits, and cron jobs use this date.",
        "generated_at": prior_generated_at or pipeline_now().isoformat(),
        "method": "Deterministic GitHub API proxy for trends: recent high-star activity, new repositories, AI/tooling activity, and latest releases. No repository source is cloned or stored.",
        "limits": {
            "github_api_request_max": max_requests,
            "github_api_requests_used": budget.used,
        },
        "queries": queries,
        "sections": sections,
        "recent_releases": release_repos,
        "candidates": candidates[:50],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload, out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=pipeline_today())
    parser.add_argument("--max-requests", type=int, default=MAX_GH_REQUESTS)
    parser.add_argument("--no-commit", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Re-query GitHub even if today's trends file already exists.")
    args = parser.parse_args()

    if args.max_requests < 4:
        raise SystemExit("--max-requests must be at least 4")

    existing_path = ROOT / "trends" / f"{args.date}.json"
    if existing_path.exists() and not args.refresh:
        payload = json.loads(existing_path.read_text(encoding="utf-8"))
        top = payload["candidates"][0]["full_name"] if payload.get("candidates") else "none"
        print(json.dumps({
            "ok": True,
            "schema": payload.get("schema"),
            "date": payload.get("date"),
            "date_basis": payload.get("date_basis", "ICT/UTC+7 pipeline date"),
            "path": str(existing_path.relative_to(ROOT)),
            "github_api_requests_used": 0,
            "candidate_count": len(payload.get("candidates") or []),
            "top_candidate": top,
            "commit": "unchanged",
            "cached": True,
        }, sort_keys=True))
        return 0

    payload, out_path = collect(args.date, max_requests=args.max_requests)
    commit_status = "skipped"
    if not args.no_commit:
        commit_status = git_commit(out_path, args.date)

    top = payload["candidates"][0]["full_name"] if payload["candidates"] else "none"
    print(json.dumps({
        "ok": True,
        "schema": SCHEMA,
        "date": payload["date"],
        "date_basis": payload.get("date_basis"),
        "path": str(out_path.relative_to(ROOT)),
        "github_api_requests_used": payload["limits"]["github_api_requests_used"],
        "candidate_count": len(payload["candidates"]),
        "top_candidate": top,
        "commit": commit_status,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
