from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from ..config import MemorySettings
from ..models import MemoryRecord


class EngramMemoryClient:
    def __init__(self, settings: MemorySettings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(self.settings.bearer_token_env, "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url.rstrip('/')}{endpoint}"
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.settings.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Engram request failed: {exc.code} {message}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Engram request failed: {exc}") from exc

    def search_memories(self, query: str, filters: dict | None = None, limit: int = 5) -> list[MemoryRecord]:
        response = self._request(self.settings.search_endpoint, {"query": query, "filters": filters or {}, "limit": limit})
        rows = response.get("results", response.get("items", response if isinstance(response, list) else []))
        return [MemoryRecord.from_dict(item) for item in rows]

    def store_memory(self, category: str, content: str, metadata: dict) -> MemoryRecord:
        response = self._request(self.settings.store_endpoint, {"content": content, "category": category, "metadata": metadata})
        if "id" not in response:
            response = {
                "id": str(response.get("memory_id", "")),
                "category": category,
                "content": content,
                "metadata": metadata,
            }
        return MemoryRecord.from_dict(response)

    def record_outcome(self, episode_id: str, worked: bool, evidence: dict) -> MemoryRecord:
        category = "episode_summary" if worked else "failure"
        content = json.dumps({"episode_id": episode_id, "worked": worked, "evidence": evidence}, sort_keys=True)
        return self.store_memory(category=category, content=content, metadata={"episode_id": episode_id, "worked": worked})

    def get_active_context(self, task_scope: str, limit: int = 8) -> list[MemoryRecord]:
        filters = {"categories": ["decision", "warning", "failure", "fact", "growth", "episode_summary"]}
        return self.search_memories(task_scope, filters=filters, limit=limit)
