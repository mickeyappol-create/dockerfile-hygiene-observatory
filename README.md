# OSS Dockerfile Hygiene Observatory

Daily signed Apex scans of public root Dockerfiles from high-star open-source GitHub repositories.

## What this dataset contains

- `results/YYYY-MM-DD.json`: per-repository neutral facts: repo, stars, root Dockerfile URL, target commit when resolved, Dockerfile SHA-256, Apex `decision`, severity `counts`, rule IDs, and `verification_receipt` metadata.
- `aggregate.json`: latest PASS/REVIEW/BLOCK counts/rates, rule occurrence counts/rates, and time series.
- `docs/index.html`: static GitHub Pages dashboard with explicit data links.

Raw Dockerfile text is **not** stored. Finding prose and instruction text are **not** stored. The goal is ecosystem-level hygiene measurement, not ranking or shaming individual projects.

## Method

1. Query public GitHub repositories by stars (`stars:>10000 fork:false archived:false`, sorted descending).
2. Fetch only root `Dockerfile` via `raw.githubusercontent.com`; no cloning.
3. Submit each Dockerfile once to Apex `agent-dockerfile-lint` using signed Agent Passport headers and the Hermes user-agent.
4. Submit a safe receipt-backed usage review after each verified run to satisfy the Apex feedback gate.
5. Store only reproducibility and aggregate fields.

Day 1 limits: at most 100 lint runs and at most 200 Dockerfile collection requests.

## Reproduce / operations

Daily scans are automated by `.github/workflows/daily-scan.yml` at 03:17 UTC. Do **not** run the scanner manually during normal operation; a manual run can double-spend the daily collection/lint budget and race the scheduled commit. Manual fallback is for operator-requested outage recovery only.

For third-party reproduction outside the scheduled workflow:

```bash
APEX_AGENT_KEY=<your-agent-key> python3 scripts/collect_and_scan.py --key-file env --limit 50
```

Requires `gh`, `git`, and Python 3. No Python package dependencies.

## Citation / trust

Every row includes an Apex `verification_receipt.verify_url`; use that receipt as the verification source for the lint output. The dashboard also lists sample receipt links.

## Ethics and boundaries

- Public Dockerfiles only.
- No third-party secret scanning.
- No repository cloning.
- No raw Dockerfile text in the published dataset.
- Neutral project names; no leaderboard or blame framing.
