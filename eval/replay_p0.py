from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay an archived application with the frozen P0 evaluator."
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--variant", choices=["baseline", "full"], required=True)
    parser.add_argument("--mode", choices=["offline", "online"], default="offline")
    parser.add_argument("--repeat", type=int, default=0)
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads((ROOT / "eval/dataset/p0_oracle_lock.json").read_text(encoding="utf-8"))
    if any(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected
        for path, expected in lock["files"].items()
    ):
        parser.error("Evaluation code differs from the frozen oracle")
    if args.max_calls < 1 or args.repeat < 0:
        parser.error("Invalid budget or repeat index")
    with tempfile.TemporaryDirectory(prefix="p0-replay-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(args.snapshot) as archive:
            if any(
                Path(name).is_absolute() or ".." in Path(name).parts for name in archive.namelist()
            ):
                parser.error("Unsafe snapshot paths")
            archive.extractall(root)
        command = [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0,sys.argv.pop(1)); from eval.p0_runner import main; main()",
            str(root),
            "--dataset",
            str(ROOT / "eval/dataset/p0_sealed.jsonl"),
            "--variant",
            args.variant,
            "--mode",
            args.mode,
            "--repeat",
            str(args.repeat),
            "--max-calls",
            str(args.max_calls),
            "--output",
            str(args.output),
        ]
        if args.mode == "online":
            protocol = json.loads(
                (ROOT / "eval/dataset/p0_protocol.json").read_text(encoding="utf-8")
            )
            command += ["--task-ids", ",".join(protocol["online_task_ids"])]
        raise SystemExit(
            subprocess.run(
                command, cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, check=False
            ).returncode
        )


if __name__ == "__main__":
    main()
