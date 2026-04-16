from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from ..models import MemoryRecord


class InMemoryMemoryClient:
    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []

    def _score(self, query: str, record: MemoryRecord) -> float:
        query_terms = {item for item in query.lower().split() if item}
        if not query_terms:
            return 0.0
        body = f"{record.category} {record.content} {' '.join(str(v) for v in record.metadata.values())}".lower()
        matched = sum(1.0 for term in query_terms if term in body)
        return matched / len(query_terms)

    def search_memories(self, query: str, filters: dict | None = None, limit: int = 5) -> list[MemoryRecord]:
        categories = set(filters.get("categories", [])) if filters else None
        matches: list[MemoryRecord] = []
        for record in self.records:
            if categories and record.category not in categories:
                continue
            score = self._score(query, record)
            if score > 0:
                matches.append(replace(record, score=score))
        matches.sort(key=lambda item: item.score or 0.0, reverse=True)
        return matches[:limit]

    def store_memory(self, category: str, content: str, metadata: dict) -> MemoryRecord:
        identifier = sha256(f"{category}:{content}:{len(self.records)}".encode("utf-8")).hexdigest()[:12]
        record = MemoryRecord(id=identifier, category=category, content=content, metadata=dict(metadata))
        self.records.append(record)
        return record

    def record_outcome(self, episode_id: str, worked: bool, evidence: dict) -> MemoryRecord:
        category = "episode_summary" if worked else "failure"
        return self.store_memory(category, f"Episode {episode_id} worked={worked}", {"episode_id": episode_id, **dict(evidence)})

    def get_active_context(self, task_scope: str, limit: int = 8) -> list[MemoryRecord]:
        return self.search_memories(task_scope, {"categories": ["decision", "warning", "failure", "fact", "growth", "episode_summary"]}, limit=limit)
