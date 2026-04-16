from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil

from .config import load_config
from .dataset_builder.builder import DatasetBuilder
from .memory_client.engram import EngramMemoryClient
from .models import EvaluationMetrics, TaskRequest
from .orchestrator.loop import EpisodeOrchestrator
from .orchestrator.student import LocalStudentRunner
from .orchestrator.teacher import OpenAICompatibleTeacher
from .policy import GuardrailEngine
from .storage import ArtifactStore
from .task_runner.runner import TaskRunner, WorkspacePreflightError
from .trainer.manager import TrainingManager


def build_runtime(config_path: str):
    config = load_config(config_path)
    store = ArtifactStore(config)
    builder = DatasetBuilder(config, store)
    memory_client = EngramMemoryClient(config.memory)
    teacher = OpenAICompatibleTeacher(config.teacher)
    student = LocalStudentRunner(config.student)
    task_runner = TaskRunner(config.paths.runtime_dir)
    guardrails = GuardrailEngine(config.guardrails)
    trainer = TrainingManager(config, store, builder)
    orchestrator = EpisodeOrchestrator(config, store, memory_client, teacher, student, task_runner, builder, guardrails)
    return config, store, builder, trainer, orchestrator, task_runner, memory_client


def cmd_init_artifacts(args: argparse.Namespace) -> int:
    config, store, _, _, _, _, _ = build_runtime(args.config)
    store.ensure_layout()
    print(json.dumps({"status": "ok", "root": str(config.paths.root)}))
    return 0


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
    candidate = trainer.promote_candidate(candidate, human_approved=args.human_approved)
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


def main() -> int:
    parser = argparse.ArgumentParser(prog="homunculus")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-artifacts")
    init_parser.add_argument("--config", required=True)
    init_parser.set_defaults(func=cmd_init_artifacts)

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
    promote_parser.add_argument("--human-approved", action="store_true")
    promote_parser.set_defaults(func=cmd_promote_candidate)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config", required=True)
    doctor_parser.set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
