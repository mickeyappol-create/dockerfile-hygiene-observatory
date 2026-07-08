#!/usr/bin/env python3
"""Rebase over origin/main, then push.

For Hermes cron jobs that share the repo with GitHub Actions. Outputs compact
JSON so Telegram reports expose pull/push failures instead of hiding them.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

MAX_ATTEMPTS = 3


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def tail(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout + "\n" + proc.stderr).strip()[-1200:]


def is_retryable_push_failure(text: str) -> bool:
    lower = text.lower()
    return (
        "fetch first" in lower
        or "non-fast-forward" in lower
        or "[rejected]" in lower
        or "updates were rejected" in lower
    )


def main() -> int:
    payload: dict[str, object] = {"schema": "apex-git-sync-push/1"}
    attempts = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        record: dict[str, object] = {"attempt": attempt}
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
                "attempts": attempts + [record],
            })
            print(json.dumps(payload, sort_keys=True))
            return 124
        record["pull_output_tail"] = tail(pull)
        if pull.returncode != 0:
            payload.update({
                "ok": False,
                "stage": "pull",
                "pull": "failed",
                "pull_exit_code": pull.returncode,
                "pull_output_tail": record["pull_output_tail"],
                "push": "skipped",
                "attempts": attempts + [record],
            })
            print(json.dumps(payload, sort_keys=True))
            return pull.returncode

        try:
            push = run(["git", "push", "origin", "main"], timeout=60)
        except subprocess.TimeoutExpired as exc:
            payload.update({
                "ok": False,
                "stage": "push",
                "push": "failed",
                "push_exit_code": 124,
                "push_output_tail": f"git push timeout after {exc.timeout}s",
                "attempts": attempts + [record],
            })
            print(json.dumps(payload, sort_keys=True))
            return 124
        record["push_output_tail"] = tail(push)
        attempts.append(record)
        if push.returncode == 0:
            payload.update({
                "ok": True,
                "stage": "done",
                "pull": "ok",
                "push": "ok",
                "attempt": attempt,
                "attempts": attempts,
                "pull_output_tail": record["pull_output_tail"],
                "push_output_tail": record["push_output_tail"],
            })
            print(json.dumps(payload, sort_keys=True))
            return 0

        if attempt < MAX_ATTEMPTS and is_retryable_push_failure(str(record["push_output_tail"])):
            time.sleep(attempt)
            continue

        payload.update({
            "ok": False,
            "stage": "push",
            "pull": "ok",
            "push": "failed",
            "push_exit_code": push.returncode,
            "pull_output_tail": record["pull_output_tail"],
            "push_output_tail": record["push_output_tail"],
            "attempt": attempt,
            "attempts": attempts,
        })
        print(json.dumps(payload, sort_keys=True))
        return push.returncode

    payload.update({"ok": False, "stage": "push", "push": "failed", "attempts": attempts})
    print(json.dumps(payload, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
