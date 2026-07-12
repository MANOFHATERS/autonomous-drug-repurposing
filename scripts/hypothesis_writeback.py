#!/usr/bin/env python3
"""Hypothesis writeback helper for /api/hypothesis/validate route.

RT-010 ROOT FIX (Team Member 17): shells out from the Next.js route
to the phase4.writeback module. The route cannot import Python directly,
so it spawns this script via child_process.spawn.

Usage:
    python3 scripts/hypothesis_writeback.py <req_path> <resp_path>

Request JSON (read from <req_path>):
    {
        "drug": "metformin",
        "disease": "type 2 diabetes",
        "outcome": "validated_positive",
        "validated_by": "org_abc123",
        "validation_study_id": "NCT12345678",
        "notes": "Wet lab confirmed efficacy at 50uM",
        "original_gt_score": 0.87,
        "original_rl_rank": 3
    }

Response JSON (written to <resp_path>):
    {
        "result": { ... },
        "error": null | "error message"
    }
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for _p in (str(REPO_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: hypothesis_writeback.py <req_path> <resp_path>", file=sys.stderr)
        return 2
    req_path, resp_path = sys.argv[1], sys.argv[2]

    try:
        with open(req_path) as f:
            req = json.load(f)
    except Exception as e:
        _write_resp(resp_path, {"error": f"Could not read request: {e}"})
        return 1

    try:
        from phase4.writeback import write_validated_hypothesis
        result = write_validated_hypothesis(
            drug=req["drug"],
            disease=req["disease"],
            outcome=req["outcome"],
            validated_by=req["validated_by"],
            validation_study_id=req.get("validation_study_id"),
            notes=req.get("notes"),
            original_gt_score=req.get("original_gt_score"),
            original_rl_rank=req.get("original_rl_rank"),
        )
        _write_resp(resp_path, {"result": result, "error": None})
        return 0
    except Exception as e:
        tb = traceback.format_exc()
        _write_resp(resp_path, {"error": f"{type(e).__name__}: {e}", "traceback": tb})
        return 1


def _write_resp(resp_path: str, payload: dict) -> None:
    with open(resp_path, "w") as f:
        json.dump(payload, f, default=str)


if __name__ == "__main__":
    sys.exit(main())
