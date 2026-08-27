"""Regression tests for long-term-memory cache accounting."""

import asyncio

import pytest

from app.core.cache import InMemoryCacheService
from app.services.memory import MemoryService


class EmptyMemory:
    """Return a successful memory lookup with no matching records."""

    def __init__(self) -> None:
        """Initialize the provider call counter."""
        self.search_calls = 0

    async def search(self, user_id: str, query: str) -> dict[str, list]:
        """Record provider calls and return an empty result set."""
        self.search_calls += 1
        return {"results": []}


def test_successful_empty_memory_search_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated empty searches should produce a real cache hit."""
    service = MemoryService()
    memory = EmptyMemory()
    cache = InMemoryCacheService(default_ttl=60)

    async def get_memory() -> EmptyMemory:
        return memory

    monkeypatch.setattr(service, "_get_memory", get_memory)
    monkeypatch.setattr("app.services.memory.cache_service", cache)

    async def exercise() -> tuple[str, str]:
        await cache.initialize()
        first = await service.search("user-1", "same question")
        second = await service.search("user-1", "same question")
        return first, second

    first, second = asyncio.run(exercise())

    assert first == second == ""
    assert memory.search_calls == 1
    assert service.cache_stats() == {"hits": 1, "misses": 1, "hit_rate": 0.5, "scope": "instance"}
