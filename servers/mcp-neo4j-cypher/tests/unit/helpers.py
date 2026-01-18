from unittest.mock import AsyncMock

from neo4j import AsyncDriver


def build_async_driver() -> AsyncDriver:
    return AsyncMock(spec=AsyncDriver)
