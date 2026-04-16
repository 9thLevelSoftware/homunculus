from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from ..config import HomunculusConfig
from ..models import DatasetSnapshot, EpisodeRecord, PreferencePair, SFTSample, utc_now
from ..storage import ArtifactStore


class DatasetBuilder:
    def __init__(self, config: HomunculusConfig, store: ArtifactStore) -> None:
        self.config = config
        self.store = store

    def ingest_episode(self, episode: EpisodeRecord) -> dict[str, int]:
        result = {"sft_added": 0, "dpo_added": 0}
        sample = self.build_sft_sample(episode)
        if sample:
            split = self.store.split_for_text(f"{episode.prompt}:{episode.diff_hash}")
            if self._append_unique_sft(split, sample, episode.diff_hash):
                result["sft_added"] = 1

        pair = self.build_preference_pair(episode)
        if pair:
            split = self.store.split_for_text(pair.prompt)
            if split == "test":
                split = "valid"
            if self._append_unique_dpo(split, pair):
                result["dpo_added"] = 1
        return result

    def build_sft_sample(self, episode: EpisodeRecord) -> SFTSample | None:
        if episode.outcome != "accepted":
            return None
        if not episode.verification_passed:
            return None
        if episode.review_status != "approved":
            return None
        assistant_payload = {
            "plan": episode.plan,
            "patch": episode.patch,
            "rationale": episode.teacher_output.get("rationale"),
        }
        return SFTSample(
            messages=[
                {"role": "system", "content": "You are a careful coding assistant. Produce safe, testable patches."},
                {"role": "user", "content": episode.prompt},
                {"role": "assistant", "content": json.dumps(assistant_payload, sort_keys=True)},
            ],
            episode_id=episode.episode_id,
            source=episode.source,
            verification={
                "diff_hash": episode.diff_hash,
                "verification_passed": episode.verification_passed,
                "review_status": episode.review_status,
            },
        )

    def build_preference_pair(self, accepted_episode: EpisodeRecord) -> PreferencePair | None:
        if accepted_episode.outcome != "accepted" or not accepted_episode.verification_passed:
            return None
        episodes = self.store.load_episodes()
        candidates = [
            item
            for item in episodes
            if item.task_id == accepted_episode.task_id
            and item.prompt == accepted_episode.prompt
            and item.outcome != "accepted"
            and item.patch
        ]
        if accepted_episode.comparison_group:
            candidates = [item for item in candidates if item.comparison_group == accepted_episode.comparison_group]
        if not candidates:
            return None
        loser = candidates[-1]
        if loser.patch == accepted_episode.patch:
            return None
        return PreferencePair(
            prompt=accepted_episode.prompt,
            chosen=accepted_episode.patch or "",
            rejected=loser.patch or "",
            episode_ids=[accepted_episode.episode_id, loser.episode_id],
            verification={
                "winner_diff_hash": accepted_episode.diff_hash,
                "loser_diff_hash": loser.diff_hash,
            },
            source=accepted_episode.source,
        )

    def can_build_training_snapshot(self) -> bool:
        try:
            snapshot = self.preview_sft_snapshot()
        except RuntimeError:
            return False
        return snapshot.sample_counts["splits"]["train"] > 0 and snapshot.sample_counts["splits"]["valid"] > 0 and snapshot.sample_counts["splits"]["test"] > 0

    def compose_training_splits(self) -> tuple[list[dict], list[dict], list[dict]]:
        snapshot = self.preview_sft_snapshot()
        snapshot_dir = Path(snapshot.snapshot_path)
        train_payloads = self._load_jsonl(snapshot_dir / "train.jsonl") if snapshot_dir.exists() else []
        valid_payloads = self._load_jsonl(snapshot_dir / "valid.jsonl") if snapshot_dir.exists() else []
        test_payloads = self._load_jsonl(snapshot_dir / "test.jsonl") if snapshot_dir.exists() else []
        if train_payloads or valid_payloads or test_payloads:
            return train_payloads, valid_payloads, test_payloads
        train_payloads, valid_payloads, test_payloads, _ = self._compose_snapshot_payloads()
        return train_payloads, valid_payloads, test_payloads

    def materialize_sft_snapshot(self) -> DatasetSnapshot:
        train_payloads, valid_payloads, test_payloads, metadata = self._compose_snapshot_payloads()
        snapshot = self._build_snapshot(metadata)
        self.store.write_snapshot(snapshot, train_payloads, valid_payloads, test_payloads)
        return snapshot

    def preview_sft_snapshot(self) -> DatasetSnapshot:
        _, _, _, metadata = self._compose_snapshot_payloads()
        return self._build_snapshot(metadata)

    def snapshot_id(self) -> str:
        return self.preview_sft_snapshot().snapshot_id

    def _compose_snapshot_payloads(self) -> tuple[list[dict], list[dict], list[dict], dict]:
        seed_payloads = self._load_seed_payloads(self.config.paths.seed_sft_path)
        self_train = [item.to_dict() for item in self.store.load_sft_samples("train")]
        self_valid = [item.to_dict() for item in self.store.load_sft_samples("valid")]
        self_test = [item.to_dict() for item in self.store.load_sft_samples("test")]
        if not self_valid or not self_test:
            raise RuntimeError("Training snapshot requires non-empty valid and test splits.")
        allowed_self = self._allowed_self_generated_count(len(seed_payloads))
        selected_self_train = self_train[-allowed_self:] if allowed_self else []
        train_payloads = seed_payloads + selected_self_train
        if not selected_self_train:
            raise RuntimeError("Training snapshot requires at least one approved self-generated train sample.")
        self_ratio = len(selected_self_train) / len(train_payloads) if train_payloads else 0.0
        if self_ratio > self.config.thresholds.max_self_generated_ratio:
            raise RuntimeError("Training snapshot violates the configured self-generated ratio.")
        metadata = {
            "splits": {
                "train": len(train_payloads),
                "valid": len(self_valid),
                "test": len(self_test),
            },
            "seed_count": len(seed_payloads),
            "self_count": len(selected_self_train),
            "selected_episode_ids": {
                "train": [payload["episode_id"] for payload in selected_self_train],
                "valid": [payload["episode_id"] for payload in self_valid],
                "test": [payload["episode_id"] for payload in self_test],
            },
            "self_generated_ratio": self_ratio,
        }
        return train_payloads, self_valid, self_test, metadata

    def _build_snapshot(self, metadata: dict) -> DatasetSnapshot:
        train_payloads, valid_payloads, test_payloads, _ = self._compose_snapshot_payloads()
        combined = [{"split": "train", **item} for item in train_payloads]
        combined += [{"split": "valid", **item} for item in valid_payloads]
        combined += [{"split": "test", **item} for item in test_payloads]
        snapshot_id = self.store.snapshot_id(combined)
        snapshot_path = str(self.store.snapshot_root("sft") / snapshot_id)
        config_hash = sha256(self.config.source_path.read_bytes()).hexdigest()
        return DatasetSnapshot(
            snapshot_id=snapshot_id,
            snapshot_path=snapshot_path,
            sample_counts={
                "splits": metadata["splits"],
                "seed_count": metadata["seed_count"],
                "self_count": metadata["self_count"],
            },
            selected_episode_ids=metadata["selected_episode_ids"],
            self_generated_ratio=metadata["self_generated_ratio"],
            config_hash=config_hash,
            created_at=utc_now(),
        )

    def _append_unique_sft(self, split: str, sample: SFTSample, diff_hash: str) -> bool:
        existing = self.store.load_jsonl(self.store.datasets_dir / "sft" / f"{split}.jsonl")
        key = self._sample_key(sample.messages[1]["content"], diff_hash)
        for item in existing:
            if self._sample_key(item["messages"][1]["content"], item["verification"]["diff_hash"]) == key:
                return False
        self.store.append_sft_sample(split, sample)
        return True

    def _append_unique_dpo(self, split: str, pair: PreferencePair) -> bool:
        existing = self.store.load_jsonl(self.store.datasets_dir / "dpo" / f"{split}.jsonl")
        key = sha256(f"{pair.prompt}:{pair.chosen}:{pair.rejected}".encode("utf-8")).hexdigest()
        for item in existing:
            other = sha256(f"{item['prompt']}:{item['chosen']}:{item['rejected']}".encode("utf-8")).hexdigest()
            if other == key:
                return False
        self.store.append_dpo_pair(split, pair)
        return True

    def _allowed_self_generated_count(self, seed_count: int) -> int:
        ratio = self.config.thresholds.max_self_generated_ratio
        if ratio <= 0 or seed_count == 0:
            return 0
        return int(seed_count * ratio / (1.0 - ratio))

    def _load_seed_payloads(self, path: Path) -> list[dict]:
        return self._load_jsonl(path)

    def _load_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def _sample_key(self, prompt: str, diff_hash: str) -> str:
        return sha256(f"{prompt}:{diff_hash}".encode("utf-8")).hexdigest()
