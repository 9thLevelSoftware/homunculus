from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .models import (
    AgentConfig,
    CodexConfig,
    HomunculusSymphonyConfig,
    HooksConfig,
    PollingConfig,
    SymphonyConfig,
    TrackerConfig,
    WorkflowDefinition,
    WorkspaceConfig,
)


class WorkflowError(RuntimeError):
    """Raised when WORKFLOW.md cannot be parsed or rendered safely."""


def load_workflow(path: str | Path = "WORKFLOW.md") -> SymphonyConfig:
    workflow = load_workflow_definition(path)
    return build_config(workflow)


def load_workflow_definition(path: str | Path) -> WorkflowDefinition:
    workflow_path = Path(path).resolve()
    try:
        text = workflow_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"missing_workflow_file: {workflow_path}") from exc
    raw_config, prompt = _split_front_matter(text)
    return WorkflowDefinition(path=workflow_path, config=raw_config, prompt_template=prompt.strip())


def build_config(definition: WorkflowDefinition) -> SymphonyConfig:
    raw = definition.config
    base = definition.path.parent
    tracker_raw = _as_map(raw.get("tracker", {}), "tracker")
    tracker_kind = str(tracker_raw.get("kind", "")).strip()
    if not tracker_kind:
        raise WorkflowError("tracker.kind is required")
    api_key_ref = tracker_raw.get("api_key") or "$LINEAR_API_KEY"
    api_key, api_key_env = _resolve_secret(api_key_ref)
    tracker = TrackerConfig(
        kind=tracker_kind,
        endpoint=str(
            tracker_raw.get("endpoint")
            or ("https://api.linear.app/graphql" if tracker_kind == "linear" else "")
        ),
        api_key=api_key,
        api_key_env=api_key_env,
        project_slug=str(tracker_raw.get("project_slug", "")).strip(),
        active_states=tuple(_string_list(tracker_raw.get("active_states"), ["Todo", "In Progress"])),
        terminal_states=tuple(
            _string_list(
                tracker_raw.get("terminal_states"),
                ["Closed", "Cancelled", "Canceled", "Duplicate", "Done"],
            )
        ),
        label=str(tracker_raw.get("label", "symphony")),
    )

    polling_raw = _as_map(raw.get("polling", {}), "polling")
    workspace_raw = _as_map(raw.get("workspace", {}), "workspace")
    hooks_raw = _as_map(raw.get("hooks", {}), "hooks")
    agent_raw = _as_map(raw.get("agent", {}), "agent")
    codex_raw = _as_map(raw.get("codex", {}), "codex")
    hom_raw = _as_map(raw.get("homunculus", {}), "homunculus")

    source_workspace = _resolve_path(base, hom_raw.get("source_workspace", "."))
    homunculus = HomunculusSymphonyConfig(
        config_path=_resolve_path(base, hom_raw.get("config_path", "homunculus.toml")),
        source_workspace=source_workspace,
        base_branch=str(hom_raw.get("base_branch", "master")),
        branch_prefix=str(hom_raw.get("branch_prefix", "codex/")),
        auto_merge=bool(hom_raw.get("auto_merge", True)),
        artifact_curation=bool(hom_raw.get("artifact_curation", True)),
        runner=str(hom_raw.get("runner", "homunculus")),
        fallback_runner=str(hom_raw.get("fallback_runner", "homunculus")),
        done_state=str(hom_raw.get("done_state", "Done")),
        in_progress_state=str(hom_raw.get("in_progress_state", "In Progress")),
        failed_state=(
            None
            if hom_raw.get("failed_state") in (None, "")
            else str(hom_raw.get("failed_state", "Rework"))
        ),
        merge_gates=tuple(
            _string_list(
                hom_raw.get("merge_gates"),
                [
                    "python -m homunculus.cli harness-check --strict",
                    "python -m unittest discover -q",
                ],
            )
        ),
        verification_workspace=str(hom_raw.get("verification_workspace", "self")),
    )

    return SymphonyConfig(
        workflow_path=definition.path,
        prompt_template=definition.prompt_template,
        raw_config=raw,
        tracker=tracker,
        polling=PollingConfig(interval_ms=_int_value(polling_raw.get("interval_ms"), 30000)),
        workspace=WorkspaceConfig(
            root=_resolve_path(base, workspace_raw.get("root", "/home/homunculus/workspaces"))
        ),
        hooks=HooksConfig(
            after_create=_optional_str(hooks_raw.get("after_create")),
            before_run=_optional_str(hooks_raw.get("before_run")),
            after_run=_optional_str(hooks_raw.get("after_run")),
            before_remove=_optional_str(hooks_raw.get("before_remove")),
            timeout_ms=_int_value(hooks_raw.get("timeout_ms"), 60000),
        ),
        agent=AgentConfig(
            max_concurrent_agents=_positive_int(agent_raw.get("max_concurrent_agents"), 10),
            max_turns=_positive_int(agent_raw.get("max_turns"), 20),
            max_retry_backoff_ms=_positive_int(agent_raw.get("max_retry_backoff_ms"), 300000),
            max_concurrent_agents_by_state=_state_limits(
                agent_raw.get("max_concurrent_agents_by_state")
            ),
        ),
        codex=CodexConfig(
            command=str(codex_raw.get("command", "codex app-server")),
            approval_policy=_optional_str(codex_raw.get("approval_policy")),
            thread_sandbox=_optional_str(codex_raw.get("thread_sandbox")),
            turn_sandbox_policy=codex_raw.get("turn_sandbox_policy"),
            turn_timeout_ms=_positive_int(codex_raw.get("turn_timeout_ms"), 3600000),
            read_timeout_ms=_positive_int(codex_raw.get("read_timeout_ms"), 5000),
            stall_timeout_ms=_int_value(codex_raw.get("stall_timeout_ms"), 300000),
        ),
        homunculus=homunculus,
    )


