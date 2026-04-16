from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from ..config import TeacherSettings
from ..models import MemoryRecord, TaskRequest, TeacherResponse


class OpenAICompatibleTeacher:
    def __init__(self, settings: TeacherSettings) -> None:
        self.settings = settings

    def generate(self, task: TaskRequest, memories: list[MemoryRecord], student_hint: str | None = None) -> TeacherResponse:
        system_prompt = (
            "You are the teacher model for a coding agent. "
            "Return JSON with keys plan (array), candidate_patch (string or null), rationale (string)."
        )
        user_prompt = {
            "task_id": task.task_id,
            "workspace": task.workspace,
            "prompt": task.prompt,
            "memories": [
                {"category": item.category, "content": item.content, "metadata": item.metadata}
                for item in memories
            ],
            "student_hint": student_hint,
        }
        payload = {
            "model": self.settings.model,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_prompt, sort_keys=True)},
            ],
        }
        token = os.environ.get(self.settings.api_key_env, "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(
            f"{self.settings.base_url.rstrip('/')}{self.settings.endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.settings.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Teacher request failed: {exc.code} {message}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Teacher request failed: {exc}") from exc
        content = self._extract_content(raw)
        parsed = self._extract_json(content)
        self._validate_payload(parsed)
        return TeacherResponse(
            plan=list(parsed.get("plan", [])),
            candidate_patch=parsed.get("candidate_patch"),
            rationale=parsed.get("rationale"),
            raw=raw,
        )

    def _extract_content(self, raw: dict[str, Any]) -> str:
        content = raw["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif item.get("type") == "text" and isinstance(item.get("content"), str):
                        parts.append(item["content"])
            if parts:
                return "".join(parts)
        raise RuntimeError("Teacher response content is not a supported format.")

    def _extract_json(self, content: str) -> dict[str, Any]:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("plan", []), list):
            raise RuntimeError("Teacher response must include a plan array.")
        candidate_patch = payload.get("candidate_patch")
        if candidate_patch is not None and not isinstance(candidate_patch, str):
            raise RuntimeError("Teacher response candidate_patch must be a string or null.")
        rationale = payload.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise RuntimeError("Teacher response rationale must be a string or null.")


class StaticTeacher:
    def __init__(self, response: TeacherResponse) -> None:
        self.response = response
        self.last_memories: list[MemoryRecord] = []

    def generate(self, task: TaskRequest, memories: list[MemoryRecord], student_hint: str | None = None) -> TeacherResponse:
        self.last_memories = list(memories)
        return self.response
