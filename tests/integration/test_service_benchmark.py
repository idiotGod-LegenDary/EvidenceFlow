import argparse
import copy
import json

import pytest

from app.config.settings import Settings
from app.observability.trace import CallBudget
from eval.service_benchmark import business_checks, run_suite, run_task, summarize
from eval.service_dataset import DATASET, load_tasks


def test_dataset_is_frozen_and_splits_do_not_share_paraphrase_families():
    tasks = load_tasks()
    assert len(tasks) == len({t.id for t in tasks}) == 100
    assert len({t.family for t in tasks}) == 25
    assert sum(t.split == "dev" for t in tasks) == 20
    dev = {t.family for t in tasks if t.split == "dev"}
    test = {t.family for t in tasks if t.split == "test"}
    assert dev.isdisjoint(test)


@pytest.mark.parametrize("failure", ["duplicate_id", "shared_family"])
def test_invalid_dataset_is_rejected(tmp_path, failure):
    rows = [t.model_dump() for t in load_tasks()[:2]]
    if failure == "duplicate_id":
        rows[1]["id"] = rows[0]["id"]
    else:
        rows[1]["split"] = "test"
    path = tmp_path / "tasks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_tasks(path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "family",
    ["cancel_direct", "refund_direct", "changed_policy", "duplicate_confirm", "shipped_cancel"],
)
async def test_benchmark_grades_business_state_and_interventions(family):
    task = next(t for t in load_tasks() if t.family == family)
    result = await run_task(task, Settings(), CallBudget(1))
    assert result["success"], result["checks"]
    assert not result["unsafe_write"] and result["model_calls"] == []
    damaged = copy.deepcopy(result["after"])
    damaged["orders"][task.order_id]["total_cents"] += 1
    assert not all(
        business_checks(
            task,
            result["expected_before_confirmation"],
            damaged,
            task.goal == "execute" and task.intervention in {"none", "duplicate"},
            result["case"]["action"]["arguments"],
        ).values()
    )


@pytest.mark.asyncio
async def test_online_suite_requires_key_before_creating_run(tmp_path):
    args = argparse.Namespace(mode="online", output=tmp_path / "run")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        await run_suite(args)
    assert not args.output.exists()


def test_cost_is_unknown_when_any_attempt_has_missing_usage():
    result = {
        "goal": "execute",
        "success": True,
        "unsafe_write": False,
        "checks": {},
        "latency_ms": 1,
        "model_calls": [
            {"usage_reported": True, "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
            {"usage_reported": False},
        ],
    }
    summary = summarize([result], 2, mode="online", input_price=1, output_price=2)
    assert summary["estimated_cost"] is None and summary["unrun"] == 1
    result["model_calls"].pop()
    assert (
        summarize([result], 1, mode="online", input_price=1, output_price=2)["estimated_cost"]
        == 0.00014
    )


@pytest.mark.asyncio
async def test_suite_writes_replayable_artifacts(tmp_path):
    args = argparse.Namespace(
        mode="offline",
        split="dev",
        dataset=DATASET,
        limit=1,
        repeats=1,
        max_calls=1,
        timeout=10,
        output=tmp_path / "run",
        input_price_per_million=None,
        output_price_per_million=None,
        currency="unspecified",
    )
    summary = await run_suite(args)
    rows = [
        json.loads(line)
        for line in (args.output / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert summary["attempted"] == summary["passed"] == len(rows) == 1
    assert rows[0]["tool_calls"] and rows[0]["before"] != rows[0]["after"]
    assert json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))["dataset_sha256"]
