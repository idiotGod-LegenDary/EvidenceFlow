"""Tool registry construction."""

from app.business.orders import OrderGatewayFactory, database_order_gateway
from app.retrieval.hybrid import HybridRetriever
from app.tools.aftersale_tools import (
    CancelOrderTool,
    CreateRefundRequestTool,
    CreateReturnRequestTool,
    GetRefundStatusTool,
)
from app.tools.base import Tool
from app.tools.knowledge_tools import (
    EscalateToHumanTool,
    GetCouponTool,
    GetWarrantyTool,
    SearchKnowledgeTool,
)
from app.tools.logistics_tools import TrackShipmentTool
from app.tools.order_tools import GetOrderItemsTool, GetOrderTool
from app.tools.product_tools import (
    CheckInventoryTool,
    GetProductDetailTool,
    RecommendProductsTool,
    SearchProductsTool,
)
from app.tools.registry import ToolRegistry


def build_tool_registry(
    retriever: HybridRetriever, gateway_factory: OrderGatewayFactory = database_order_gateway
) -> ToolRegistry:
    """Process-scoped registry. DB-backed tools resolve the request session lazily."""
    registry = ToolRegistry()
    tools: list[Tool] = [
        GetOrderTool(gateway_factory=gateway_factory),
        GetOrderItemsTool(gateway_factory=gateway_factory),
        TrackShipmentTool(gateway_factory=gateway_factory),
        GetRefundStatusTool(gateway_factory=gateway_factory),
        CreateRefundRequestTool(gateway_factory=gateway_factory),
        CreateReturnRequestTool(gateway_factory=gateway_factory),
        CancelOrderTool(gateway_factory=gateway_factory),
        SearchProductsTool(),
        GetProductDetailTool(),
        RecommendProductsTool(),
        GetCouponTool(),
        CheckInventoryTool(),
        GetWarrantyTool(),
        SearchKnowledgeTool(retriever),
        EscalateToHumanTool(),
    ]
    for tool in tools:
        registry.register(tool)
    return registry
