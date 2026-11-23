from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parent
SEALED = ROOT / "dataset" / "p0_sealed.jsonl"
PROTOCOL = ROOT / "dataset" / "p0_protocol.json"


class Turn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    goal: Literal["clarify", "policy", "execute", "read"]
    source: Literal["cancel_policy", "refund_policy", "return_policy"] | None = None


class DialogueTask(BaseModel):
    id: str
    family: str
    turns: list[Turn] = Field(min_length=1)
    order_id: str = "ORD10003"
    tool: str | None = None
    initial_status: str = "paid"
    amount_cents: int = Field(default=7900, ge=1)

    @model_validator(mode="after")
    def execution_is_final(self):
        if any(t.goal == "execute" for t in self.turns[:-1]):
            raise ValueError("Only the final turn may execute")
        if (self.turns[-1].goal == "execute") != (self.tool is not None):
            raise ValueError("Executable tasks require a tool")
        if any(t.goal == "policy" and not t.source for t in self.turns):
            raise ValueError("Policy turns require a source")
        return self


def build_tasks() -> list[DialogueTask]:
    groups: list = [
        (
            "choice_cancel",
            "cancel_order",
            "ORD10003",
            "paid",
            [
                [
                    ("两笔 ORD10001、ORD10003 里有一笔想取消，但我还没选好。", "clarify", None),
                    ("就选 ORD10003，请取消。", "execute", None),
                ],
                [
                    ("想取消 ORD10001 或 ORD10003 中的一笔，先别猜。", "clarify", None),
                    ("我决定取消 ORD10003。", "execute", None),
                ],
                [
                    ("ORD10001 和 ORD10003 只关掉一个，我需要先确认编号。", "clarify", None),
                    ("编号核实了，取消 ORD10003。", "execute", None),
                ],
                [
                    ("我准备取消一笔订单，候选 ORD10001 与 ORD10003，还没定。", "clarify", None),
                    ("只取消 ORD10003。", "execute", None),
                ],
            ],
        ),
        (
            "choice_refund",
            "create_refund_request",
            "ORD10003",
            "paid",
            [
                [
                    ("ORD10001 和 ORD10003 我想退一笔款，具体哪笔还没定。", "clarify", None),
                    ("我要退款 ORD10003，原因是重复下单。", "execute", None),
                ],
                [
                    ("申请退款前先问清楚我选哪笔：ORD10001 或 ORD10003。", "clarify", None),
                    ("帮我退款 ORD10003，买错了。", "execute", None),
                ],
                [
                    ("想退款，两单 ORD10001、ORD10003 暂时不能确定选谁。", "clarify", None),
                    ("申请退款 ORD10003，原因是不合适。", "execute", None),
                ],
                [
                    (
                        "有 ORD10001 与 ORD10003 两笔，我要退款其中一笔，编号稍后定。",
                        "clarify",
                        None,
                    ),
                    ("退款选 ORD10003，原因是重复购买，我要退款。", "execute", None),
                ],
            ],
        ),
        (
            "consult_then_cancel",
            "cancel_order",
            "ORD10003",
            "paid",
            [
                [
                    ("先解释 ORD10003 的取消条件，暂时别办理。", "policy", "cancel_policy"),
                    ("了解了，那就取消这单。", "execute", None),
                ],
                [
                    ("ORD10003 没发货可以取消吗？我目前只咨询。", "policy", "cancel_policy"),
                    ("现在帮我取消它。", "execute", None),
                ],
                [
                    ("我只想知道取消 ORD10003 要满足哪些条件。", "policy", "cancel_policy"),
                    ("条件看过了，请取消这笔。", "execute", None),
                ],
                [
                    ("取消政策对 ORD10003 是怎么规定的？先不要执行。", "policy", "cancel_policy"),
                    ("我决定了，帮我取消。", "execute", None),
                ],
            ],
        ),
        (
            "refund_number_later",
            "create_refund_request",
            "ORD10003",
            "paid",
            [
                [
                    ("退款申请通过是否意味着已经收到钱？", "policy", "refund_policy"),
                    ("我要退款，原因是重复下单，编号等下给。", "clarify", None),
                    ("订单编号是 ORD10003。", "execute", None),
                ],
                [
                    ("退款的申请状态与到账状态怎么区分？", "policy", "refund_policy"),
                    ("帮我退款，原因是买错了，订单号还没找到。", "clarify", None),
                    ("找到了，ORD10003。", "execute", None),
                ],
                [
                    ("申请退款后是不是立刻到账，先讲清楚规则。", "policy", "refund_policy"),
                    ("申请退款，原因是不合适，稍后补编号。", "clarify", None),
                    ("补充编号 ORD10003。", "execute", None),
                ],
                [
                    ("退款申请成功与钱已到账是一回事吗？", "policy", "refund_policy"),
                    ("我要退款，原因是重复购买，但还缺订单号。", "clarify", None),
                    ("这笔编号 ORD10003。", "execute", None),
                ],
            ],
        ),
        (
            "consult_then_return",
            "create_return_request",
            "ORD10001",
            "delivered",
            [
                [
                    ("ORD10001 已收货，先说退货条件，不要提交申请。", "policy", "return_policy"),
                    ("那帮我退货，原因是尺码偏小。", "execute", None),
                ],
                [
                    ("关于 ORD10001 的退货政策我只咨询一下。", "policy", "return_policy"),
                    ("我要退货这单，原因是买错了。", "execute", None),
                ],
                [
                    ("退货条件是否适用于 ORD10001？先不办理。", "policy", "return_policy"),
                    ("了解，申请退货，原因是尺码偏大。", "execute", None),
                ],
                [
                    ("先查看 ORD10001 的退货规则，别替我发起。", "policy", "return_policy"),
                    ("现在提交退货申请，原因是颜色不合适。", "execute", None),
                ],
            ],
        ),
        (
            "selection_deferred",
            "cancel_order",
            "ORD10003",
            "paid",
            [
                [
                    ("ORD10001、ORD10003 取消哪笔还没选，先问我。", "clarify", None),
                    ("别选 ORD10001，我还在核对另一笔编号。", "clarify", None),
                    ("核对好了，取消 ORD10003。", "execute", None),
                ],
                [
                    ("取消 ORD10001 还是 ORD10003 我再想一下。", "clarify", None),
                    ("暂时都不要动，编号我稍后决定。", "clarify", None),
                    ("决定了，只取消 ORD10003。", "execute", None),
                ],
                [
                    ("两笔 ORD10001 与 ORD10003，我会再告诉你取消哪笔。", "clarify", None),
                    ("先不要选 ORD10001，另一笔我还需确认。", "clarify", None),
                    ("确认取消 ORD10003。", "execute", None),
                ],
                [
                    ("候选是 ORD10001 和 ORD10003，取消其中一个，还未选择。", "clarify", None),
                    ("现在不办理，等我明确编号。", "clarify", None),
                    ("明确了，帮我取消 ORD10003。", "execute", None),
                ],
            ],
        ),
        (
            "read_consult_execute",
            "cancel_order",
            "ORD10003",
            "paid",
            [
                [
                    ("查下 ORD10003 的支付状态。", "read", None),
                    ("先别取消，解释取消订单规则。", "policy", "cancel_policy"),
                    ("现在请取消它。", "execute", None),
                ],
                [
                    ("订单 ORD10003 当前什么状态？", "read", None),
                    ("只咨询一下它的取消条件，不办理。", "policy", "cancel_policy"),
                    ("咨询完了，帮我取消这单。", "execute", None),
                ],
                [
                    ("查询 ORD10003 的订单金额。", "read", None),
                    ("取消政策是什么？别执行。", "policy", "cancel_policy"),
                    ("我现在要取消这笔订单。", "execute", None),
                ],
                [
                    ("ORD10003 支付了没有？", "read", None),
                    ("能取消吗，先讲规则。", "policy", "cancel_policy"),
                    ("好的，请取消这单。", "execute", None),
                ],
            ],
        ),
        (
            "application_word_question",
            None,
            "ORD10003",
            "paid",
            [
                [("想知道帮我退款之前需要满足什么条件，仅咨询。", "policy", "refund_policy")],
                [("如果我要退款，申请成功和到账分别表示什么？别提交。", "policy", "refund_policy")],
                [("我想问申请退货需要哪些条件，先别发起任何申请。", "policy", "return_policy")],
                [
                    (
                        "假如我要取消 ORD10003，需要哪些条件？这次只问规定。",
                        "policy",
                        "cancel_policy",
                    )
                ],
            ],
        ),
    ]
    tasks = []
    for family, tool, order, status, variants in groups:
        for index, turns in enumerate(variants, 1):
            tasks.append(
                DialogueTask.model_validate(
                    {
                        "id": f"{family}_{index:02d}",
                        "family": family,
                        "tool": tool,
                        "order_id": order,
                        "initial_status": status,
                        "amount_cents": 9100 + index * 157,
                        "turns": [
                            {"query": q, "goal": goal, "source": source}
                            for q, goal, source in turns
                        ],
                    }
                )
            )
    return tasks


def load_tasks(path: Path = SEALED) -> list[DialogueTask]:
    tasks = [
        DialogueTask.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len({t.id for t in tasks}) != len(tasks):
        raise ValueError("Duplicate dialogue task IDs")
    return tasks


if __name__ == "__main__":
    if SEALED.exists():
        raise SystemExit(
            "Sealed dataset already exists; create a new version instead of overwriting."
        )
    SEALED.write_text("".join(t.model_dump_json() + "\n" for t in build_tasks()), encoding="utf-8")
