from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

ORDER_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])ORD\d+(?![A-Za-z0-9_-])", re.IGNORECASE)
PREFIXES = {"cancel": "取消订单", "refund": "我要退款", "return": "我要退货"}
NEGATED_OPERATION = re.compile(
    r"(?:不要|别|不想|先不|暂不)(?:帮我|替我|给我)?(?:取消|退款|退货|申请|办理)"
)


def policy_topic(text: str) -> str | None:
    for term in ("取消", "退货", "退款"):
        if term in text:
            return term
    return None


def is_policy_question(text: str) -> bool:
    clauses = re.split(r"[，,。；;]", text.rstrip("。；; "))
    last_clause = clauses[-1]
    prefix = "，".join(clauses[:-1])
    acknowledged = any(cue in prefix for cue in ("看过", "了解", "明白", "清楚", "知道了"))
    if (
        policy_topic(text)
        and not acknowledged
        and any(cue in prefix for cue in ("条件", "规则", "政策", "规定"))
    ):
        return True
    last_has_question = any(
        cue in last_clause
        for cue in (
            "条件",
            "规则",
            "政策",
            "规定",
            "怎么",
            "吗",
            "？",
            "?",
            "是否",
            "能否",
            "能不能",
        )
    )
    if NEGATED_OPERATION.search(last_clause) and not last_has_question:
        return False
    if (
        not any(word in text for word in ("如果", "假如", "要是"))
        and not NEGATED_OPERATION.search(last_clause)
        and any(
            cue in last_clause
            for cue in (
                "请取消",
                "帮我取消",
                "我要取消",
                "我要退款",
                "申请退款",
                "帮我退款",
                "我要退货",
                "申请退货",
                "帮我退货",
                "提交退货",
            )
        )
        and not last_has_question
    ):
        return False
    return policy_topic(text) is not None and any(
        cue in text
        for cue in (
            "规则",
            "政策",
            "条件",
            "规定",
            "能不能",
            "是否",
            "可以吗",
            "能退吗",
            "能退货吗",
            "能取消吗",
            "什么区别",
            "有什么区别",
            "一回事",
            "区别",
            "代表",
            "意味着",
            "怎么",
            "吗",
            "？",
            "?",
            "解释",
            "仅咨询",
            "只咨询",
            "只问",
            "先说",
            "先查看",
        )
    )


class DialogueSlots(BaseModel):
    phase: Literal["idle", "awaiting_order", "awaiting_selection"] = "idle"
    operation: Literal["cancel", "refund", "return"] | None = None
    selected_order: str | None = None
    candidates: list[str] = Field(default_factory=list)
    reason: str = ""


def resolve_turn(text: str, previous: DialogueSlots) -> tuple[DialogueSlots, str, str | None]:
    slots = previous.model_copy(deep=True)
    ids = list(dict.fromkeys(m.upper() for m in ORDER_PATTERN.findall(text)))
    topic = policy_topic(text)
    if is_policy_question(text):
        if ids:
            slots.selected_order = ids[0] if len(ids) == 1 else None
            slots.candidates = ids
        slots.phase, slots.operation, slots.reason = "idle", None, ""
        return slots, text, None

    if NEGATED_OPERATION.search(text) or any(
        cue in text
        for cue in (
            "不要取消",
            "别取消",
            "不取消",
            "不要退款",
            "不申请退款",
            "不要退货",
            "不申请退货",
            "放弃办理",
            "撤回申请",
        )
    ):
        slots.phase, slots.operation, slots.reason = "idle", None, ""
        return slots, text, "本次未提交新的订单操作。已有待审核建议请在工单中撤销。"

    operation: Literal["cancel", "refund", "return"] | None = None
    if any(cue in text for cue in ("我要退款", "申请退款", "帮我退款", "想退款", "想退一笔款")):
        operation = "refund"
    elif any(cue in text for cue in ("我要退货", "申请退货", "帮我退货", "提交退货")):
        operation = "return"
    elif "取消" in text or "不想要了" in text or "关掉" in text:
        operation = "cancel"
    deferred = any(
        cue in text
        for cue in (
            "没选",
            "未选",
            "没定",
            "未定",
            "不确定",
            "没确定",
            "未确定",
            "还不能确定",
            "不能确定",
            "还在核对",
            "还需确认",
            "还需核实",
            "再想",
            "稍后定",
            "等我",
            "再告诉",
            "不要动",
            "不办理",
            "现在不办理",
            "别选",
            "不要选",
            "先不要选",
            "还需确认",
            "还未选择",
        )
    )
    if operation is not None:
        slots.operation = operation
        if operation != "cancel":
            match = re.search(r"原因[是：:]*\s*(.+)", text)
            slots.reason = (match.group(1) if match else text)[:160]
    actionable = slots.operation is not None and (operation is not None or slots.phase != "idle")
    if operation and any(
        cue in text
        for cue in ("订单号还没", "编号等下", "订单号还缺", "订单编号稍后", "不知道订单号")
    ):
        slots.selected_order = None
    if actionable and (len(ids) > 1 or deferred):
        slots.candidates = ids if len(ids) > 1 else slots.candidates
        slots.selected_order = None
        slots.phase = "awaiting_selection"
        return slots, text, "请明确要处理哪一个订单，选定前不会提交操作。"
    if ids:
        slots.selected_order = ids[0] if len(ids) == 1 else None
        slots.candidates = ids
    if actionable:
        assert slots.operation is not None
        if not slots.selected_order:
            slots.phase = "awaiting_order"
            return slots, text, "请提供要处理的订单号，确认后我才能继续。"
        resolved = f"{PREFIXES[slots.operation]} {slots.selected_order}"
        if slots.reason:
            resolved += f"，原因是{slots.reason}"
        slots.phase, slots.operation = "idle", None
        return slots, resolved, None
    if len(ids) > 1 and topic:
        slots.phase = "awaiting_selection"
        return slots, text, "请明确要处理哪一个订单，选定前不会提交操作。"
    return slots, text, None
