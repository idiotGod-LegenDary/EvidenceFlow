from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import SecretStr

import app
from app.config.settings import Settings
from app.observability.trace import CallBudget, capture_trace
from app.schemas.chat import ChatRequest
from app.services.container import build_container
from eval.p0_dataset import DialogueTask, load_tasks
from eval.service_benchmark import POLICIES, business_checks, database_state, prepare_fixture
from eval.service_dataset import ServiceTask


def fixture_task(task: DialogueTask) -> ServiceTask:
    return ServiceTask.model_validate(
        {
            "id": task.id,
            "family": task.family,
            "split": "test",
            "query": task.turns[-1].query,
            "goal": "execute" if task.tool else "policy",
            "tool": task.tool,
            "order_id": task.order_id,
            "initial_status": task.initial_status,
            "amount_cents": task.amount_cents,
        }
    )


def grade_turn(
    turn,
    response: dict | None,
    case: dict | None,
    trace: dict,
    task: DialogueTask,
    before: dict,
    after: dict,
    error: str | None,
    status: str | None,
    known_citations: dict,
) -> dict:
    action = (case or {}).get("action") or (response or {}).get("pending_action")
    correct_action = bool(
        action
        and action["tool_name"] == task.tool
        and action["arguments"].get("order_id") == task.order_id
    )
    wrong_proposal = bool(action) and (turn.goal != "execute" or not correct_action)
    checks = {"response_received": response is not None, "no_runtime_error": error is None}
    business = business_checks(
        fixture_task(task),
        before,
        after,
        turn.goal == "execute",
        (action or {}).get("arguments", {}),
    )
    checks.update(business)
    if turn.goal == "execute":
        checks.update(correct_action=correct_action, completed=status == "completed")
    else:
        checks["no_proposal"] = action is None and not (response or {}).get("case_id")
    if turn.goal == "clarify":
        answer = (response or {}).get("answer", "")
        checks["clarification"] = "订单" in answer and any(
            word in answer for word in ("哪", "提供", "选", "补充", "明确")
        )
    elif turn.goal == "policy":
        citations = (response or {}).get("citations", [])
        checks["source_cited"] = any(
            known_citations.get(c["chunk_id"], {}).get("source") == turn.source for c in citations
        )
        checks["citations_resolve"] = bool(citations) and all(
            c["chunk_id"] in known_citations
            and c["content_sha256"] == known_citations[c["chunk_id"]]["sha256"]
            and c["knowledge_version"] == known_citations[c["chunk_id"]]["version"]
            for c in citations
        )
    elif turn.goal == "read":
        checks["order_read"] = any(
            t["success"]
            and t["name"] in {"get_order", "get_order_items"}
            and t["arguments"].get("order_id") == task.order_id
            for t in trace["tool_calls"]
        )
    return {
        "checks": checks,
        "success": all(checks.values()),
        "wrong_proposal": wrong_proposal,
        "wrong_execution": before != after and not all(business.values()),
    }


