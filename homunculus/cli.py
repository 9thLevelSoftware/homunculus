from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from .autonomy import generate_report
from .autonomy.acceptance import render_acceptance_markdown, validate_acceptance
from .autonomy.precheck import format_precheck_table, run_precheck
from .autonomy.preflight import format_preflight_table, run_preflight
from .config import load_config
from .harness import format_harness_report, run_harness_check
from .models import EvaluationMetrics, TaskRequest
from .runtime import build_runtime
from .task_runner.runner import WorkspacePreflightError


def cmd_init_artifacts(args: argparse.Namespace) -> int:
    config, store, _, _, _, _, _ = build_runtime(args.config)
    store.ensure_layout()
    print(json.dumps({"status": "ok", "root": str(config.paths.root)}))
    return 0


def cmd_harness_check(args: argparse.Namespace) -> int:
    root = Path(args.root)
    report = run_harness_check(root, strict=args.strict)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_harness_report(report))
    return 0 if report.ok else 1


def cmd_run_episode(args: argparse.Namespace) -> int:
    _, _, _, _, orchestrator, _, _ = build_runtime(args.config)
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    episode = orchestrator.run_episode(TaskRequest(task_id=args.task_id, workspace=args.workspace, prompt=prompt))
    print(json.dumps(episode.to_dict(), indent=2))
    return 0


def cmd_apply_episode(args: argparse.Namespace) -> int:
    config, store, _, _, _, task_runner, _ = build_runtime(args.config)
    episode = store.get_episode(args.episode_id)
    if not episode:
        raise SystemExit(f"Unknown episode: {args.episode_id}")
    workspace = config.workspaces[episode.workspace]
    patch = store.read_patch_artifact(args.episode_id)
    result = task_runner.apply_episode_patch(workspace, patch)
    print(json.dumps({
        "episode_id": args.episode_id,
        "workspace_path": result.workspace_path,
        "diff_hash": result.diff_hash,
        "verification_results": [item.__dict__ for item in result.verification_results],
    }, indent=2))
    return 0


def cmd_train_sft(args: argparse.Namespace) -> int:
    _, store, _, trainer, _, _, _ = build_runtime(args.config)
    store.ensure_layout()
    manifest = trainer.run_sft(simulate=args.simulate)
    print(json.dumps(manifest.to_dict(), indent=2))
    return 0


def cmd_evaluate_candidate(args: argparse.Namespace) -> int:
    _, store, _, trainer, _, _, _ = build_runtime(args.config)
    candidate = store.get_candidate(args.candidate_id)
    if not candidate:
        raise SystemExit(f"Unknown candidate: {args.candidate_id}")
    metrics = EvaluationMetrics.from_dict(json.loads(Path(args.metrics_file).read_text(encoding="utf-8")))
    candidate = trainer.evaluate_candidate(candidate, metrics)
    print(json.dumps(candidate.to_dict(), indent=2))
    return 0


