from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_symphony_status(runtime_dir: str | Path = "runtime", *, limit: int = 20) -> dict[str, Any]:
    root = Path(runtime_dir)
    state_path = root / "symphony_state.json"
    runs_path = root / "symphony_runs.jsonl"
    state = _read_json(state_path) or {}
    runs = _read_jsonl(runs_path)[-limit:]
    return {
        "runtime_dir": str(root.resolve()),
        "state": state,
        "recent_runs": runs,
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows
