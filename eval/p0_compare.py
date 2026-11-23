from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from eval.p0_dataset import PROTOCOL, SEALED

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "eval/baselines/pre_p0.zip"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(output: Path) -> dict:
    variants = {}
    for variant in ("baseline", "full"):
        rows: list[dict] = []
        for path in sorted(output.glob(f"r*/{variant}/tasks.jsonl")):
            rows.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        calls = [c for r in rows for t in r["turns"] for c in t["model_calls"]]
        reported = [c for c in calls if c.get("usage_reported")]
        per_family = {}
        for family in sorted({r["family"] for r in rows}):
            selected = [r for r in rows if r["family"] == family]
            per_family[family] = {
                "passed": sum(r["success"] for r in selected),
                "attempted": len(selected),
            }
        variants[variant] = {
            "attempted": len(rows),
            "passed": sum(r["success"] for r in rows),
            "success_rate": sum(r["success"] for r in rows) / len(rows) if rows else None,
            "wrong_proposal_trials": sum(r["wrong_proposal"] for r in rows),
            "wrong_execution_trials": sum(r["wrong_execution"] for r in rows),
            "model_http_attempts": len(calls),
            "usage_reported_attempts": len(reported),
            "reported_prompt_tokens": sum(c["usage"]["prompt_tokens"] for c in reported)
            if reported
            else None,
            "reported_completion_tokens": sum(c["usage"]["completion_tokens"] for c in reported)
            if reported
            else None,
            "median_trial_latency_ms": statistics.median(r["latency_ms"] for r in rows)
            if rows
            else None,
            "by_family": per_family,
        }
    paired: dict[str, dict[tuple[str, int], bool]] = {}
    for variant in ("baseline", "full"):
        paired[variant] = {}
        for path in output.glob(f"r*/{variant}/tasks.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                paired[variant][(row["task_id"], row["repeat"])] = row["success"]
    common = paired["baseline"].keys() & paired["full"].keys()
    return {
        "variants": variants,
        "paired_trials": len(common),
        "improved": [
            list(k) for k in sorted(common) if not paired["baseline"][k] and paired["full"][k]
        ],
        "regressed": [
            list(k) for k in sorted(common) if paired["baseline"][k] and not paired["full"][k]
        ],
        "note": "Developer-authored preregistered scenarios; correlated repeats, no significance claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run frozen pre-P0 application and current application with one oracle."
    )
    parser.add_argument("--mode", choices=["offline", "online"], default="offline")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=240)
    args = parser.parse_args()
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if (
        digest(SEALED) != protocol["dataset_sha256"]
        or digest(BASELINE) != protocol["baseline_sha256"]
    ):
        raise ValueError("Frozen dataset or baseline archive changed")
    if args.max_calls < 1 or (
        args.mode == "online" and args.max_calls > protocol["online_max_calls_total"]
    ):
        parser.error("Request budget must be positive and cannot exceed the registered online cap")
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "protocol": protocol,
        "max_calls": args.max_calls,
        "python": platform.python_version(),
        "evaluator_sha256": digest(ROOT / "eval/p0_runner.py"),
        "orchestrator_sha256": digest(Path(__file__)),
        "protocol_sha256": digest(PROTOCOL),
        "confirmation_policy": protocol["confirmation_policy"],
        "restart_between_turns": True,
    }
    manifest["full_source_sha256"] = hashlib.sha256(
        b"".join(
            p.relative_to(ROOT).as_posix().encode() + b"\0" + p.read_bytes()
            for p in sorted(
                [*ROOT.glob("app/**/*.py"), *ROOT.glob("app/skills/definitions/*.json")]
            )
        )
    ).hexdigest()
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    used = 0
    repeats = protocol[f"{args.mode}_repeats"]
    with tempfile.TemporaryDirectory(prefix="pre-p0-") as tmp:
        folder = Path(tmp)
        with zipfile.ZipFile(BASELINE) as archive:
            for name in archive.namelist():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("Unsafe archive path")
            archive.extractall(folder)
        for repeat in range(repeats):
            for variant in ("baseline", "full") if repeat % 2 == 0 else ("full", "baseline"):
                if args.mode == "online" and used >= args.max_calls:
                    break
                dest = args.output / f"r{repeat}" / variant
                app_root = folder if variant == "baseline" else ROOT
                command = [
                    sys.executable,
                    "-c",
                    "import sys; sys.path.insert(0,sys.argv.pop(1)); from eval.p0_runner import main; main()",
                    str(app_root),
                    "--dataset",
                    str(SEALED),
                    "--output",
                    str(dest),
                    "--variant",
                    variant,
                    "--mode",
                    args.mode,
                    "--repeat",
                    str(repeat),
                    "--max-calls",
                    str(max(args.max_calls - used, 1)),
                ]
                if args.mode == "online":
                    command += ["--task-ids", ",".join(protocol["online_task_ids"])]
                with (args.output / f"r{repeat}-{variant}.log").open(
                    "w", encoding="utf-8"
                ) as stream:
                    completed = subprocess.run(
                        command,
                        cwd=ROOT,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        env={**os.environ, "PYTHONUTF8": "1"},
                        check=False,
                    )
                if completed.returncode:
                    raise RuntimeError(f"{variant} worker failed; inspect its local log")
                summary = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
                used += summary["model_http_attempts"]
                print(
                    f"r{repeat} {variant}: {summary['passed']}/{summary['attempted']} calls={summary['model_http_attempts']}",
                    flush=True,
                )
                (args.output / "summary.json").write_text(
                    json.dumps(aggregate(args.output), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    summary = aggregate(args.output)
    summary.update(
        model_http_attempts_total=used,
        budget_exhausted=args.mode == "online" and used >= args.max_calls,
        planned_trials_per_variant=(
            len(protocol["online_task_ids"]) if args.mode == "online" else protocol["task_count"]
        )
        * repeats,
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
