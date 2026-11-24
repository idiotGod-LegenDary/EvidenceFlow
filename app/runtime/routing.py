from __future__ import annotations

import re
from typing import Any

from app.business.dialogue import ORDER_PATTERN, is_policy_question
from app.core.enums import IntentLabel
from app.runtime.contracts import RequestRoute

OPERATIONS: dict[str, dict[str, Any]] = {
    "cancel_order": {"name": "取消订单", "prefix": "取消订单", "intent": IntentLabel.CANCEL_ORDER},
    "create_refund_request": {
        "name": "退款申请",
        "prefix": "我要退款",
        "intent": IntentLabel.REFUND,
    },
    "create_return_request": {
        "name": "退货申请",
        "prefix": "我要退货",
        "intent": IntentLabel.RETURN_EXCHANGE,
    },
}


def operation_specs() -> list[dict]:
    return [
        {"name": name, "description": spec["name"], "requires_confirmation": True}
        for name, spec in OPERATIONS.items()
    ]


def route_request(query: str, selected_order: str | None = None) -> RequestRoute:
    if any(
        word in query.lower()
        for word in ("股市", "股票", "写作业", "医疗诊断", "炸弹", "jailbreak", "系统提示词")
    ):
        return RequestRoute("refuse", IntentLabel.OUT_OF_SCOPE)
    if re.fullmatch(r"(?:你好|您好|hi|hello|谢谢)[\s!！。~]*", query.strip(), re.IGNORECASE):
        return RequestRoute("greeting", IntentLabel.GREETING)
    ids = list(dict.fromkeys(x.upper() for x in ORDER_PATTERN.findall(query)))
    order_id = ids[0] if len(ids) == 1 else selected_order if not ids else None
    if is_policy_question(query):
        return RequestRoute("policy", IntentLabel.FAQ_POLICY, order_id)
    for tool, spec in OPERATIONS.items():
        if query.startswith(str(spec["prefix"]) + " "):
            return RequestRoute("operation", IntentLabel(str(spec["intent"].value)), order_id, tool)
    if any(word in query for word in ("人工客服", "转人工", "值班经理")):
        return RequestRoute("handoff", IntentLabel.HUMAN_HANDOFF, order_id)
    if any(word in query for word in ("物流", "快递", "运单", "轨迹")):
        return RequestRoute("lookup", IntentLabel.LOGISTICS, order_id)
    if "退款" in query:
        return RequestRoute("lookup", IntentLabel.REFUND, order_id)
    if order_id:
        return RequestRoute("lookup", IntentLabel.ORDER_INQUIRY, order_id)
    if any(word in query for word in ("商品", "推荐", "SKU-", "库存", "耳机", "T恤")):
        return RequestRoute("lookup", IntentLabel.PRODUCT_CONSULT)
    return RequestRoute("policy", IntentLabel.FAQ_POLICY)
