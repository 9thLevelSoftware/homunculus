"""Runtime construction - shared by cli.py and daemon.py."""
from __future__ import annotations

from .config import load_config
from .dataset_builder.builder import DatasetBuilder
from .memory_client.engram import EngramMemoryClient
from .orchestrator.loop import EpisodeOrchestrator
from .orchestrator.student import LocalStudentRunner
from .orchestrator.teacher import OpenAICompatibleTeacher
from .policy import GuardrailEngine
from .storage import ArtifactStore
from .task_runner.runner import TaskRunner
from .trainer.manager import TrainingManager


def build_runtime(config_path: str):
    """Build all runtime components from config.

    Returns:
        Tuple of (config, store, builder, trainer, orchestrator, task_runner, memory_client)
    """
    config = load_config(config_path)
    store = ArtifactStore(config)
    builder = DatasetBuilder(config, store)
    memory_client = EngramMemoryClient(config.memory)
    teacher = OpenAICompatibleTeacher(config.teacher)
    student = LocalStudentRunner(config.student)
    task_runner = TaskRunner(config.paths.runtime_dir)
    guardrails = GuardrailEngine(config.guardrails)
    trainer = TrainingManager(config, store, builder)
    orchestrator = EpisodeOrchestrator(
        config, store, memory_client, teacher, student,
        task_runner, builder, guardrails
    )
    return config, store, builder, trainer, orchestrator, task_runner, memory_client
