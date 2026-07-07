#!/usr/bin/env python3
"""Record the daily synthesis decision next to the trend input."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICT = dt.timezone(dt.timedelta(hours=7), name="ICT")


def pipeline_today() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone(ICT).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=pipeline_today())
    parser.add_argument("--status", choices=["skipped", "created", "failed"], required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--candidate", default="")
    parser.add_argument("--source", default="apex-card-synthesis")
    args = parser.parse_args()

    out_path = ROOT / "trends" / f"{args.date}.synthesis.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "apex-synthesis-decision/1",
        "date": args.date,
        "date_basis": "ICT/UTC+7 pipeline date",
        "recorded_at": dt.datetime.now(dt.timezone.utc).astimezone(ICT).isoformat(),
        "status": args.status,
        "reason": args.reason,
        "candidate": args.candidate or None,
        "source": args.source,
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(out_path.relative_to(ROOT)), "status": args.status}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
