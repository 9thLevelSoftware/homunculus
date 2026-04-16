from __future__ import annotations

from typing import Protocol

from ..models import MemoryRecord


class MemoryContract(Protocol):
    def search_memories(self, query: str, filters: dict | None = None, limit: int = 5) -> list[MemoryRecord]:
        ...

    def store_memory(self, category: str, content: str, metadata: dict) -> MemoryRecord:
        ...

    def record_outcome(self, episode_id: str, worked: bool, evidence: dict) -> MemoryRecord:
        ...

    def get_active_context(self, task_scope: str, limit: int = 8) -> list[MemoryRecord]:
        ...
