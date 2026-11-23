"""Reproducible full-vs-incremental evidence lifecycle experiment (no API key).

Uses hash vectors to measure work and snapshot correctness, not semantic quality
or real model inference latency. All mutations happen in temporary corpus copies.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import platform
import shutil
import statistics
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.providers.embeddings import HashEmbeddingProvider
from app.retrieval.knowledge import KnowledgeRuntime

SCENARIOS = ("unchanged", "modify_one", "add_one", "delete_one", "shrink_one")


def mutate(root: Path, scenario: str) -> None:
    target = root / "return_policy.md"
    if scenario == "modify_one":
        target.write_text(
            target.read_text(encoding="utf-8")
            + "\n\n## 新增生效条款\n试验订单的政策已更新，以新条款为准。\n",
            encoding="utf-8",
        )
    elif scenario == "add_one":
        (root / "experiment_policy.md").write_text(
            "# 试验政策\n新增加的试验商品支持预约配送。\n", encoding="utf-8"
        )
    elif scenario == "delete_one":
        target.unlink()
    elif scenario == "shrink_one":
        target.write_text("# 精简政策\n试验政策只保留本条有效说明。\n", encoding="utf-8")


async def signature(kb: KnowledgeRuntime) -> list[tuple[str, str]]:
    hits = await kb.retriever.retrieve("退货政策更新", top_k=10000)
    assert kb.report is not None
    assert all(hit.knowledge_version == kb.report.version for hit in hits)
    return sorted((hit.chunk_id, hit.content_sha256) for hit in hits)


async def measure(source: Path, scenario: str, *, force: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix="knowledge-eval-") as tmp:
        root = Path(tmp) / "knowledge"
        shutil.copytree(source, root)
        # Pydantic accepts _env_file at runtime; its generated mypy signature omits it.
        config: dict[str, Any] = {
            "_env_file": None,
            "app_env": "testing",
            "knowledge_dir": str(root),
            "embedding_api_key": "",
            "local_embedding_enabled": False,
            "unstructured_enabled": False,
            "qdrant_url": "",
            "embedding_dim": 32,
        }
        kb = KnowledgeRuntime(
            Settings(**config),
            HashEmbeddingProvider(dim=32),
        )
        initial = await kb.reload()
        await signature(kb)  # prime the same query's old-version cache
        mutate(root, scenario)
        report = await kb.reload(force=force)
        after = await signature(kb)
        assert len(after) == report.chunks
        if scenario == "delete_one":
            assert not any(chunk_id.startswith("return_policy_") for chunk_id, _ in after)
        return {
            "initial_chunks": initial.chunks,
            "report": report.model_dump(),
            "signature": after,
        }


async def run(source: Path, repeats: int) -> dict:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if not (source / "return_policy.md").is_file():
        raise ValueError("experiment corpus must include return_policy.md")
    # Tokenizer initialization / imports are outside measured reload windows.
    from app.retrieval.tokenizer import tokenize

    tokenize("预热退货政策")
    files = sorted(path for path in source.iterdir() if path.is_file())
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    raw = []
    summaries = []
    for scenario in SCENARIOS:
        by_mode: dict[str, list[dict]] = {"full": [], "incremental": []}
        for repeat in range(repeats):
            # Alternate order; report paired observations, no significance claim.
            order = ("full", "incremental") if repeat % 2 == 0 else ("incremental", "full")
            pair = {}
            for mode in order:
                result = await measure(source, scenario, force=mode == "full")
                pair[mode] = result
                by_mode[mode].append(result["report"])
                raw.append({"scenario": scenario, "repeat": repeat, "mode": mode, **result})
            assert pair["full"]["signature"] == pair["incremental"]["signature"]
            assert pair["full"]["report"]["version"] == pair["incremental"]["report"]["version"]
        full, incremental = by_mode["full"][0], by_mode["incremental"][0]
        summaries.append(
            {
                "scenario": scenario,
                "documents": full["documents"],
                "chunks": full["chunks"],
                "full_embedded_texts": full["embedded_texts"],
                "incremental_embedded_texts": incremental["embedded_texts"],
                "reused_texts": incremental["reused_texts"],
                "embedding_work_reduction": 1
                - incremental["embedded_texts"] / full["embedded_texts"],
                "full_reload_p50_ms": statistics.median(
                    item["elapsed_ms"] for item in by_mode["full"]
                ),
                "incremental_reload_p50_ms": statistics.median(
                    item["elapsed_ms"] for item in by_mode["incremental"]
                ),
                "same_evidence": True,
            }
        )
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "upstream_commit": "2a12a62382a8c3ec033fa9a5b19b9b23b8780ab8",
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "packages": {
                n: importlib.metadata.version(n) for n in ("pydantic", "jieba", "rank-bm25")
            },
        },
        "embedding_backend": "HashEmbeddingProvider(dim=32)",
        "scope": "embedding input counts and evidence consistency; not semantic retrieval quality or real LLM latency",
        "repeats": repeats,
        "corpus_manifest": manifest,
        "corpus_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "summary": summaries,
        "raw": raw,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", type=Path, default=Path("knowledge"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output", type=Path, default=Path("eval/experiments/knowledge_lifecycle.json")
    )
    args = parser.parse_args()
    result = asyncio.run(run(args.knowledge, args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"Raw paired observations: {args.output}")


if __name__ == "__main__":
    main()
