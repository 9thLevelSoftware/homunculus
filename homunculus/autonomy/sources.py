"""SC2 source-name vocabulary.

Producers (task_generator, suggestions) emit :class:`GeneratedTask.source`
literals; consumers (reporter) classify those literals into SC2 buckets.
Keeping the vocabulary in one module prevents drift like the original
B3 defect where the reporter matched ``{"generated", "resonance"}`` but
no producer ever emitted those values.

Add a new source literal here first, then in the producing module, then
in ``GeneratedTask.source``'s docstring — in that order.
"""
from __future__ import annotations

from typing import Literal

# Values emitted by ``task_generator.TaskGenerator`` (all introspection-
# derived) and any future continuation source the daemon adds. The
# reporter counts these as SC2 self-directed.
SELF_DIRECTED_SOURCES: frozenset[str] = frozenset({"introspection", "continuation"})

# Values emitted by ``suggestions.SuggestionReader`` when an operator
# drops a file under ``suggestions/``. Counts toward SC2 suggestion tasks.
SUGGESTION_SOURCES: frozenset[str] = frozenset({"user"})

SourceClass = Literal["self_directed", "suggestion", "other"]


def classify_source(raw: str | None) -> SourceClass:
    """Classify a ``GeneratedTask.source`` literal into an SC2 bucket.

    The reporter calls this instead of open-coding ``source in {...}``
    so a future rename or addition only has to touch this module.
    Case- and whitespace-insensitive; ``None`` or unknown literals
    return ``"other"`` (neither bucket counts them).
    """
    if not raw:
        return "other"
    normalized = raw.strip().lower()
    if normalized in SELF_DIRECTED_SOURCES:
        return "self_directed"
    if normalized in SUGGESTION_SOURCES:
        return "suggestion"
    return "other"
