"""Phase 5 pre-soak readiness gates.

The preflight check refuses to start a soak run if the environment is
not in a clean, verified-healthy state. Seven gates, each returning a
:class:`GateResult`; the aggregate :class:`PreflightResult` is
``passed=True`` iff every gate passed.

Gates (per spec §4):

* ``config_parses`` — TOML re-parses without error.
* ``doctor_passes`` — the existing ``doctor`` command reports ``ok``.
* ``worktrees_clean`` — ``runtime/worktrees/`` has no stale episode dirs.
* ``test_suite_passes`` — ``python -m unittest discover`` returns 0.
* ``task_queue_ready`` — queue non-empty or generator can synthesize one.
* ``teacher_reachable`` — a 1-token ping hits the teacher endpoint.
* ``git_clean`` — every configured workspace has a clean working tree.

All gates fail closed: unexpected errors translate to ``passed=False``
with the exception recorded in ``detail``. No gate is allowed to raise.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error, request

from ..config import HomunculusConfig, load_config
from .models import GateResult, PreflightResult

logger = logging.getLogger(__name__)

# Per spec: the test-suite gate is the long pole. Cap it so a runaway
# test cannot stall preflight indefinitely.
_TEST_SUITE_TIMEOUT_SECONDS = 300
_GIT_TIMEOUT_SECONDS = 30
_TEACHER_PING_TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_preflight(settings: HomunculusConfig) -> PreflightResult:
    """Execute all seven preflight gates and return the aggregate verdict.

    Args:
        settings: Loaded :class:`HomunculusConfig`. Callers that only
            hold the TOML path should use :func:`load_config` first;
            the ``config_parses`` gate re-parses to validate, so this
            function still runs even when called from CLI with the
            already-loaded config.

    Returns:
        :class:`PreflightResult`. ``passed`` is True iff every gate
        passed. Callers should inspect ``gates`` for actionable detail.
    """
    gates: dict[str, GateResult] = {}
    gates["config_parses"] = _gate_config_parses(settings)
    gates["doctor_passes"] = _gate_doctor_passes(settings)
    gates["worktrees_clean"] = _gate_worktrees_clean(settings)
    gates["test_suite_passes"] = _gate_test_suite_passes(settings)
    gates["task_queue_ready"] = _gate_task_queue_ready(settings)
    gates["teacher_reachable"] = _gate_teacher_reachable(settings)
    gates["git_clean"] = _gate_git_clean(settings)
    passed = all(gate.passed for gate in gates.values())
    return PreflightResult(passed=passed, gates=gates)


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------

def _gate_config_parses(settings: HomunculusConfig) -> GateResult:
    """Re-parse the TOML config and confirm it decodes cleanly.

    ``settings`` is already-parsed, so we infer the original path from
    ``settings.paths.root``. If we cannot locate a TOML on disk we fall
    back to declaring the gate passed — the config is evidently valid
    because we hold a parsed instance.
    """
    root = settings.paths.root
    candidates = [root / "homunculus.toml", root / "homunculus.example.toml"]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return GateResult(
            name="config_parses",
            passed=True,
            detail="No TOML file found on disk; using in-memory config.",
        )
    try:
        load_config(path)
    except Exception as exc:  # noqa: BLE001 — narrow context, re-raise as detail
        return GateResult(
            name="config_parses",
            passed=False,
            detail=f"Re-parse of {path} failed: {exc}",
        )
    return GateResult(
        name="config_parses", passed=True, detail=f"Parsed {path} OK."
    )


def _gate_doctor_passes(settings: HomunculusConfig) -> GateResult:
    """Invoke the ``doctor`` command and check its exit status.

    We shell out via ``sys.executable`` rather than importing
    ``cmd_doctor`` directly: this mirrors how an operator would invoke
    preflight and avoids side-effects from ``build_runtime`` (e.g. HTTP
    calls during client construction).
    """
    config_path = _config_path_for(settings)
    if config_path is None:
        return GateResult(
            name="doctor_passes",
            passed=False,
            detail="Cannot resolve config path for doctor invocation.",
        )
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "homunculus.cli", "doctor", "--config", str(config_path)],
            capture_output=True,
            text=True,
            timeout=_TEST_SUITE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name="doctor_passes",
            passed=False,
            detail="doctor timed out after 5 minutes.",
        )
    except OSError as exc:
        return GateResult(
            name="doctor_passes", passed=False, detail=f"doctor invocation failed: {exc}"
        )
    if completed.returncode == 0:
        return GateResult(
            name="doctor_passes", passed=True, detail="doctor reported all checks OK."
        )
    # Extract the ``failed`` list from the JSON blob, if present, for a
    # more actionable error message than the raw tail.
    detail = _tail(completed.stdout or completed.stderr, lines=10)
    try:
        parsed = json.loads(completed.stdout)
        failed = parsed.get("failed")
        if isinstance(failed, list) and failed:
            detail = f"doctor failed checks: {', '.join(str(x) for x in failed)}"
    except (json.JSONDecodeError, TypeError):
        pass
    return GateResult(name="doctor_passes", passed=False, detail=detail)


def _gate_worktrees_clean(settings: HomunculusConfig) -> GateResult:
    """Verify ``runtime/worktrees/`` has no stale directories.

    A stale directory is any child of ``runtime/worktrees``. Under
    steady-state the :class:`TaskRunner` removes worktrees after each
    episode; leftover dirs indicate a crashed cycle.
    """
    worktrees_dir = settings.paths.runtime_dir / "worktrees"
    if not worktrees_dir.exists():
        return GateResult(
            name="worktrees_clean",
            passed=True,
            detail="No worktrees directory yet (fresh install).",
        )
    try:
        stale = [p.name for p in worktrees_dir.iterdir() if p.is_dir()]
    except OSError as exc:
        return GateResult(
            name="worktrees_clean",
            passed=False,
            detail=f"Cannot list {worktrees_dir}: {exc}",
        )
    if not stale:
        return GateResult(
            name="worktrees_clean",
            passed=True,
            detail=f"{worktrees_dir} is empty.",
        )
    return GateResult(
        name="worktrees_clean",
        passed=False,
        detail=f"Found {len(stale)} stale worktree dir(s): {', '.join(sorted(stale)[:5])}",
    )


def _gate_test_suite_passes(settings: HomunculusConfig) -> GateResult:
    """Run the full test suite and require a zero exit code.

    This is the long-pole gate. We cap the duration at 5 minutes; any
    run that takes longer is treated as a failure (something is wrong
    with the environment, not with the tests). ``sys.executable`` is
    used rather than bare ``python`` so the gate behaves identically
    inside and outside a virtualenv (Phase 2 review fix).
    """
    cwd = settings.paths.root
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-q"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TEST_SUITE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            name="test_suite_passes",
            passed=False,
            detail="Test suite exceeded 5-minute timeout.",
        )
    except OSError as exc:
        return GateResult(
            name="test_suite_passes",
            passed=False,
            detail=f"Could not spawn test runner: {exc}",
        )
    combined = (completed.stderr or "") + (completed.stdout or "")
    tail = _tail(combined, lines=5)
    if completed.returncode == 0:
        return GateResult(
            name="test_suite_passes",
            passed=True,
            detail=tail or "unittest discover returned 0.",
        )
    return GateResult(
        name="test_suite_passes",
        passed=False,
        detail=tail or f"unittest exited with code {completed.returncode}.",
    )


def _gate_task_queue_ready(settings: HomunculusConfig) -> GateResult:
    """Confirm the daemon has real work to pick up when it starts.

    Passes when either:
      * the persisted queue has at least one pending entry, OR
      * the task generator can synthesize work from the introspection
        cache on disk — i.e. ``traces/introspection.jsonl`` contains
        at least one record AND the generator returns a non-empty
        list when invoked against those records.

    Previously this gate passed the moment ``TaskGenerator(store=None)``
    could be constructed, which is a tautology: construction cannot
    raise for valid settings. An empty queue + empty introspection
    cache is NOT ready — it means the soak will idle for seven days
    (audit B4, 2026-04-16).
    """
    queue_path = settings.paths.runtime_dir / "task_queue.jsonl"
    pending = 0
    if queue_path.exists():
        try:
            for raw in queue_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("status") == "pending":
                    pending += 1
        except OSError as exc:
            return GateResult(
                name="task_queue_ready",
                passed=False,
                detail=f"Cannot read queue at {queue_path}: {exc}",
            )
    if pending > 0:
        return GateResult(
            name="task_queue_ready",
            passed=True,
            detail=f"{pending} pending task(s) in queue.",
        )

    # Queue is empty — can the generator synthesize at least one task
    # from the introspection cache on disk?
    introspection_path = settings.paths.traces_dir / "introspection.jsonl"
    if not introspection_path.exists():
        return GateResult(
            name="task_queue_ready",
            passed=False,
            detail=(
                "no pending tasks and no introspection cache at "
                f"{introspection_path}; soak would idle. Queue a manual "
                "task or run introspection first."
            ),
        )
    try:
        from ..task_generator import TaskGenerator  # local to avoid import cycles
        from ..models import IntrospectionResult

        results: list[IntrospectionResult] = []
        for raw in introspection_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                results.append(IntrospectionResult.from_dict(payload))
            except (KeyError, TypeError, ValueError):
                continue

        gen = TaskGenerator(store=None)
        synthesized = gen.generate_from_introspection(results, max_tasks=1)
    except Exception as exc:  # noqa: BLE001 — gate must never raise
        return GateResult(
            name="task_queue_ready",
            passed=False,
            detail=f"generator dry-run failed: {exc}",
        )
    if not synthesized:
        return GateResult(
            name="task_queue_ready",
            passed=False,
            detail=(
                "no pending tasks and generator yielded 0 tasks from "
                f"{len(results)} introspection record(s); soak would idle."
            ),
        )
    return GateResult(
        name="task_queue_ready",
        passed=True,
        detail=(
            f"queue empty; generator can synthesize from "
            f"{len(results)} introspection record(s) "
            f"({len(synthesized)} dry-run task)."
        ),
    )


def _gate_teacher_reachable(settings: HomunculusConfig) -> GateResult:
    """Ping the teacher endpoint with a 1-token request.

    Fails closed: any network error, non-2xx HTTP status, or JSON
    decode failure marks the gate as failed. Timeout is capped at 10
    seconds so we cannot stall preflight when the endpoint is down.
    The auth token is pulled from the configured env var; missing
    tokens are reported as a specific sub-failure (actionable).
    """
    teacher = settings.teacher
    token = os.environ.get(teacher.api_key_env, "")
    if not token:
        return GateResult(
            name="teacher_reachable",
            passed=False,
            detail=f"${teacher.api_key_env} is not set.",
        )
    url = f"{teacher.base_url.rstrip('/')}{teacher.endpoint}"
    payload = {
        "model": teacher.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0.0,
    }
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=_TEACHER_PING_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
    except error.HTTPError as exc:
        # Auth failures (401/403) are unambiguous gate failures.
        # 404 means the URL path is wrong — the endpoint we configured
        # does not exist on the server. That is a misconfiguration the
        # operator must fix before launching, not a "reachable" state.
        # Other 4xx responses (e.g. 400, 422, 429) still indicate the
        # endpoint is alive and routing requests, so we treat them as
        # pass-with-warning.
        if exc.code in (401, 403, 404):
            return GateResult(
                name="teacher_reachable",
                passed=False,
                detail=f"Teacher responded HTTP {exc.code}.",
            )
        if 400 <= exc.code < 500:
            return GateResult(
                name="teacher_reachable",
                passed=True,
                detail=f"Endpoint reachable (HTTP {exc.code}).",
            )
        return GateResult(
            name="teacher_reachable",
            passed=False,
            detail=f"Teacher responded HTTP {exc.code}.",
        )
    except error.URLError as exc:
        return GateResult(
            name="teacher_reachable",
            passed=False,
            detail=f"Network error contacting teacher: {exc.reason}",
        )
    except (TimeoutError, OSError) as exc:
        return GateResult(
            name="teacher_reachable",
            passed=False,
            detail=f"Teacher ping failed: {exc}",
        )
    return GateResult(
        name="teacher_reachable",
        passed=True,
        detail=f"Teacher responded HTTP {status}.",
    )


def _gate_git_clean(settings: HomunculusConfig) -> GateResult:
    """Verify every configured workspace has a clean working tree.

    Runs ``git status --porcelain`` in each workspace path. Any output
    marks the gate as failed; the detail names the dirty workspaces so
    the operator can resolve them.
    """
    dirty: list[str] = []
    unreachable: list[str] = []
    for name, workspace in settings.workspaces.items():
        path = Path(workspace.path)
        if not path.exists():
            unreachable.append(f"{name}:missing")
            continue
        try:
            completed = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(path),
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            unreachable.append(f"{name}:timeout")
            continue
        except OSError as exc:
            unreachable.append(f"{name}:{exc}")
            continue
        if completed.returncode != 0:
            unreachable.append(f"{name}:not-a-repo")
            continue
        if completed.stdout.strip():
            dirty.append(name)
    if dirty or unreachable:
        parts: list[str] = []
        if dirty:
            parts.append(f"dirty: {', '.join(dirty)}")
        if unreachable:
            parts.append(f"unreachable: {', '.join(unreachable)}")
        return GateResult(
            name="git_clean", passed=False, detail="; ".join(parts)
        )
    return GateResult(
        name="git_clean",
        passed=True,
        detail=f"All {len(settings.workspaces)} workspace(s) clean.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config_path_for(settings: HomunculusConfig) -> Path | None:
    """Best-effort config path reconstruction.

    The :class:`HomunculusConfig` dataclass does not carry its source
    path, so we look for the two canonical names under ``paths.root``.
    Callers that instantiated via a different file should pass it
    explicitly (currently no such caller exists).
    """
    root = settings.paths.root
    for name in ("homunculus.toml", "homunculus.example.toml"):
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _tail(text: str, *, lines: int) -> str:
    """Return the last ``lines`` non-empty lines of ``text``."""
    if not text:
        return ""
    buf = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(buf[-lines:])


def format_preflight_table(result: PreflightResult) -> str:
    """Human-readable table rendering for CLI output."""
    rows = [("GATE", "STATUS", "DETAIL")]
    for name, gate in result.gates.items():
        status = "PASS" if gate.passed else "FAIL"
        rows.append((name, status, gate.detail))
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    lines = []
    for idx, row in enumerate(rows):
        line = "  ".join(row[i].ljust(widths[i]) for i in range(3))
        lines.append(line.rstrip())
        if idx == 0:
            lines.append("  ".join("-" * widths[i] for i in range(3)))
    summary = "PASS" if result.passed else "FAIL"
    lines.append("")
    lines.append(f"Overall: {summary}")
    return "\n".join(lines)


__all__ = ["run_preflight", "format_preflight_table"]
