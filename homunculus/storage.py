from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from .config import HomunculusConfig
from .models import AdapterManifest, DatasetSnapshot, EpisodeRecord, PreferencePair, SFTSample


class ArtifactStore:
    def __init__(self, config: HomunculusConfig) -> None:
        self.config = config
        self.traces_dir = config.paths.traces_dir
        self.datasets_dir = config.paths.datasets_dir
        self.models_dir = config.paths.models_dir
        self.runtime_dir = config.paths.runtime_dir

    def ensure_layout(self) -> None:
        for path in [
            self.traces_dir,
            self.traces_dir / "patches",
            self.datasets_dir / "sft",
            self.datasets_dir / "dpo",
            self.datasets_dir / "seed",
            self.datasets_dir / "snapshots" / "sft",
            self.models_dir,
            self.models_dir / "adapters",
            self.runtime_dir,
            self.runtime_dir / "worktrees",
        ]:
            path.mkdir(parents=True, exist_ok=True)
        for path in [
            self.traces_dir / "events.jsonl",
            self.traces_dir / "episodes.jsonl",
            self.datasets_dir / "sft" / "train.jsonl",
            self.datasets_dir / "sft" / "valid.jsonl",
            self.datasets_dir / "sft" / "test.jsonl",
            self.datasets_dir / "dpo" / "train.jsonl",
            self.datasets_dir / "dpo" / "valid.jsonl",
            self.config.paths.seed_sft_path,
            self.config.paths.seed_dpo_path,
        ]:
            self._ensure_file(path)
        registry = self.models_dir / "registry.json"
        if not registry.exists():
            registry.write_text(
                json.dumps({"active_candidate_id": None, "candidates": [], "history": []}, indent=2),
                encoding="utf-8",
            )

    def _ensure_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")

    def append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.append_jsonl(self.traces_dir / "events.jsonl", {"type": event_type, **payload})

    def append_episode(self, episode: EpisodeRecord) -> None:
        self.append_jsonl(self.traces_dir / "episodes.jsonl", episode.to_dict())

    def load_episodes(self) -> list[EpisodeRecord]:
        return [EpisodeRecord.from_dict(item) for item in self.load_jsonl(self.traces_dir / "episodes.jsonl")]

    def get_episode(self, episode_id: str) -> EpisodeRecord | None:
        for item in self.load_episodes():
            if item.episode_id == episode_id:
                return item
        return None

    def append_sft_sample(self, split: str, sample: SFTSample) -> None:
        self.append_jsonl(self.datasets_dir / "sft" / f"{split}.jsonl", sample.to_dict())

    def load_sft_samples(self, split: str) -> list[SFTSample]:
        return [SFTSample.from_dict(item) for item in self.load_jsonl(self.datasets_dir / "sft" / f"{split}.jsonl")]

    def append_dpo_pair(self, split: str, pair: PreferencePair) -> None:
        self.append_jsonl(self.datasets_dir / "dpo" / f"{split}.jsonl", pair.to_dict())

    def patch_path(self, episode_id: str) -> Path:
        return self.traces_dir / "patches" / f"{episode_id}.patch"

    def write_patch_artifact(self, episode_id: str, patch: str | None) -> Path:
        path = self.patch_path(episode_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((patch or ""), encoding="utf-8")
        return path

    def read_patch_artifact(self, episode_id: str) -> str:
        return self.patch_path(episode_id).read_text(encoding="utf-8")

    def snapshot_root(self, kind: str = "sft") -> Path:
        return self.datasets_dir / "snapshots" / kind

    def write_snapshot(self, snapshot: DatasetSnapshot, train_payloads: list[dict], valid_payloads: list[dict], test_payloads: list[dict]) -> Path:
        snapshot_dir = Path(snapshot.snapshot_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for name, payloads in (("train", train_payloads), ("valid", valid_payloads), ("test", test_payloads)):
            target = snapshot_dir / f"{name}.jsonl"
            with target.open("w", encoding="utf-8") as handle:
                for payload in payloads:
                    handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        (snapshot_dir / "snapshot.json").write_text(json.dumps({
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_path": snapshot.snapshot_path,
            "sample_counts": snapshot.sample_counts,
            "selected_episode_ids": snapshot.selected_episode_ids,
            "self_generated_ratio": snapshot.self_generated_ratio,
            "config_hash": snapshot.config_hash,
            "created_at": snapshot.created_at,
        }, indent=2), encoding="utf-8")
        return snapshot_dir

    def load_registry(self) -> dict[str, Any]:
        registry_path = self.models_dir / "registry.json"
        if not registry_path.exists():
            return {"active_candidate_id": None, "candidates": [], "history": []}
        return json.loads(registry_path.read_text(encoding="utf-8"))

    def save_registry(self, registry: dict[str, Any]) -> None:
        (self.models_dir / "registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")

    def register_candidate(self, manifest: AdapterManifest) -> None:
        registry = self.load_registry()
        registry["candidates"] = [item for item in registry.get("candidates", []) if item.get("candidate_id") != manifest.candidate_id]
        registry.setdefault("candidates", []).append(manifest.to_dict())
        self.save_registry(registry)

    def update_candidate(self, manifest: AdapterManifest) -> None:
        self.register_candidate(manifest)

    def get_candidate(self, candidate_id: str) -> AdapterManifest | None:
        registry = self.load_registry()
        for item in registry.get("candidates", []):
            if item.get("candidate_id") == candidate_id:
                return AdapterManifest.from_dict(item)
        return None

    def set_active_candidate(self, candidate: AdapterManifest) -> None:
        registry = self.load_registry()
        previous = registry.get("active_candidate_id")
        if previous and previous != candidate.candidate_id:
            registry.setdefault("history", []).append(previous)
        registry["active_candidate_id"] = candidate.candidate_id
        self.save_registry(registry)

    def active_candidate(self) -> AdapterManifest | None:
        registry = self.load_registry()
        candidate_id = registry.get("active_candidate_id")
        if not candidate_id:
            return None
        return self.get_candidate(candidate_id)

    def split_for_text(self, text: str) -> str:
        bucket = int(sha256(text.encode("utf-8")).hexdigest(), 16) % 10
        if bucket == 0:
            return "test"
        if bucket == 1:
            return "valid"
        return "train"

    def snapshot_id(self, payloads: Iterable[dict[str, Any]]) -> str:
        digest = sha256()
        for item in payloads:
            digest.update(json.dumps(item, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()
