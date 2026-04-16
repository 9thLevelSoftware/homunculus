from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import HomunculusConfig
from ..models import AdapterManifest, LineageRecord, MergeManifest, utc_now
from ..storage import ArtifactStore


class LineageTracker:
    """Tracks the genealogy of model evolution."""

    def __init__(self, config: HomunculusConfig, store: ArtifactStore) -> None:
        self.config = config
        self.store = store
        self._cache: dict[str, LineageRecord] | None = None

    def _invalidate_cache(self) -> None:
        """Clear the in-memory cache."""
        self._cache = None

    def _load_cache(self) -> dict[str, LineageRecord]:
        """Load all lineage records into a lookup dict."""
        if self._cache is None:
            records = self.store.load_lineage()
            self._cache = {r.record_id: r for r in records}
        return self._cache

    def get_record(self, record_id: str) -> LineageRecord | None:
        """Get a lineage record by ID."""
        cache = self._load_cache()
        return cache.get(record_id)

    def get_current_generation(self) -> int:
        """Get the highest generation number in the lineage."""
        records = self.store.load_lineage()
        if not records:
            return 0
        return max(r.generation for r in records)

    def register_base_model(self, model_id: str, metadata: dict[str, Any] | None = None) -> LineageRecord:
        """Register a base model as generation 0 in the lineage.

        Called once when first initializing the evolution system,
        or when explicitly adding a new base model to track.
        """
        record = LineageRecord(
            record_id=f"base-{model_id}",
            record_type="base",
            model_id=model_id,
            parent_ids=[],
            episode_ids=[],
            generation=0,
            metadata=metadata or {},
        )

        self.store.append_lineage(record)
        self._invalidate_cache()
        return record

    def ensure_base_registered(self, model_id: str) -> LineageRecord:
        """Ensure a base model is registered, creating if needed."""
        record_id = f"base-{model_id}"
        existing = self.get_record(record_id)
        if existing:
            return existing
        return self.register_base_model(model_id)

    def register_lora(
        self,
        candidate: AdapterManifest,
        episode_ids: list[str] | None = None,
    ) -> LineageRecord:
        """Register a trained LoRA adapter in the lineage.

        Args:
            candidate: The adapter manifest from training
            episode_ids: Episodes that contributed training data

        Returns:
            The created lineage record
        """
        # Ensure base model is registered
        base_record = self.ensure_base_registered(candidate.base_model)

        record = LineageRecord(
            record_id=candidate.candidate_id,
            record_type="lora",
            model_id=candidate.model_id,
            parent_ids=[base_record.record_id],
            episode_ids=episode_ids or [],
            generation=base_record.generation,  # LoRAs are same generation as their base
            metadata={
                "adapter_path": candidate.adapter_path,
                "dataset_snapshot": candidate.dataset_snapshot,
                "trainer": candidate.trainer,
            },
        )

        self.store.append_lineage(record)
        self._invalidate_cache()
        return record

    def register_merge(
        self,
        merge_manifest: MergeManifest,
        output_model_id: str,
    ) -> LineageRecord:
        """Register a merge operation in the lineage.

        Creates a new lineage record for the merged model with:
        - All source LoRAs as parents
        - All episodes from source LoRAs aggregated
        - Generation incremented from max parent generation

        Args:
            merge_manifest: The merge operation details
            output_model_id: Model identifier for the merged result

        Returns:
            The created lineage record for the merged model
        """
        cache = self._load_cache()

        # Collect parent information
        parent_ids = []
        all_episode_ids: set[str] = set()
        max_generation = 0

        for lora_id in merge_manifest.source_loras:
            if lora_id in cache:
                parent = cache[lora_id]
                parent_ids.append(parent.record_id)
                all_episode_ids.update(parent.episode_ids)
                max_generation = max(max_generation, parent.generation)

        # Also include the base model as a parent
        # Get base from first LoRA's parent
        for lora_id in merge_manifest.source_loras:
            if lora_id in cache:
                lora_record = cache[lora_id]
                for pid in lora_record.parent_ids:
                    if pid not in parent_ids:
                        parent_ids.append(pid)
                break

        record = LineageRecord(
            record_id=merge_manifest.merge_id,
            record_type="merged",
            model_id=output_model_id,
            parent_ids=parent_ids,
            episode_ids=sorted(all_episode_ids),
            merge_id=merge_manifest.merge_id,
            generation=max_generation + 1,  # Increment generation
            metadata={
                "merge_method": merge_manifest.merge_method,
                "output_path": merge_manifest.output_path,
                "source_count": len(merge_manifest.source_loras),
            },
        )

        self.store.append_lineage(record)
        self._invalidate_cache()
        return record

    def get_ancestors(self, record_id: str, max_depth: int | None = None) -> list[LineageRecord]:
        """Get all ancestors of a record (parents, grandparents, etc.).

        Args:
            record_id: The record to find ancestors for
            max_depth: Maximum depth to traverse (None = unlimited)

        Returns:
            List of ancestor records, ordered from nearest to farthest
        """
        cache = self._load_cache()
        record = cache.get(record_id)
        if not record:
            return []

        ancestors: list[LineageRecord] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(pid, 1) for pid in record.parent_ids]

        while queue:
            pid, depth = queue.pop(0)
            if pid in visited:
                continue
            if max_depth and depth > max_depth:
                continue

            visited.add(pid)
            parent = cache.get(pid)
            if parent:
                ancestors.append(parent)
                queue.extend((gpid, depth + 1) for gpid in parent.parent_ids)

        return ancestors

    def get_descendants(self, record_id: str, max_depth: int | None = None) -> list[LineageRecord]:
        """Get all descendants of a record (children, grandchildren, etc.).

        Args:
            record_id: The record to find descendants for
            max_depth: Maximum depth to traverse (None = unlimited)

        Returns:
            List of descendant records, ordered from nearest to farthest
        """
        cache = self._load_cache()

        # Build reverse index (child -> parents becomes parent -> children)
        children_map: dict[str, list[str]] = {}
        for rid, record in cache.items():
            for pid in record.parent_ids:
                children_map.setdefault(pid, []).append(rid)

        descendants: list[LineageRecord] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(cid, 1) for cid in children_map.get(record_id, [])]

        while queue:
            cid, depth = queue.pop(0)
            if cid in visited:
                continue
            if max_depth and depth > max_depth:
                continue

            visited.add(cid)
            child = cache.get(cid)
            if child:
                descendants.append(child)
                queue.extend((gcid, depth + 1) for gcid in children_map.get(cid, []))

        return descendants

    def export_graph(self) -> dict[str, Any]:
        """Export the full lineage as a JSON-serializable graph.

        Returns:
            Dict with 'nodes' and 'edges' suitable for visualization
        """
        records = self.store.load_lineage()

        nodes = []
        edges = []

        for record in records:
            nodes.append({
                "id": record.record_id,
                "type": record.record_type,
                "model_id": record.model_id,
                "generation": record.generation,
                "episode_count": len(record.episode_ids),
                "created_at": record.created_at,
            })

            for parent_id in record.parent_ids:
                edges.append({
                    "source": parent_id,
                    "target": record.record_id,
                    "type": "parent_of",
                })

        return {
            "nodes": nodes,
            "edges": edges,
            "generations": self.get_current_generation() + 1,
            "total_records": len(records),
        }

    def get_episodes_for_model(self, record_id: str) -> list[str]:
        """Get all episode IDs that contributed to a model.

        For merged models, this aggregates episodes from all ancestors.
        """
        record = self.get_record(record_id)
        if not record:
            return []

        all_episodes = set(record.episode_ids)

        # For merged models, also get episodes from ancestors
        if record.record_type == "merged":
            for ancestor in self.get_ancestors(record_id):
                all_episodes.update(ancestor.episode_ids)

        return sorted(all_episodes)
