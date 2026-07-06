#!/usr/bin/env python3
"""Rebuild aggregate.json and docs from stored results only.

This script performs no network collection and no Apex calls. It reads
results/YYYY-MM-DD.json, recomputes compatible aggregate snapshots, asserts that
repo-level rule rates are <= 100%, and re-renders the static dashboard.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from collect_and_scan import ROOT, build_aggregate, render_page


def main() -> int:
    results_dir = ROOT / "results"
    result_files = sorted(results_dir.glob("*.json"))
    if not result_files:
        raise SystemExit("No results/*.json files found")

    aggregate = None
    snapshots = []
    latest_rows = []
    latest_date = None

    # Rebuild each snapshot from stored rows. build_aggregate preserves fields and
    # recomputes rule_repos_with_rule + corrected rule_rates from rows[].rules.
    original_agg = ROOT / "aggregate.json"
    backup = original_agg.read_text(encoding="utf-8") if original_agg.exists() else None
    try:
        if original_agg.exists():
            original_agg.unlink()
        for path in result_files:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("rows") or []
            date = data.get("date") or path.stem
            aggregate = build_aggregate(date, rows)
            snapshots = aggregate["series"]
            latest_rows = rows
            latest_date = date
            original_agg.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        if backup is not None:
            original_agg.write_text(backup, encoding="utf-8")
        raise

    assert aggregate is not None
    aggregate["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    aggregate["series"] = snapshots
    aggregate["latest"] = snapshots[-1]
    aggregate["data_links"] = {"latest_results": f"results/{latest_date}.json"}

    for snap in aggregate["series"]:
        bad = {k: v for k, v in snap.get("rule_rates", {}).items() if v > 1.0 + 1e-12}
        assert not bad, f"rule_rates over 100% in {snap.get('date')}: {bad}"
        repo_count = snap.get("repo_count") or 0
        for rule, repos in snap.get("rule_repos_with_rule", {}).items():
            assert repos <= repo_count, f"{rule} repos_with_rule={repos} > repo_count={repo_count}"
            expected = (repos / repo_count) if repo_count else 0
            assert abs(snap.get("rule_rates", {}).get(rule, 0) - expected) < 1e-12, rule

    original_agg.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render_page(aggregate, latest_rows)

    latest = aggregate["latest"]
    unpinned_repos = latest.get("rule_repos_with_rule", {}).get("unpinned_base_image", 0)
    unpinned_rate = latest.get("rule_rates", {}).get("unpinned_base_image", 0)
    over_100 = sum(1 for snap in aggregate["series"] for v in snap.get("rule_rates", {}).values() if v > 1.0 + 1e-12)
    print(json.dumps({
        "status": "ok",
        "snapshots": len(aggregate["series"]),
        "latest_date": latest.get("date"),
        "repo_count": latest.get("repo_count"),
        "unpinned_base_image": {
            "repos_with_rule": unpinned_repos,
            "rate": unpinned_rate,
            "rate_pct": round(unpinned_rate * 100, 1),
            "occurrences": latest.get("rule_counts", {}).get("unpinned_base_image", 0),
        },
        "rule_rates_over_100_count": over_100,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