def cmd_promote_candidate(args: argparse.Namespace) -> int:
    _, store, _, trainer, _, _, _ = build_runtime(args.config)
    candidate = store.get_candidate(args.candidate_id)
    if not candidate:
        raise SystemExit(f"Unknown candidate: {args.candidate_id}")
    candidate = trainer.promote_candidate(candidate)
    print(json.dumps(candidate.to_dict(), indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config, store, _, _, _, task_runner, memory_client = build_runtime(args.config)
    store.ensure_layout()
    checks: list[dict[str, str | bool]] = []

    checks.append({"name": "git", "ok": shutil.which("git") is not None})
    checks.append({"name": "teacher_auth_env", "ok": bool(os.environ.get(config.teacher.api_key_env))})
    checks.append({"name": "engram_auth_env", "ok": bool(os.environ.get(config.memory.bearer_token_env))})
    checks.append({"name": "mlx_lm", "ok": importlib.util.find_spec("mlx_lm") is not None})

    for path in [
        config.paths.traces_dir,
        config.paths.datasets_dir,
        config.paths.models_dir,
        config.paths.runtime_dir,
    ]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doctor-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append({"name": f"writable:{path.name}", "ok": True})
        except OSError:
            checks.append({"name": f"writable:{path.name}", "ok": False})

    for name, workspace in config.workspaces.items():
        try:
            task_runner.require_clean_workspace(workspace)
            checks.append({"name": f"workspace:{name}", "ok": True})
        except WorkspacePreflightError:
            checks.append({"name": f"workspace:{name}", "ok": False})
        except Exception:
            checks.append({"name": f"workspace:{name}", "ok": False})

    try:
        memory_client.get_active_context("doctor", limit=1)
        checks.append({"name": "engram_reachable", "ok": True})
    except Exception:
        checks.append({"name": "engram_reachable", "ok": False})

    failed = [item["name"] for item in checks if not item["ok"]]
    print(json.dumps({"checks": checks, "ok": not failed, "failed": failed}, indent=2))
    return 0 if not failed else 1


_DAY_PATTERN = re.compile(r"^DAY-(\d+)$", re.IGNORECASE)


def _parse_since(value: str | None) -> datetime | None:
    """Parse ``--since`` flag accepting ``DAY-N`` or ISO-8601 timestamp."""
    if not value:
        return None
    match = _DAY_PATTERN.match(value.strip())
    if match:
        days = int(match.group(1))
        return datetime.now(timezone.utc) - timedelta(days=days)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid --since value {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def cmd_autonomy_precheck(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = run_precheck(
        config,
        lookback_days=args.lookback_days,
        soak_days=args.soak_days,
        threshold_min=args.threshold_min,
        safety_margin=args.safety_margin,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_precheck_table(result))
    return 0 if result.verdict == "PASS" else 2


def cmd_autonomy_preflight(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = run_preflight(config)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_preflight_table(result))
    return 0 if result.passed else 1


def cmd_autonomy_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    since = _parse_since(args.since)
    report = generate_report(
        runtime_dir=config.paths.runtime_dir,
        traces_dir=config.paths.traces_dir,
        models_dir=config.paths.models_dir,
        since=since,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_report_table(report)
    return 0


def _print_report_table(report) -> None:
    rows: list[tuple[str, str]] = [
        ("generated_at", report.generated_at.isoformat()),
        ("uptime_days", f"{report.uptime.total_seconds() / 86400.0:.2f}"),
        ("cycles_completed", str(report.cycles_completed)),
        ("episodes_total", str(report.episodes_total)),
        ("episodes_success", str(report.episodes_success)),
        ("episodes_failed", str(report.episodes_failed)),
        ("self_directed_tasks_completed", str(report.self_directed_tasks_completed)),
        ("suggestion_tasks_completed", str(report.suggestion_tasks_completed)),
        ("loras_trained", str(report.loras_trained)),
        ("loras_merged", str(report.loras_merged)),
        ("current_base_generation", str(report.current_base_generation)),
        ("patch_success_rate", f"{report.patch_success_rate:.3f}"),
        (
            "patch_success_rate_trend",
            "n/a" if report.patch_success_rate_trend is None
            else f"{report.patch_success_rate_trend:+.3f}",
        ),
        (
            "coverage_percent",
            "n/a" if report.coverage_percent is None
            else f"{report.coverage_percent:.2f}",
        ),
        (
            "coverage_trend",
            "n/a" if report.coverage_trend is None
            else f"{report.coverage_trend:+.3f}",
        ),
        (
            "watchdog_flags",
            ", ".join(report.watchdog_flags) if report.watchdog_flags else "(none)",
        ),
    ]
    width = max(len(k) for k, _ in rows)
    for key, val in rows:
        print(f"{key.ljust(width)}  {val}")


def cmd_autonomy_accept(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report = generate_report(
        runtime_dir=config.paths.runtime_dir,
        traces_dir=config.paths.traces_dir,
        models_dir=config.paths.models_dir,
    )
    # Use the first workspace's path as the repo to inspect for SC6.
    # Multi-workspace acceptance is out of scope; soak runs against a
    # single branch.
    first_workspace = next(iter(config.workspaces.values()))
    verdict = validate_acceptance(
        report,
        soak_branch=args.soak_branch,
        workspace_root=Path(first_workspace.path),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_acceptance_markdown(
            verdict, report=report, soak_branch=args.soak_branch
        ),
        encoding="utf-8",
    )
    print(json.dumps(verdict.to_dict(), indent=2))
    return 0 if verdict.overall == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="homunculus")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-artifacts")
    init_parser.add_argument("--config", required=True)
    init_parser.set_defaults(func=cmd_init_artifacts)

    harness_parser = subparsers.add_parser(
        "harness-check",
        help="Validate repository-local agent harness docs, config, and CI.",
    )
    harness_parser.add_argument("--root", default=".")
    harness_parser.add_argument("--strict", action="store_true")
    harness_parser.add_argument("--json", action="store_true")
    harness_parser.set_defaults(func=cmd_harness_check)

    run_parser = subparsers.add_parser("run-episode")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--workspace", required=True)
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--prompt", default="")
    run_parser.add_argument("--prompt-file")
    run_parser.set_defaults(func=cmd_run_episode)

    apply_parser = subparsers.add_parser("apply-episode")
    apply_parser.add_argument("--config", required=True)
    apply_parser.add_argument("--episode-id", required=True)
    apply_parser.set_defaults(func=cmd_apply_episode)

    train_parser = subparsers.add_parser("train-sft")
    train_parser.add_argument("--config", required=True)
    train_parser.add_argument("--simulate", action="store_true")
    train_parser.set_defaults(func=cmd_train_sft)

    eval_parser = subparsers.add_parser("evaluate-candidate")
    eval_parser.add_argument("--config", required=True)
    eval_parser.add_argument("--candidate-id", required=True)
    eval_parser.add_argument("--metrics-file", required=True)
    eval_parser.set_defaults(func=cmd_evaluate_candidate)

    promote_parser = subparsers.add_parser("promote-candidate")
    promote_parser.add_argument("--config", required=True)
    promote_parser.add_argument("--candidate-id", required=True)
    promote_parser.set_defaults(func=cmd_promote_candidate)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config", required=True)
    doctor_parser.set_defaults(func=cmd_doctor)

    precheck_parser = subparsers.add_parser(
        "autonomy-precheck",
        help="SOAK-PROTOCOL §2.2 throughput gate — projects LoRA merges over soak window.",
    )
    precheck_parser.add_argument("--config", required=True)
    precheck_parser.add_argument("--json", action="store_true")
    precheck_parser.add_argument(
        "--lookback-days", type=int, default=14,
        help="Historical window for episode rate calculation (default: 14).",
    )
    precheck_parser.add_argument(
        "--soak-days", type=int, default=7,
        help="Intended soak duration in days (default: 7).",
    )
    precheck_parser.add_argument(
        "--threshold-min", type=float, default=1.0,
        help="Minimum projected merges for PASS verdict (default: 1.0).",
    )
    precheck_parser.add_argument(
        "--safety-margin", type=float, default=1.5,
        help="Projection floor for 'OK' margin annotation (default: 1.5).",
    )
    precheck_parser.set_defaults(func=cmd_autonomy_precheck)

    preflight_parser = subparsers.add_parser("autonomy-preflight")
    preflight_parser.add_argument("--config", required=True)
    preflight_parser.add_argument("--json", action="store_true")
    preflight_parser.set_defaults(func=cmd_autonomy_preflight)

    report_parser = subparsers.add_parser("autonomy-report")
    report_parser.add_argument("--config", required=True)
    report_parser.add_argument("--json", action="store_true")
    report_parser.add_argument(
        "--since",
        default=None,
        help="Filter episodes to those at or after this time. "
             "Accepts DAY-N (e.g. DAY-7) or an ISO-8601 timestamp.",
    )
    report_parser.set_defaults(func=cmd_autonomy_report)

    accept_parser = subparsers.add_parser("autonomy-accept")
    accept_parser.add_argument("--config", required=True)
    accept_parser.add_argument(
        "--soak-log",
        default=None,
        help="Path to soak-log directory (reserved; report is regenerated live).",
    )
    accept_parser.add_argument("--output", required=True)
    accept_parser.add_argument("--soak-branch", required=True)
    accept_parser.set_defaults(func=cmd_autonomy_accept)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
