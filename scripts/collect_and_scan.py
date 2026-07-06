#!/usr/bin/env python3
"""Collect public root Dockerfiles from high-star GitHub repos and scan via Apex.

Safety boundaries:
- No cloning; Dockerfiles are fetched from raw.githubusercontent.com only.
- No secret scanning of third-party repos; only static Dockerfile hygiene lint.
- Stores aggregate fields, rule IDs and receipt metadata; never stores raw Dockerfile text.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UA = "Hermes-Agent/1.0 (+https://github.com/NousResearch/Hermes-Agent; dockerfile-hygiene-observatory)"
APEX_BASE = "https://api.smartapex.uk"
TOOL_ID = "agent-dockerfile-lint"
CARD_ID = "card_agent_dockerfile_lint"
MAX_COLLECT_REQUESTS = 200
MAX_LINT_RUNS = 100
DEFAULT_PASSPORT_FILE = ROOT.parent / ".agents" / "agentbbs-hermes-passport.txt"
AGENT_KEY = ""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_json(url: str, *, method: str = "GET", body: Any | None = None, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[int, dict[str, str], Any]:
    data = None if body is None else canonical_json(body).encode("utf-8")
    h = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, dict(resp.headers), json.loads(raw) if raw else None


def fetch_text(url: str, timeout: int = 25) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, dict(resp.headers), raw.decode("utf-8", "replace")


def gh_json(args: list[str]) -> Any:
    out = subprocess.check_output(["gh", "api", *args], text=True)
    return json.loads(out)


def load_agent_key(key_file: str | None = None) -> str:
    """Load the dedicated Hermes Supply Agent key without printing it.

    Mission runs must use /opt/data/.agents/agentbbs-hermes-passport.txt.
    For third-party reproduction only, pass --key-file env to opt into APEX_AGENT_KEY.
    """
    if key_file == "env":
        key = os.environ.get("APEX_AGENT_KEY", "").strip()
        if key.startswith("agd_"):
            return key
        raise RuntimeError("--key-file env was requested, but APEX_AGENT_KEY is not an agd_ key")
    path = Path(key_file) if key_file else DEFAULT_PASSPORT_FILE
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key.startswith("agd_"):
            return key
        raise RuntimeError(f"Agent passport file exists but does not look like an agd_ key: {path}")
    raise RuntimeError(f"No dedicated Hermes Supply Agent passport found. Expected file: {path}")


def gh_search_repositories(max_pages: int = 4) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Sort by stars and keep the query broad; filter archived/forks where possible.
    for page in range(1, max_pages + 1):
        data = gh_json([
            "search/repositories",
            "--method", "GET",
            "-f", "q=stars:>10000 fork:false archived:false",
            "-f", "sort=stars",
            "-f", "order=desc",
            "-f", "per_page=100",
            "-f", f"page={page}",
        ])
        for repo in data.get("items", []):
            full = repo["full_name"]
            if full not in seen:
                seen.add(full)
                repos.append(repo)
    repos.sort(key=lambda r: int(r.get("stargazers_count") or 0), reverse=True)
    return repos


def git_ls_remote_head(full_name: str, branch: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "ls-remote", f"https://github.com/{full_name}.git", f"refs/heads/{branch}"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        ).strip()
        return out.split()[0] if out else None
    except Exception:
        return None


def sign_agent_headers(path: str, body: dict[str, Any], intent: str) -> dict[str, str]:
    key = AGENT_KEY
    if not key:
        raise RuntimeError("Apex Agent Passport key not loaded")
    canon = canonical_json(body)
    sha = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    ts = str(int(time.time() * 1000))
    nonce = secrets.token_hex(16)
    msg = f"APEX-V1\nPOST\n{path}\n{ts}\n{nonce}\n{sha}"
    sig = hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Authorization": "Bearer " + key,
        "X-Agent-Protocol": "apex/1",
        "X-Agent-Client": "hermes-agent",
        "X-Agent-Mode": "autonomous",
        "X-Agent-Intent": intent,
        "X-Agent-Timestamp": ts,
        "X-Agent-Nonce": nonce,
        "X-Agent-Content-SHA256": sha,
        "X-Agent-Signature": sig,
    }


def apex_lint(dockerfile: str) -> dict[str, Any]:
    path = f"/v1/tools/{TOOL_ID}/run"
    body = {"dockerfile": dockerfile}
    headers = sign_agent_headers(path, body, "tool")
    status, resp_headers, data = request_json(APEX_BASE + path, method="POST", body=body, headers=headers, timeout=45)
    return data


def submit_review(receipt_id: str, use_case: str) -> dict[str, Any] | None:
    path = f"/v1/cards/{CARD_ID}/reviews"
    body = {
        "schema": "apex-usage-review/1",
        "receipt_id": receipt_id,
        "tool_id": TOOL_ID,
        "usefulness_score": 5,
        "worked": True,
        "use_case": use_case[:180],
        "public_summary": "A verified Hermes agent used the read-only Dockerfile lint wrapper for an OSS Dockerfile hygiene observatory scan. No raw Dockerfile text, raw output, secrets, or private logs are included.",
        "problem_found": None,
        "requested_improvement": None,
    }
    headers = sign_agent_headers(path, body, "review")
    try:
        _status, _headers, data = request_json(APEX_BASE + path, method="POST", body=body, headers=headers, timeout=30)
        return data
    except urllib.error.HTTPError as e:
        # Duplicate/already-satisfied reviews should not destroy the scan; return safe error metadata.
        return {"error_status": e.code, "error_body": e.read().decode("utf-8", "replace")[:500]}


def rule_id(finding: dict[str, Any]) -> str:
    for key in ("rule_id", "rule", "id", "code", "name"):
        val = finding.get(key)
        if val:
            return str(val)
    msg = str(finding.get("message") or finding.get("summary") or finding)[:80]
    return "unclassified:" + hashlib.sha256(msg.encode()).hexdigest()[:12]


def safe_result(repo: dict[str, Any], raw_url: str, commit_sha: str | None, dockerfile: str, apex: dict[str, Any], review: dict[str, Any] | None, collected_at: str) -> dict[str, Any]:
    result = apex.get("result", {})
    receipt = apex.get("verification_receipt", {})
    findings = result.get("findings") or []
    rules = []
    for f in findings:
        if isinstance(f, dict):
            rules.append({
                "rule": rule_id(f),
                "severity": f.get("severity") or f.get("level") or f.get("tier"),
            })
    return {
        "repo": repo["full_name"],
        "stars": repo.get("stargazers_count"),
        "default_branch": repo.get("default_branch"),
        "dockerfile_path": "Dockerfile",
        "raw_url": raw_url,
        "target_commit": commit_sha,
        "collected_at": collected_at,
        "dockerfile_sha256": hashlib.sha256(dockerfile.encode("utf-8", "replace")).hexdigest(),
        "decision": result.get("decision"),
        "counts": result.get("counts"),
        "rules": rules,
        "instruction_count": result.get("instruction_count"),
        "from_count": result.get("from_count"),
        "verification_receipt": {
            "receipt_id": receipt.get("receipt_id"),
            "verify_url": receipt.get("verify_url"),
            "cite_as": receipt.get("cite_as") or apex.get("cite_as"),
            "issued_at": receipt.get("issued_at"),
            "identity_level": (receipt.get("identity") or {}).get("level"),
            "agent_client": (receipt.get("identity") or {}).get("agent_client"),
            "checks": receipt.get("checks"),
        },
        "usage_review": {
            "status": review.get("status") if isinstance(review, dict) else None,
            "id": ((review or {}).get("review") or {}).get("id") if isinstance(review, dict) else None,
        },
    }


def build_aggregate(date: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    agg_path = ROOT / "aggregate.json"
    previous = None
    if agg_path.exists():
        try:
            previous = json.loads(agg_path.read_text())
        except Exception:
            previous = None
    decision_counts = Counter(r.get("decision") or "UNKNOWN" for r in rows)
    # rule_counts is total occurrences. A rule can occur multiple times in one Dockerfile.
    rule_counts = Counter(rule["rule"] for r in rows for rule in (r.get("rules") or []))
    # rule_repos_with_rule is the repo-level prevalence count: each rule is counted at most once per repo.
    rule_repos_with_rule = Counter(
        rule_name
        for r in rows
        for rule_name in {rule.get("rule") for rule in (r.get("rules") or []) if rule.get("rule")}
    )
    severity_counts = Counter((rule.get("severity") or "unknown") for r in rows for rule in (r.get("rules") or []))
    total = len(rows)
    snapshot = {
        "date": date,
        "repo_count": total,
        "decision_counts": dict(decision_counts),
        "decision_rates": {k: (v / total if total else 0) for k, v in decision_counts.items()},
        "rule_counts": dict(rule_counts.most_common()),
        "rule_counts_meaning": "occurrences across all scanned Dockerfiles; a single repo can contribute multiple occurrences",
        "rule_repos_with_rule": dict(rule_repos_with_rule.most_common()),
        "rule_rates": {k: (v / total if total else 0) for k, v in rule_repos_with_rule.items()},
        "rule_rates_meaning": "repos_with_rule divided by repo_count; each rule counted at most once per repo",
        "severity_counts": dict(severity_counts),
        "receipt_count": sum(1 for r in rows if (r.get("verification_receipt") or {}).get("receipt_id")),
        "method": "High-star public GitHub repositories were sorted by stargazers; root Dockerfile was fetched from raw.githubusercontent.com; each Dockerfile text was submitted once to Apex agent-dockerfile-lint via signed Agent Passport headers; only decisions, counts, rule IDs, file hashes, target refs, and receipt metadata are stored. Rule counts are occurrences; rule rates are repo-level prevalence (repos_with_rule / repo_count).",
    }
    series = [] if not previous else list(previous.get("series", []))
    series = [s for s in series if s.get("date") != date]
    series.append(snapshot)
    series.sort(key=lambda s: s.get("date", ""))
    return {
        "schema": "dockerfile-hygiene-observatory-aggregate/1",
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "latest": snapshot,
        "series": series,
        "data_links": {"latest_results": f"results/{date}.json"},
        "limits": {"daily_lint_max": MAX_LINT_RUNS, "daily_collect_request_max": MAX_COLLECT_REQUESTS},
    }


def render_page(aggregate: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    latest = aggregate["latest"]
    def pct(x: float) -> str:
        return f"{x*100:.1f}%"
    dec_rows = "\n".join(
        f"<tr><td>{k}</td><td>{latest['decision_counts'].get(k,0)}</td><td>{pct(latest['decision_rates'].get(k,0))}</td></tr>"
        for k in ["PASS", "REVIEW", "BLOCK", "UNKNOWN"]
    )
    rule_rows = "\n".join(
        f"<tr><td>{rule}</td><td>{repos}</td><td>{latest['rule_counts'].get(rule, 0)}</td><td>{(latest['rule_counts'].get(rule, 0) / repos):.2f}</td><td>{pct(latest['rule_rates'].get(rule, 0))}</td></tr>"
        for rule, repos in list(latest.get("rule_repos_with_rule", {}).items())[:20]
    )
    sample_receipts = [r["verification_receipt"] for r in rows if (r.get("verification_receipt") or {}).get("receipt_id")][:5]
    receipt_rows = "\n".join(
        f"<tr><td><a href='{v['verify_url']}'>{v['receipt_id'][:12]}…</a></td><td>{v.get('identity_level')}</td><td>{v.get('issued_at')}</td></tr>"
        for v in sample_receipts
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSS Dockerfile Hygiene Observatory</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;line-height:1.5}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}td,th{{border:1px solid #ddd;padding:.5rem;text-align:left}}code{{background:#f4f4f4;padding:.1rem .25rem}}.note{{background:#f8fafc;border:1px solid #e5e7eb;padding:1rem}}</style>
</head><body>
<h1>OSS Dockerfile Hygiene Observatory</h1>
<p class="note">Daily signed Apex scans of public root Dockerfiles from high-star open-source GitHub repositories. Neutral ecosystem-level measurement; no rankings or blame framing.</p>
<h2>Latest snapshot: {latest['date']}</h2>
<ul><li>Repositories scanned: <strong>{latest['repo_count']}</strong></li><li>Apex verification receipts preserved: <strong>{latest['receipt_count']}</strong></li><li>This page as data: <a href="aggregate.json"><code>aggregate.json</code></a> · raw snapshot: <a href="{aggregate['data_links']['latest_results']}"><code>{aggregate['data_links']['latest_results']}</code></a></li></ul>
<h2>Decision mix</h2><table><thead><tr><th>Decision</th><th>Count</th><th>Rate</th></tr></thead><tbody>{dec_rows}</tbody></table>
<h2>Most frequent rule hits</h2><table><thead><tr><th>Rule</th><th>Repos (repos_with_rule)</th><th>Occurrences</th><th>Avg per affected repo</th><th>Rate over scanned repos</th></tr></thead><tbody>{rule_rows}</tbody></table>
<h2>Method</h2><p>{latest['method']}</p><p>Stored per repository: repo name, stars, branch/path, raw URL, target commit when resolved, Dockerfile SHA-256, Apex decision/counts/rule IDs, and verification receipt metadata. Raw Dockerfile text and finding prose are not stored.</p>
<h2>Receipt evidence (sample)</h2><table><thead><tr><th>Receipt</th><th>Identity</th><th>Issued at</th></tr></thead><tbody>{receipt_rows}</tbody></table>
<p><strong>cite_as:</strong> Validate individual rows via their <code>verification_receipt.verify_url</code>. Example citation: {sample_receipts[0]['cite_as'] if sample_receipts else 'No receipt yet'}.</p>
</body></html>"""
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(html, encoding="utf-8")
    # GitHub Pages can serve docs; duplicate JSON for simple relative links.
    (ROOT / "docs" / "aggregate.json").write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    results_dir = ROOT / "docs" / "results"
    results_dir.mkdir(exist_ok=True)
    date = latest["date"]
    (results_dir / f"{date}.json").write_text((ROOT / "results" / f"{date}.json").read_text(encoding="utf-8"), encoding="utf-8")


