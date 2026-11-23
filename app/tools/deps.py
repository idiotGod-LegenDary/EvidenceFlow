"""Repository-backed tool base."""

from app.business.orders import (
    DatabaseOrderGateway,
    OrderGateway,
    OrderGatewayFactory,
    database_order_gateway,
)
from app.core.request_scope import get_db_session
from app.repositories.order_repository import OrderRepository


class RepositoryBackedTool:
    """Tools hold an optional repo for tests; production resolves the request session."""

    def __init__(self, repository: OrderRepository | None = None) -> None:
        self._repository = repository

    @property
    def repository(self) -> OrderRepository:
        if self._repository is not None:
            return self._repository
        return OrderRepository(get_db_session())


class GatewayBackedTool:
    def __init__(
        self,
        repository: OrderRepository | None = None,
        *,
        gateway_factory: OrderGatewayFactory = database_order_gateway,
    ) -> None:
        self._gateway = DatabaseOrderGateway(repository) if repository is not None else None
        self._gateway_factory = gateway_factory

    @property
    def gateway(self) -> OrderGateway:
        return (
            self._gateway if self._gateway is not None else self._gateway_factory(get_db_session())
        )
