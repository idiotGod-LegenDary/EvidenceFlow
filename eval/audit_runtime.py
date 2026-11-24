from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "eval/baselines/pre_independent_runtime.zip"
SNAPSHOT_SHA256 = "caf79c94a49ef9fbc189a272d03e253a7106d2fcdd4a690a1c54445bce4ae360"
REPLACED = (
    "app/services/chat_service.py",
    "app/tools/base.py",
    "app/tools/registry.py",
    "app/retrieval/hybrid.py",
    "app/retrieval/fusion.py",
)
RETIRED = ("app/agents/", "app/skills/", "app/memory/", "app/prompts/")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def audit() -> dict:
    if digest(SNAPSHOT.read_bytes()) != SNAPSHOT_SHA256:
        raise ValueError("Pre-rewrite snapshot digest mismatch")
    with zipfile.ZipFile(SNAPSHOT) as archive:
        before = {name: digest(archive.read(name)) for name in archive.namelist()}
    current = {
        path.relative_to(ROOT).as_posix(): digest(path.read_bytes())
        for path in sorted(ROOT.glob("app/**/*.py"))
    }
    new_runtime = {name: sha for name, sha in current.items() if name.startswith("app/runtime/")}
    if not new_runtime or set(new_runtime) & set(before):
        raise ValueError("New runtime inventory does not match the rewrite boundary")
    replacements = {}
    for name in REPLACED:
        if before[name] == current[name]:
            raise ValueError(f"Expected replacement is byte-identical: {name}")
        replacements[name] = {"before_sha256": before[name], "current_sha256": current[name]}
    if any(name.startswith(RETIRED) for name in current):
        raise ValueError("Retired runtime remains in active source")
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    if any("langgraph" in item or "langchain" in item for item in dependencies):
        raise ValueError("Legacy framework remains a default dependency")
    return {
        "snapshot": SNAPSHOT.relative_to(ROOT).as_posix(),
        "snapshot_sha256": SNAPSHOT_SHA256,
        "snapshot_files": len(before),
        "new_runtime": new_runtime,
        "replacements": replacements,
        "retired_application_files": {
            name: sha
            for name, sha in before.items()
            if name.startswith("app/") and name not in current
        },
        "unchanged_application_files": {
            name: sha for name, sha in current.items() if before.get(name) == sha
        },
        "default_dependencies_exclude_legacy_frameworks": True,
        "scope": "Structural and byte-level audit; not a claim that all retained files are original.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit the runtime replacement against its snapshot."
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(audit(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(report)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
