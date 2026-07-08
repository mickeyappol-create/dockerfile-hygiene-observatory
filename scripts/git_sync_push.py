#!/usr/bin/env python3
"""Rebase over origin/main, then push.

For Hermes cron jobs that share the repo with GitHub Actions. Outputs compact
JSON so Telegram reports expose pull/push failures instead of hiding them.
"""
from __future__ import annotations

import json
import subprocess
import sys


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def tail(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout + "\n" + proc.stderr).strip()[-1200:]


def main() -> int:
    payload: dict[str, object] = {"schema": "apex-git-sync-push/1"}
    try:
        pull = run(["git", "pull", "--rebase", "--autostash", "origin", "main"], timeout=90)
    except subprocess.TimeoutExpired as exc:
        payload.update({
            "ok": False,
            "stage": "pull",
            "pull": "failed",
            "pull_exit_code": 124,
            "pull_output_tail": f"git pull --rebase timeout after {exc.timeout}s",
            "push": "skipped",
        })
        print(json.dumps(payload, sort_keys=True))
        return 124
    payload["pull_output_tail"] = tail(pull)
    if pull.returncode != 0:
        payload.update({
            "ok": False,
            "stage": "pull",
            "pull": "failed",
            "pull_exit_code": pull.returncode,
            "push": "skipped",
        })
        print(json.dumps(payload, sort_keys=True))
        return pull.returncode
    payload["pull"] = "ok"

    try:
        push = run(["git", "push", "origin", "main"], timeout=60)
    except subprocess.TimeoutExpired as exc:
        payload.update({
            "ok": False,
            "stage": "push",
            "push": "failed",
            "push_exit_code": 124,
            "push_output_tail": f"git push timeout after {exc.timeout}s",
        })
        print(json.dumps(payload, sort_keys=True))
        return 124
    payload["push_output_tail"] = tail(push)
    if push.returncode != 0:
        payload.update({
            "ok": False,
            "stage": "push",
            "push": "failed",
            "push_exit_code": push.returncode,
        })
        print(json.dumps(payload, sort_keys=True))
        return push.returncode

    payload.update({"ok": True, "stage": "done", "push": "ok"})
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
