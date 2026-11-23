from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.order_repository import OrderRepository
from app.schemas.order import Order


class RefundRecord(BaseModel):
    refund_id: str
    order_id: str
    status: str
    amount_cents: int
    reason: str


class ReturnRecord(BaseModel):
    return_id: str
    order_id: str
    status: str
    reason: str


@dataclass
class OrderState:
    order: Order
    refunds: list[RefundRecord]
    returns: list[ReturnRecord]


class OrderGateway(Protocol):
    supports_local_transactions: bool

    async def get_visible_order(self, order_id: str, user_id: str) -> Order: ...
    async def lock_order(self, order_id: str) -> None: ...
    async def inspect(self, order_id: str, user_id: str) -> OrderState: ...
    async def get_refunds(self, order_id: str) -> list[RefundRecord]: ...
    async def cancel_order(self, order_id: str, *, trace_id: str) -> dict: ...
    async def create_refund(self, *, order_id: str, reason: str, trace_id: str) -> dict: ...
    async def create_return(self, *, order_id: str, reason: str, trace_id: str) -> dict: ...


OrderGatewayFactory = Callable[[AsyncSession], OrderGateway]


class DatabaseOrderGateway:
    supports_local_transactions = True

    def __init__(self, repository: OrderRepository) -> None:
        self._repository = repository

    async def get_visible_order(self, order_id: str, user_id: str) -> Order:
        return await self._repository.get_visible_order(order_id, user_id)

    async def lock_order(self, order_id: str) -> None:
        await self._repository.lock_order(order_id)

    async def get_refunds(self, order_id: str) -> list[RefundRecord]:
        return [
            RefundRecord(
                refund_id=r.refund_id,
                order_id=r.order_id,
                status=r.status,
                amount_cents=r.amount_cents,
                reason=r.reason,
            )
            for r in await self._repository.get_refunds(order_id)
        ]

    async def inspect(self, order_id: str, user_id: str) -> OrderState:
        return OrderState(
            order=await self.get_visible_order(order_id, user_id),
            refunds=await self.get_refunds(order_id),
            returns=[
                ReturnRecord(
                    return_id=r.return_id, order_id=r.order_id, status=r.status, reason=r.reason
                )
                for r in await self._repository.get_returns(order_id)
            ],
        )

    async def cancel_order(self, order_id: str, *, trace_id: str) -> dict:
        return await self._repository.cancel_order(order_id, trace_id=trace_id)

    async def create_refund(self, *, order_id: str, reason: str, trace_id: str) -> dict:
        return await self._repository.create_refund(
            order_id=order_id, reason=reason, trace_id=trace_id
        )

    async def create_return(self, *, order_id: str, reason: str, trace_id: str) -> dict:
        return await self._repository.create_return(
            order_id=order_id, reason=reason, trace_id=trace_id
        )


def database_order_gateway(session: AsyncSession) -> OrderGateway:
    return DatabaseOrderGateway(OrderRepository(session))


def verify_operation(
    tool_name: str, arguments: dict, result: dict, before: OrderState, after: OrderState
) -> dict[str, bool]:
    order_id = before.order.order_id
    expected = before.order.model_dump(mode="json")
    checks = {
        "order_matches": result.get("order_id") == order_id and after.order.order_id == order_id
    }
    counts_before = (len(before.refunds), len(before.returns))
    counts_after = (len(after.refunds), len(after.returns))
    if tool_name == "cancel_order":
        expected["status"] = "cancelled"
        checks.update(
            order_cancelled=after.order.status.value == "cancelled",
            no_extra_records=counts_after == counts_before,
            order_fields_match=after.order.model_dump(mode="json") == expected,
        )
    elif tool_name == "create_refund_request":
        expected["status"] = "refunding"
        refund = next((r for r in after.refunds if r.refund_id == result.get("refund_id")), None)
        checks.update(
            order_refunding=after.order.status.value == "refunding",
            one_refund=counts_after == (counts_before[0] + 1, counts_before[1]),
            refund_matches=refund is not None
            and refund.order_id == order_id
            and refund.amount_cents == before.order.total_cents
            and refund.reason == arguments.get("reason")
            and refund.status == "requested",
            order_fields_match=after.order.model_dump(mode="json") == expected,
        )
    elif tool_name == "create_return_request":
        returned = next((r for r in after.returns if r.return_id == result.get("return_id")), None)
        checks.update(
            one_return=counts_after == (counts_before[0], counts_before[1] + 1),
            order_unchanged=after.order.model_dump(mode="json") == expected,
            return_matches=returned is not None
            and returned.order_id == order_id
            and returned.reason == arguments.get("reason")
            and returned.status == "requested",
        )
    else:
        checks["supported_action"] = False
    return checks