_VARIABLE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*}}")


def render_prompt(template: str, *, issue: Any, attempt: int | None = None) -> str:
    context = {"issue": _to_plain(issue), "attempt": attempt}

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = _resolve_variable(context, name)
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=True)
        if value is None:
            return ""
        return str(value)

    rendered = _VARIABLE_RE.sub(replace, template)
    if "{{" in rendered or "}}" in rendered:
        raise WorkflowError("template_render_error: unresolved template marker")
    return rendered.strip()


def _resolve_variable(context: dict[str, Any], name: str) -> Any:
    current: Any = context
    for part in name.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise WorkflowError(f"template_render_error: unknown variable {name!r}")
    return current


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    return value


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end_index: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        raise WorkflowError("workflow_parse_error: unterminated YAML front matter")
    front = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :])
    parsed = _parse_yaml(front)
    if not isinstance(parsed, dict):
        raise WorkflowError("workflow_front_matter_not_a_map")
    return parsed, body


def _parse_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_yaml_subset(text)
    try:
        payload = yaml.safe_load(text) or {}
    except Exception as exc:  # pragma: no cover - exercised only with PyYAML
        raise WorkflowError(f"workflow_parse_error: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError("workflow_front_matter_not_a_map")
    return payload


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    source_lines = text.splitlines()
    lines = [
        line.rstrip("\n")
        for line in source_lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return {}
    payload, index = _parse_yaml_block(lines, 0, _indent(lines[0]))
    if index != len(lines):
        raise WorkflowError(f"workflow_parse_error: unexpected line {lines[index]!r}")
    if not isinstance(payload, dict):
        raise WorkflowError("workflow_front_matter_not_a_map")
    return payload


def _parse_yaml_block(lines: list[str], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    stripped = lines[index].strip()
    if stripped.startswith("- "):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_map(lines, index, indent)


def _parse_yaml_map(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent > indent:
            raise WorkflowError(f"workflow_parse_error: unexpected indentation: {line!r}")
        stripped = line.strip()
        if stripped.startswith("- "):
            break
        if ":" not in stripped:
            raise WorkflowError(f"workflow_parse_error: expected key: value, got {line!r}")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise WorkflowError(f"workflow_parse_error: empty key in {line!r}")
        if raw_value == "|":
            block, index = _collect_literal_block(lines, index + 1, current_indent)
            result[key] = block
            continue
        if raw_value == "":
            if index + 1 >= len(lines) or _indent(lines[index + 1]) <= current_indent:
                result[key] = {}
                index += 1
            else:
                result[key], index = _parse_yaml_block(lines, index + 1, _indent(lines[index + 1]))
            continue
        result[key] = _parse_scalar(raw_value)
        index += 1
    return result, index


def _parse_yaml_list(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line = lines[index]
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent != indent:
            raise WorkflowError(f"workflow_parse_error: unexpected list indentation: {line!r}")
        stripped = line.strip()
        if not stripped.startswith("- "):
            break
        raw_value = stripped[2:].strip()
        if raw_value == "":
            if index + 1 >= len(lines):
                result.append(None)
                index += 1
            else:
                value, index = _parse_yaml_block(lines, index + 1, _indent(lines[index + 1]))
                result.append(value)
            continue
        if ":" in raw_value and not raw_value.startswith(("'", '"')):
            key, value = raw_value.split(":", 1)
            item: dict[str, Any] = {key.strip(): _parse_scalar(value.strip())}
            index += 1
            result.append(item)
            continue
        result.append(_parse_scalar(raw_value))
        index += 1
    return result, index


def _collect_literal_block(lines: list[str], index: int, parent_indent: int) -> tuple[str, int]:
    collected: list[str] = []
    min_indent: int | None = None
    while index < len(lines):
        line = lines[index]
        current_indent = _indent(line)
        if current_indent <= parent_indent:
            break
        min_indent = current_indent if min_indent is None else min(min_indent, current_indent)
        collected.append(line)
        index += 1
    if min_indent is None:
        return "", index
    return "\n".join(line[min_indent:] for line in collected).rstrip(), index


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value in {"", "null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        return value


def _as_map(value: Any, section: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkflowError(f"{section} must be a map")
    return value


def _string_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return default
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    raise WorkflowError(f"expected list of strings, got {type(value).__name__}")


def _resolve_secret(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    text = str(value)
    if text.startswith("$") and len(text) > 1:
        env_name = text[1:]
        return os.environ.get(env_name) or None, env_name
    return text, None


def _resolve_path(base: Path, value: Any) -> Path:
    raw = os.path.expandvars(os.path.expanduser(str(value)))
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()


def _int_value(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"expected integer, got {value!r}") from exc


def _positive_int(value: Any, default: int) -> int:
    observed = _int_value(value, default)
    if observed <= 0:
        raise WorkflowError(f"expected positive integer, got {observed}")
    return observed


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _state_limits(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkflowError("agent.max_concurrent_agents_by_state must be a map")
    limits: dict[str, int] = {}
    for key, raw in value.items():
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            limits[str(key).lower()] = parsed
    return limits
