from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare(before: Path, after: Path) -> dict:
    summaries = [
        json.loads((p / "summary.json").read_text(encoding="utf-8")) for p in (before, after)
    ]
    keys = (
        "dataset_sha256",
        "fixture_sha256",
        "mode",
        "split",
        "oracle_version",
        "requested_model",
        "thinking",
        "retrieval",
        "runtime_limits",
        "actual_models",
    )
    mismatches = [key for key in keys if summaries[0].get(key) != summaries[1].get(key)]
    if mismatches:
        raise ValueError("Incomparable run configuration: " + ", ".join(mismatches))
    runs = []
    for path in (before, after):
        rows = [
            json.loads(line)
            for line in (path / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        indexed = {(r["task_id"], r["repeat"]): r for r in rows}
        if len(indexed) != len(rows):
            raise ValueError("Duplicate task/repeat pair")
        runs.append(indexed)
    if not runs[0] or runs[0].keys() != runs[1].keys():
        raise ValueError("Runs must contain the same attempted tasks and repeats")
    improved, regressed = [], []
    for key, row in runs[0].items():
        other = runs[1][key]
        if not row["success"] and other["success"]:
            improved.append(list(key))
        elif row["success"] and not other["success"]:
            regressed.append(list(key))
    return {
        "attempted_pairs": len(runs[0]),
        "before": {
            k: summaries[0][k]
            for k in ("passed", "unsafe_writes", "model_http_attempts", "median_task_latency_ms")
        },
        "after": {
            k: summaries[1][k]
            for k in ("passed", "unsafe_writes", "model_http_attempts", "median_task_latency_ms")
        },
        "improved": improved,
        "regressed": regressed,
        "note": "Descriptive paired comparison; paraphrases and repeated trials are correlated.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare runs with matching tasks and evaluation conditions."
    )
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.before, args.after)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
