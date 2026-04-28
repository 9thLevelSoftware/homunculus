"""Symphony-style Linear orchestration for Homunculus.

The package implements the repository-local orchestration contract described in
``WORKFLOW.md``: poll Linear for work, create persistent issue workspaces, run an
agent, gate the branch, and record durable run evidence.
"""
from __future__ import annotations

from .models import (
    AgentResult,
    BlockerRef,
    IssueRecord,
    LiveSession,
    MergeGateResult,
    RetryEntry,
    RunAttempt,
    SymphonyConfig,
    WorkflowDefinition,
    WorkspaceRecord,
)
from .orchestrator import SymphonyOrchestrator
from .workflow import WorkflowError, load_workflow

__all__ = [
    "AgentResult",
    "BlockerRef",
    "IssueRecord",
    "LiveSession",
    "MergeGateResult",
    "RetryEntry",
    "RunAttempt",
    "SymphonyConfig",
    "SymphonyOrchestrator",
    "WorkflowDefinition",
    "WorkflowError",
    "WorkspaceRecord",
    "load_workflow",
]
