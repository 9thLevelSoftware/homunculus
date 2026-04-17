from __future__ import annotations

from .config import GuardrailSettings
from .models import GuardrailDecision, MemoryRecord


class GuardrailEngine:
    def __init__(self, settings: GuardrailSettings) -> None:
        self.settings = settings

    def evaluate(self, prompt: str, candidate_patch: str | None, memories: list[MemoryRecord]) -> GuardrailDecision:
        body = f"{prompt}\n{candidate_patch or ''}"
        warnings: list[str] = []
        blocked: list[str] = []
        memory_refs: list[str] = []

        for rule in self.settings.warn_patterns:
            if rule.regex.search(body):
                warnings.append(rule.message)

        for rule in self.settings.block_patterns:
            if rule.regex.search(body):
                blocked.append(rule.message)

        for memory in memories:
            if memory.category in {"warning", "failure"}:
                warnings.append(f"Relevant {memory.category}: {memory.content[:120]}")
                memory_refs.append(memory.id)

        return GuardrailDecision(
            allowed=not blocked,
            warnings=warnings,
            blocked_reasons=blocked,
            memory_refs=memory_refs,
        )