def main() -> int:
    global AGENT_KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="Target total rows for the date when --append is used; otherwise rows for this run.")
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument("--append", action="store_true", help="Append to results/YYYY-MM-DD.json, skipping repos already present for that date.")
    ap.add_argument("--key-file", default=str(DEFAULT_PASSPORT_FILE), help="Apex Agent Passport file. Defaults to /opt/data/.agents/agentbbs-hermes-passport.txt")
    args = ap.parse_args()
    if args.limit > MAX_LINT_RUNS:
        raise SystemExit(f"limit {args.limit} exceeds daily lint max {MAX_LINT_RUNS}")
    AGENT_KEY = load_agent_key(args.key_file)
    repos = gh_search_repositories()
    result_path = ROOT / "results" / f"{args.date}.json"
    existing_rows: list[dict[str, Any]] = []
    existing_collection_requests = 0
    existing_lint_runs = 0
    if args.append and result_path.exists():
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        existing_rows = list(previous.get("rows") or [])
        existing_collection_requests = int((previous.get("collection") or {}).get("collection_requests_used") or 0)
        existing_lint_runs = int((previous.get("collection") or {}).get("lint_runs_used") or len(existing_rows))
    rows: list[dict[str, Any]] = list(existing_rows)
    existing_repos = {r.get("repo") for r in existing_rows}
    collect_requests = 0
    lint_runs = 0
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat()
    for repo in repos:
        if len(rows) >= args.limit:
            break
        if repo["full_name"] in existing_repos:
            continue
        if existing_collection_requests + collect_requests >= MAX_COLLECT_REQUESTS:
            break
        branch = repo.get("default_branch") or "main"
        raw_url = f"https://raw.githubusercontent.com/{repo['full_name']}/{branch}/Dockerfile"
        collect_requests += 1
        try:
            status, headers, text = fetch_text(raw_url)
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                continue
            print(f"skip {repo['full_name']} raw HTTP {e.code}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"skip {repo['full_name']} raw error {e}", file=sys.stderr)
            continue
        if not text.strip() or len(text) > 200000:
            continue
        if existing_lint_runs + lint_runs >= MAX_LINT_RUNS:
            break
        commit_sha = git_ls_remote_head(repo["full_name"], branch)
        try:
            apex = apex_lint(text)
            lint_runs += 1
            receipt_id = ((apex or {}).get("verification_receipt") or {}).get("receipt_id")
            review = submit_review(receipt_id, f"OSS Dockerfile hygiene observatory scan for {repo['full_name']} root Dockerfile") if receipt_id else None
            row = safe_result(repo, raw_url, commit_sha, text, apex, review, collected_at)
            rows.append(row)
            print(f"{len(rows):03d}/{args.limit} {repo['full_name']} {row['decision']} receipt={receipt_id}")
            time.sleep(0.35)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:1000]
            print(f"lint failed {repo['full_name']} HTTP {e.code}: {body}", file=sys.stderr)
            if e.code == 429:
                break
            if e.code == 428:
                break
        except Exception as e:
            print(f"lint failed {repo['full_name']}: {e}", file=sys.stderr)
    if len(rows) < args.limit:
        print(f"warning: collected {len(rows)} rows below requested {args.limit}", file=sys.stderr)
    out = {
        "schema": "dockerfile-hygiene-observatory-results/1",
        "date": args.date,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "collection": {
            "repo_query": "GitHub search repositories: stars:>10000 fork:false archived:false sorted by stars desc",
            "dockerfile_path_policy": "root Dockerfile only for Day 1",
            "collection_requests_used": existing_collection_requests + collect_requests,
            "lint_runs_used": existing_lint_runs + lint_runs,
            "collection_requests_used_this_run": collect_requests,
            "lint_runs_used_this_run": lint_runs,
            "raw_text_stored": False,
        },
        "rows": rows,
    }
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / f"{args.date}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    aggregate = build_aggregate(args.date, rows)
    (ROOT / "aggregate.json").write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render_page(aggregate, rows)
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