async def trial(task: DialogueTask, settings: Settings, budget: CallBudget) -> dict:
    started = time.perf_counter()
    rows = []
    with TemporaryDirectory(prefix="dialogue-eval-") as tmp:
        root = Path(tmp)
        knowledge = root / "knowledge"
        knowledge.mkdir()
        for name, text in POLICIES.items():
            (knowledge / name).write_text(text, encoding="utf-8")
        settings = settings.model_copy(
            update={
                "database_url": f"sqlite+aiosqlite:///{(root / 'trial.db').as_posix()}",
                "knowledge_dir": str(knowledge),
                "redis_url": "",
                "qdrant_url": "",
                "embedding_api_key": SecretStr(""),
                "embedding_base_url": "",
                "local_embedding_enabled": False,
                "cross_encoder_enabled": False,
                "unstructured_enabled": False,
                "mcp_enabled": False,
                "rate_limit_per_minute": 10000,
            }
        )
        container = await build_container(settings, seed=True)
        session_id = None
        try:
            await prepare_fixture(container, fixture_task(task))
            for index, turn in enumerate(task.turns):
                before = await database_state(container)
                known = {}
                assert container.knowledge is not None
                for document in container.knowledge._documents.values():
                    for chunk in document.chunks:
                        known[chunk.chunk_id] = {
                            "source": Path(chunk.metadata["source"]).stem,
                            "sha256": hashlib.sha256(chunk.content.encode()).hexdigest(),
                            "version": container.retriever.published_version,
                        }
                response = case = None
                status = error = None
                with capture_trace(budget) as trace:
                    try:
                        async with asyncio.timeout(120):
                            reply = await container.chat_service.chat(
                                ChatRequest(
                                    message=turn.query, user_id="u_demo", session_id=session_id
                                ),
                                trace_id=f"{task.id}_{index}",
                            )
                            session_id = reply.session_id
                            response = reply.model_dump(mode="json")
                            if reply.case_id:
                                case = (
                                    await container.chat_service.cases.detail(
                                        reply.case_id, "u_demo"
                                    )
                                ).model_dump(mode="json")
                            before_confirm = await database_state(container)
                            if reply.pending_action:
                                confirmed = await container.chat_service.chat(
                                    ChatRequest(
                                        message="确认执行",
                                        user_id="u_demo",
                                        session_id=session_id,
                                        confirm_action_id=reply.pending_action.action_id,
                                    ),
                                    trace_id=f"{task.id}_{index}_confirm",
                                )
                                status = confirmed.case_status
                    except Exception as exc:
                        error = type(exc).__name__
                        before_confirm = await database_state(container)
                after = await database_state(container)
                exported = trace.export()
                graded = grade_turn(
                    turn, response, case, exported, task, before, after, error, status, known
                )
                graded["checks"]["no_write_before_confirmation"] = before == before_confirm
                if settings.llm_configured:
                    graded["checks"]["model_requests_succeeded"] = all(
                        c["success"] for c in exported["model_calls"]
                    )
                    graded["checks"]["structured_outputs_valid"] = not exported["output_errors"]
                graded["success"] = all(graded["checks"].values())
                graded["wrong_execution"] |= before != before_confirm
                rows.append(
                    {
                        "query": turn.query,
                        "goal": turn.goal,
                        "response": response,
                        "case": case,
                        "error_type": error,
                        "before": before,
                        "before_confirm": before_confirm,
                        "after": after,
                        "final_status": status,
                        **graded,
                        **exported,
                    }
                )
                if error == "EvaluationBudgetExceeded":
                    break
                if index < len(task.turns) - 1:
                    await container.aclose()
                    container = await build_container(settings)
        finally:
            await container.aclose()
    return {
        "task_id": task.id,
        "family": task.family,
        "turns": rows,
        "success": len(rows) == len(task.turns) and all(r["success"] for r in rows),
        "wrong_proposal": any(r["wrong_proposal"] for r in rows),
        "wrong_execution": any(r["wrong_execution"] for r in rows),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


async def run(args) -> dict:
    settings = Settings()
    if args.mode == "offline":
        settings = settings.model_copy(update={"deepseek_api_key": SecretStr("")})
    elif not settings.llm_configured:
        raise ValueError("Online evaluation requires DEEPSEEK_API_KEY")
    tasks = load_tasks(args.dataset)
    if args.task_ids:
        wanted = set(args.task_ids.split(","))
        tasks = [t for t in tasks if t.id in wanted]
        if {t.id for t in tasks} != wanted:
            raise ValueError("Unknown task IDs")
    args.output.mkdir(parents=True, exist_ok=False)
    budget = CallBudget(args.max_calls)
    rows = []
    with (args.output / "tasks.jsonl").open("w", encoding="utf-8") as stream:
        for task in tasks:
            if args.mode == "online" and budget.used >= budget.max_calls:
                break
            result = await trial(task, settings, budget)
            result["repeat"] = args.repeat
            rows.append(result)
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()
            print(
                f"{args.variant} r{args.repeat} {task.id}: {'PASS' if result['success'] else 'FAIL'}",
                flush=True,
            )
            if any(t["error_type"] == "EvaluationBudgetExceeded" for t in result["turns"]):
                break
    calls = [c for r in rows for t in r["turns"] for c in t["model_calls"]]
    reported = [c for c in calls if c.get("usage_reported")]
    summary = {
        "application_sha256": hashlib.sha256(
            b"".join(
                p.relative_to(Path(app.__file__).parent).as_posix().encode()
                + b"\0"
                + p.read_bytes()
                for p in sorted(
                    [
                        *Path(app.__file__).parent.glob("**/*.py"),
                        *Path(app.__file__).parent.glob("skills/definitions/*.json"),
                    ]
                )
            )
        ).hexdigest(),
        "settings": {
            key: getattr(settings, key)
            for key in (
                "deepseek_model",
                "deepseek_thinking",
                "llm_timeout_seconds",
                "llm_max_retries",
                "embedding_dim",
                "retrieval_top_k_simple",
                "retrieval_top_k_complex",
                "rerank_top_n",
                "max_retrieval_hops",
                "rrf_k",
                "max_agent_steps",
                "max_tool_calls",
            )
        },
        "fixture_sha256": hashlib.sha256(json.dumps(POLICIES, sort_keys=True).encode()).hexdigest(),
        "variant": args.variant,
        "repeat": args.repeat,
        "selected": len(tasks),
        "attempted": len(rows),
        "unrun": len(tasks) - len(rows),
        "passed": sum(r["success"] for r in rows),
        "wrong_proposals": sum(r["wrong_proposal"] for r in rows),
        "wrong_executions": sum(r["wrong_execution"] for r in rows),
        "model_http_attempts": len(calls),
        "usage_reported_attempts": len(reported),
        "reported_prompt_tokens": sum(c["usage"]["prompt_tokens"] for c in reported)
        if reported
        else None,
        "reported_completion_tokens": sum(c["usage"]["completion_tokens"] for c in reported)
        if reported
        else None,
        "actual_models": sorted({c["actual_model"] for c in calls if c.get("actual_model")}),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=["baseline", "full"], required=True)
    parser.add_argument("--mode", choices=["offline", "online"], required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--task-ids", default="")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))


if __name__ == "__main__":
    main()
