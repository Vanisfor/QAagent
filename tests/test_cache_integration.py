"""Valkey integration coverage for the distributed cache backend."""

import asyncio
import os
from uuid import uuid4

import pytest

from app.core.cache import ValkeyCacheService

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv("RUN_VALKEY_TESTS") != "1", reason="set RUN_VALKEY_TESTS=1")
def test_valkey_cache_round_trip() -> None:
    """A value can be stored, read, and deleted through Valkey."""

    async def run() -> None:
        service = ValkeyCacheService(default_ttl=30)
        key = f"integration:{uuid4()}"
        await service.initialize()
        try:
            await service.set(key, "value")
            assert await service.get(key) == "value"
            await service.delete(key)
            assert await service.get(key) is None
        finally:
            await service.close()

    asyncio.run(run())
