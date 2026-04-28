# Autonomy Signal Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four audit findings that contaminate the Phase 5 soak acceptance signal so the `autonomy-accept` verdict reflects reality: B3 (reporter source-name mismatch — every soak reports SC2 = 0), B4 (preflight queue-ready gate is a tautological pass), watchdog revert counter never incremented from the daemon, and guardrail regex compiled at every episode instead of at config load.

**Architecture:** Introduce `homunculus/autonomy/sources.py` as a single source of truth for the `GeneratedTask.source` vocabulary used by both producers (`task_generator`, `suggestions`) and the consumer (`reporter`). Tighten `_gate_task_queue_ready` so an empty queue + an empty fallback generator = `passed=False` with actionable detail. Wire `Watchdog.record_task_revert` into `Daemon.run_once` at the point that already counts reverts. Compile guardrail regex patterns once at `load_config` via a typed `CompiledGuardrailRule` so a bad pattern fails the process at launch, not at first episode.

**Tech Stack:** Python 3.11+, standard library only (`re`, `dataclasses`, `pathlib`, `json`). Tests use `unittest`, `tempfile.TemporaryDirectory`, and the real `ArtifactStore` — no MagicMock of the SUT.

---

## File Structure

**New:**
- `homunculus/autonomy/sources.py` — Frozen sets + helper classifying `GeneratedTask.source` values into SC2 buckets (`SELF_DIRECTED_SOURCES`, `SUGGESTION_SOURCES`).

**Modified:**
- `homunculus/autonomy/reporter.py` — `_count_self_directed` and `_count_suggestion_tasks` consume `sources.py`.
- `homunculus/autonomy/preflight.py` — `_gate_task_queue_ready` rejects empty queue when no introspection cache exists.
- `homunculus/daemon.py` — Call `self._watchdog.record_task_revert(task.task_id)` in the `elif outcome == "reverted"` branch, then `self._watchdog.save()`.
- `homunculus/config.py` — Compile guardrail regex in `_parse_rules`; store as `CompiledGuardrailRule` with the original string preserved for diagnostics.
- `homunculus/policy.py` — `GuardrailEngine.evaluate` consumes pre-compiled patterns; remove inline `re.search` with flags.
- `homunculus/models.py` — `GeneratedTask.source` docstring updated to enumerate accepted values (`"introspection"`, `"user"`, `"continuation"`).

**Test files (modified):**
- `tests/test_autonomy.py` — New test classes: `ReporterSourceHarmonizationTests`, `PreflightQueueReadyHardenedTests`.
- `tests/test_daemon.py` — New test class: `WatchdogRevertWiringTests`.
- `tests/test_suggestions.py` — Add assertion that emitted task uses `"user"` (lock the producer contract).
- `tests/test_task_generator.py` — Add assertion that emitted tasks use `"introspection"` (lock the producer contract).
- *Not created:* a dedicated `tests/test_policy.py` — augment `tests/test_orchestrator.py` instead since `GuardrailEngine` is already exercised there.

---

## Self-Review Notes (performed at end)

Covered: all 4 BLOCKER + POLISH items from audit bucket 2 (B3, B4, watchdog wiring, policy regex). Not covered (intentionally deferred to follow-up plans): `autonomy-accept --soak-log` orphan flag (move to config-hygiene-v2), orchestrator-safety items (B1/B2/S1/S2). Type consistency: `CompiledGuardrailRule` used identically in Tasks 10/11/12. Every test step has concrete test code.

---

## Wave 1 — Source Vocabulary Alignment (B3)

### Task 1: Create `homunculus/autonomy/sources.py` with vocabulary constants

**Files:**
- Create: `homunculus/autonomy/sources.py`
- Modify: `homunculus/autonomy/__init__.py` (re-export)
- Test: `tests/test_autonomy.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_autonomy.py`:

```python
class AutonomySourcesVocabularyTests(unittest.TestCase):
    """The SC2 source-name vocabulary is the contract between
    producers (task_generator, suggestions) and the consumer
    (reporter). Lock it here so any future rename breaks this test."""

    def test_self_directed_matches_producer_emission(self):
        from homunculus.autonomy.sources import (
            SELF_DIRECTED_SOURCES,
            SUGGESTION_SOURCES,
            classify_source,
        )
        # Producers emit these literals today; see task_generator/generator.py
        # (source="introspection") and suggestions.py (source="user").
        self.assertIn("introspection", SELF_DIRECTED_SOURCES)
        self.assertIn("continuation", SELF_DIRECTED_SOURCES)
        self.assertIn("user", SUGGESTION_SOURCES)
        # No overlap.
        self.assertFalse(SELF_DIRECTED_SOURCES & SUGGESTION_SOURCES)

    def test_classify_source_normalizes_case_and_whitespace(self):
        from homunculus.autonomy.sources import classify_source
        self.assertEqual(classify_source("Introspection"), "self_directed")
        self.assertEqual(classify_source("  user  "), "suggestion")
        self.assertEqual(classify_source("continuation"), "self_directed")
        self.assertEqual(classify_source(""), "other")
        self.assertEqual(classify_source(None), "other")
        self.assertEqual(classify_source("unknown-source"), "other")
```

- [ ] **Step 2: Run test and verify it fails**

```
python -m unittest tests.test_autonomy.AutonomySourcesVocabularyTests -v
```

Expected: `ModuleNotFoundError: No module named 'homunculus.autonomy.sources'`.

- [ ] **Step 3: Create `homunculus/autonomy/sources.py`**

```python
"""SC2 source-name vocabulary.

Producers (task_generator, suggestions) emit :class:`GeneratedTask.source`
literals; consumers (reporter) classify those literals into SC2 buckets.
Keeping the vocabulary in one module prevents drift like the original
B3 defect where the reporter matched ``{"generated", "resonance"}`` but
no producer ever emitted those values.

Add a new source literal here first, then in the producing module, then
in ``GeneratedTask.source``'s docstring — in that order.
"""
from __future__ import annotations

from typing import Literal

# Values emitted by ``task_generator.TaskGenerator`` (all introspection-
# derived) and any future continuation source the daemon adds. The
# reporter counts these as SC2 self-directed.
SELF_DIRECTED_SOURCES: frozenset[str] = frozenset({"introspection", "continuation"})

# Values emitted by ``suggestions.SuggestionReader`` when an operator
# drops a file under ``suggestions/``. Counts toward SC2 suggestion tasks.
SUGGESTION_SOURCES: frozenset[str] = frozenset({"user"})

SourceClass = Literal["self_directed", "suggestion", "other"]


def classify_source(raw: str | None) -> SourceClass:
    """Classify a ``GeneratedTask.source`` literal into an SC2 bucket.

    The reporter calls this instead of open-coding ``source in {...}``
    so a future rename or addition only has to touch this module.
    Case- and whitespace-insensitive; ``None`` or unknown literals
    return ``"other"`` (neither bucket counts them).
    """
    if not raw:
        return "other"
    normalized = raw.strip().lower()
    if normalized in SELF_DIRECTED_SOURCES:
        return "self_directed"
    if normalized in SUGGESTION_SOURCES:
        return "suggestion"
    return "other"
```

- [ ] **Step 4: Re-export from `homunculus/autonomy/__init__.py`**

Open the file and add after the existing imports:

```python
from .sources import SELF_DIRECTED_SOURCES, SUGGESTION_SOURCES, classify_source
```

Add the three names to the `__all__` list. If `__all__` does not exist, add:

```python
__all__ = [
    # preserve any existing names here
    "SELF_DIRECTED_SOURCES",
    "SUGGESTION_SOURCES",
    "classify_source",
]
```

- [ ] **Step 5: Run test to verify it passes**

```
python -m unittest tests.test_autonomy.AutonomySourcesVocabularyTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add homunculus/autonomy/sources.py homunculus/autonomy/__init__.py tests/test_autonomy.py
git commit -m "feat(autonomy): source-name vocabulary module

Introduce homunculus.autonomy.sources as the single source of truth for
GeneratedTask.source literals and their SC2 classification. Unblocks
the B3 reporter fix (next task)."
```

---

### Task 2: Lock producer contract — `task_generator` emits `"introspection"`

**Files:**
- Modify: `tests/test_task_generator.py`

- [ ] **Step 1: Write the failing assertion-gap test**

The producer already emits `"introspection"` (15+ call sites). We are codifying the contract so any rename fails here rather than silently at the reporter. Append to `tests/test_task_generator.py`:

```python
class TaskGeneratorSourceContractTests(unittest.TestCase):
    """Lock the ``source`` literal emitted by the task generator.

    Regressions manifested historically as B3: the reporter expected
    ``"generated"`` or ``"resonance"`` but the producer emitted
    ``"introspection"``, silently zeroing SC2. If you change the
    producer literal, update ``SELF_DIRECTED_SOURCES`` in
    ``homunculus.autonomy.sources`` *first*."""

    def test_metrics_findings_emit_introspection_source(self) -> None:
        from homunculus.task_generator import TaskGenerator
        from homunculus.autonomy.sources import SELF_DIRECTED_SOURCES

        gen = TaskGenerator()
        findings = [
            {"type": "high_error_rate", "value": 0.5, "severity": "critical"}
        ]
        result = _make_result("metrics", findings)
        tasks = gen.generate_from_introspection([result])
        self.assertTrue(tasks, "generator must yield at least one task")
        for task in tasks:
            self.assertIn(
                task.source,
                SELF_DIRECTED_SOURCES,
                f"producer emitted {task.source!r} which is not in "
                f"SELF_DIRECTED_SOURCES={SELF_DIRECTED_SOURCES}",
            )
```

If `_make_result` is not visible at module scope in this file, copy the helper from an existing test in the same file and place it at the top of the new class as `_make_result = staticmethod(...)` or import it.

- [ ] **Step 2: Run test to verify it passes immediately**

```
python -m unittest tests.test_task_generator.TaskGeneratorSourceContractTests -v
```

Expected: PASS (producer already emits `"introspection"`). This is a contract-lock test; its value is catching future drift.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_task_generator.py
git commit -m "test(task_generator): lock source literal at 'introspection'

Codifies the producer-side contract for B3. If a future refactor
renames this literal, the test fails before the reporter silently
zeroes SC2."
```

---

### Task 3: Lock producer contract — `SuggestionReader` emits `"user"`

**Files:**
- Modify: `tests/test_suggestions.py`

- [ ] **Step 1: Write the contract-lock test**

Append to `tests/test_suggestions.py`:

```python
class SuggestionReaderSourceContractTests(unittest.TestCase):
    """Lock the ``source`` literal emitted when a suggestion is
    materialized as a ``GeneratedTask`` (see B3)."""

    def test_emitted_task_has_user_source(self) -> None:
        import tempfile
        from pathlib import Path
        from homunculus.suggestions import SuggestionReader
        from homunculus.autonomy.sources import SUGGESTION_SOURCES

        with tempfile.TemporaryDirectory() as tmp:
            suggestions_dir = Path(tmp) / "suggestions"
            suggestions_dir.mkdir()
            (suggestions_dir / "fix-the-thing.md").write_text(
                "Please fix the thing.", encoding="utf-8"
            )
            reader = SuggestionReader(suggestions_dir=suggestions_dir)
            tasks = list(reader.iter_new_tasks())
            self.assertTrue(tasks, "reader must yield at least one task")
            for task in tasks:
                self.assertIn(
                    task.source,
                    SUGGESTION_SOURCES,
                    f"SuggestionReader emitted {task.source!r} which is "
                    f"not in SUGGESTION_SOURCES={SUGGESTION_SOURCES}",
                )
```

If `SuggestionReader` does not expose `iter_new_tasks` or takes different constructor arguments, read `homunculus/suggestions.py` and adjust the test to the real entry-point method (the test's intent — emitting a task with `source="user"` — is what must be preserved).

- [ ] **Step 2: Run test to verify it passes immediately**

```
python -m unittest tests.test_suggestions.SuggestionReaderSourceContractTests -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_suggestions.py
git commit -m "test(suggestions): lock source literal at 'user'

Pins the SuggestionReader producer-side contract for B3."
```

---

### Task 4: Rewire `reporter._count_self_directed` to use `classify_source`

**Files:**
- Modify: `homunculus/autonomy/reporter.py` (lines 236–248 and 250–258)
- Modify: `tests/test_autonomy.py`

- [ ] **Step 1: Write the failing reporter test**

Append to `tests/test_autonomy.py`:

```python
class ReporterSourceHarmonizationTests(unittest.TestCase):
    """B3 regression test — real producer literals must count."""

    def _entry(self, source: str, outcome: str) -> dict:
        return {
            "task_id": f"t-{source}-{outcome}",
            "outcome": outcome,
            "task": {"source": source},
        }

    def test_introspection_task_counts_as_self_directed(self):
        from homunculus.autonomy.reporter import _count_self_directed
        history = [self._entry("introspection", "success")]
        self.assertEqual(_count_self_directed(history), 1)

    def test_continuation_task_counts_as_self_directed(self):
        from homunculus.autonomy.reporter import _count_self_directed
        history = [self._entry("continuation", "success")]
        self.assertEqual(_count_self_directed(history), 1)

    def test_user_task_counts_as_suggestion(self):
        from homunculus.autonomy.reporter import _count_suggestion_tasks
        history = [self._entry("user", "success")]
        self.assertEqual(_count_suggestion_tasks(history), 1)

    def test_failed_outcome_never_counts(self):
        from homunculus.autonomy.reporter import (
            _count_self_directed,
            _count_suggestion_tasks,
        )
        history = [
            self._entry("introspection", "error"),
            self._entry("user", "blocked"),
        ]
        self.assertEqual(_count_self_directed(history), 0)
        self.assertEqual(_count_suggestion_tasks(history), 0)

    def test_legacy_literals_no_longer_counted(self):
        """The old hardcoded ``generated`` / ``resonance`` literals were
        never emitted by any producer. They must NOT be counted (they
        were the B3 symptom; leaving them would mask the fix)."""
        from homunculus.autonomy.reporter import _count_self_directed
        history = [
            self._entry("generated", "success"),
            self._entry("resonance", "success"),
        ]
        self.assertEqual(_count_self_directed(history), 0)
```

- [ ] **Step 2: Run test and verify it fails**

```
python -m unittest tests.test_autonomy.ReporterSourceHarmonizationTests -v
```

Expected: FAIL (current reporter matches `{"generated", "resonance"}` / `"suggestion"`, which no producer emits; `test_legacy_literals_no_longer_counted` will *pass* under the broken code but the three positive tests will fail).

- [ ] **Step 3: Modify `homunculus/autonomy/reporter.py`**

Near the top of the file add:

```python
from .sources import classify_source
```

Replace `_count_self_directed` (was at `reporter.py:236-248`) and `_count_suggestion_tasks` (was at `reporter.py:250-258`) with:

```python
def _count_self_directed(history: Iterable[dict[str, Any]]) -> int:
    """Count successful tasks whose source classifies as self-directed.

    Consumes :func:`homunculus.autonomy.sources.classify_source` so the
    producer vocabulary is the single source of truth. B3 (2026-04-16)
    surfaced when this function open-coded ``{"generated", "resonance"}``
    which no producer emitted, silently zeroing SC2.
    """
    count = 0
    for entry in history:
        if not _entry_outcome_success(entry):
            continue
        if classify_source(_entry_task_source(entry)) == "self_directed":
            count += 1
    return count


def _count_suggestion_tasks(history: Iterable[dict[str, Any]]) -> int:
    """Count successful suggestion-sourced tasks.

    See :func:`_count_self_directed` for the classification contract.
    """
    count = 0
    for entry in history:
        if not _entry_outcome_success(entry):
            continue
        if classify_source(_entry_task_source(entry)) == "suggestion":
            count += 1
    return count
```

- [ ] **Step 4: Run tests and verify they pass**

```
python -m unittest tests.test_autonomy.ReporterSourceHarmonizationTests -v
python -m unittest tests.test_autonomy -v
```

Expected: ALL PASS. The second command guards against regressing any pre-existing reporter test.

- [ ] **Step 5: Commit**

```powershell
git add homunculus/autonomy/reporter.py tests/test_autonomy.py
git commit -m "fix(autonomy): reporter classifies source via sources module (B3)

Before: _count_self_directed matched {generated, resonance} and
_count_suggestion_tasks matched 'suggestion'. No producer emits those
literals — task_generator emits 'introspection' and suggestions emits
'user'. Every real soak reported SC2 = 0, making acceptance always
fail on the self-directed criterion. Now: both counters consume
classify_source() which resolves against SELF_DIRECTED_SOURCES /
SUGGESTION_SOURCES frozensets defined alongside the producer surface."
```

---

## Wave 2 — Preflight Queue-Ready Hardening (B4)

### Task 5: `_gate_task_queue_ready` fails when queue empty AND no fresh introspection cache

**Files:**
- Modify: `homunculus/autonomy/preflight.py` (lines ~225–290, the `_gate_task_queue_ready` function)
- Modify: `tests/test_autonomy.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_autonomy.py`:

```python
class PreflightQueueReadyHardenedTests(unittest.TestCase):
    """B4 regression: empty queue + no fallback signal must fail."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "runtime").mkdir()
        (self.root / "traces").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _settings(self):
        """Build a minimal HomunculusConfig-shaped stub pointing at
        the tmp dirs. Uses a real config where possible via the
        example.toml path-rewrite pattern already used elsewhere in
        tests/test_daemon.py."""
        from homunculus.config import load_config
        source = Path("homunculus.example.toml").read_text(encoding="utf-8")
        config_path = self.root / "config.toml"
        config_path.write_text(
            source.replace('path = "."', f'path = "{self.root.as_posix()}"', 1),
            encoding="utf-8",
        )
        return load_config(config_path)

    def test_empty_queue_and_empty_introspection_cache_fails(self):
        """When there are no pending entries AND no introspection
        results on disk that the generator could consume, the gate
        must fail — previously it passed via the tautological
        TaskGenerator(store=None) fallback."""
        from homunculus.autonomy.preflight import _gate_task_queue_ready
        settings = self._settings()
        result = _gate_task_queue_ready(settings)
        self.assertFalse(result.passed, result.detail)
        self.assertIn("no pending tasks", result.detail.lower())

    def test_empty_queue_but_introspection_cache_present_passes(self):
        """If introspection results exist on disk, the generator CAN
        synthesize work, so the gate passes with informative detail."""
        from homunculus.autonomy.preflight import _gate_task_queue_ready
        # Seed a minimal introspection result so the fallback has
        # something real to evaluate.
        introspection_path = self.root / "traces" / "introspection.jsonl"
        introspection_path.write_text(
            '{"mode": "metrics", "findings": [{"type": "high_error_rate",'
            ' "value": 0.5, "severity": "critical"}]}\n',
            encoding="utf-8",
        )
        settings = self._settings()
        result = _gate_task_queue_ready(settings)
        self.assertTrue(result.passed, result.detail)

    def test_pending_queue_entry_passes(self):
        """Existing behavior: non-empty queue = pass."""
        from homunculus.autonomy.preflight import _gate_task_queue_ready
        queue_path = self.root / "runtime" / "task_queue.jsonl"
        queue_path.write_text(
            '{"task_id":"x","status":"pending","task":{"source":"introspection","prompt":"p","task_id":"x"}}\n',
            encoding="utf-8",
        )
        settings = self._settings()
        result = _gate_task_queue_ready(settings)
        self.assertTrue(result.passed, result.detail)
        self.assertIn("1 pending task", result.detail)
```

- [ ] **Step 2: Run tests and verify they fail**

```
python -m unittest tests.test_autonomy.PreflightQueueReadyHardenedTests -v
```

Expected: `test_empty_queue_and_empty_introspection_cache_fails` FAILS (current code returns `passed=True` with the tautological message). The other two tests should pass against current code.

- [ ] **Step 3: Rewrite `_gate_task_queue_ready` in `homunculus/autonomy/preflight.py`**

Replace the entire function body (the fallback block starts around line 278) with:

```python
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
    cache is NOT ready — it means the soak will idle for seven days.
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
                "task or run `homunculus run-introspection` first."
            ),
        )
    try:
        from ..task_generator import TaskGenerator  # local to avoid import cycles
        from ..models import IntrospectionResult

        results = []
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
            f"{len(results)} introspection record(s) ({len(synthesized)} dry-run task)."
        ),
    )
```

If `settings.paths.traces_dir` is not the correct attribute for the traces directory, open `homunculus/config.py` and use whichever attribute resolves to `traces/` (likely `paths.traces` or `paths.root / "traces"`). Keep the rest of the function identical.

If `IntrospectionResult.from_dict` does not exist, use whatever constructor exists on `IntrospectionResult` (inspect `homunculus/models.py`); the test only requires that at least one well-formed introspection record lets the generator produce a task.

- [ ] **Step 4: Run tests and verify they pass**

```
python -m unittest tests.test_autonomy.PreflightQueueReadyHardenedTests -v
python -m unittest tests.test_autonomy -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```powershell
git add homunculus/autonomy/preflight.py tests/test_autonomy.py
git commit -m "fix(autonomy): preflight queue-ready rejects empty cache (B4)

_gate_task_queue_ready previously passed whenever TaskGenerator(None)
could be constructed — a tautology, since construction cannot raise
for valid settings. An operator with an empty queue AND no cached
introspection results would pass preflight, launch the soak, and idle
for seven days. Now the gate performs a real generator dry-run
against traces/introspection.jsonl and fails closed when either the
cache is absent or the dry-run returns zero tasks."
```

---

## Wave 3 — Watchdog Revert Wiring

### Task 6: `Daemon.run_once` calls `self._watchdog.record_task_revert` on reverted outcomes

**Files:**
- Modify: `homunculus/daemon.py` (around line 490–495, the `elif outcome == "reverted"` branch)
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon.py`:

```python
class WatchdogRevertWiringTests(unittest.TestCase):
    """The watchdog's ``repeat_revert:<task_id>`` flag depends on
    ``record_task_revert`` being called from the daemon. Without the
    wiring, the flag can never fire."""

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reverted_outcome_increments_watchdog_revert_counter(self):
        import json
        from pathlib import Path
        from homunculus.daemon import Daemon
        from homunculus.models import (
            EpisodeRecord, GeneratedTask, TaskQueueEntry, utc_now,
        )

        # Build a Daemon against a real tmp-backed config (pattern lifted
        # from existing TaskQueuePersistenceTests in this file).
        root = Path(self._tmp.name)
        source_toml = Path("homunculus.example.toml").read_text(encoding="utf-8")
        config_path = root / "config.toml"
        config_path.write_text(
            source_toml.replace('path = "."', f'path = "{root.as_posix()}"', 1),
            encoding="utf-8",
        )
        from homunculus.config import load_config
        from homunculus.storage import ArtifactStore
        settings = load_config(config_path)
        store = ArtifactStore(settings)
        store.ensure_layout()

        # Enqueue one pending task whose episode will report reverted.
        task = GeneratedTask(
            task_id="rev-1", source="introspection", prompt="p"
        )
        store.append_to_queue(TaskQueueEntry(
            task_id=task.task_id, task=task, queued_at=utc_now(),
            status="pending",
        ))

        class RevertingOrchestrator:
            def run_episode(self, request):
                return EpisodeRecord(
                    episode_id="ep-1",
                    task_id=request.task_id,
                    outcome="reverted",
                )

        daemon = Daemon(
            settings,
            orchestrator=RevertingOrchestrator(),
            store=store,
        )
        result = daemon.run_once()

        self.assertEqual(result.tasks_reverted, 1)
        # Watchdog must have incremented the revert counter for this
        # task id and persisted it.
        watchdog_path = settings.paths.runtime_dir / "watchdog.json"
        self.assertTrue(watchdog_path.exists(),
                        "watchdog must persist after record_task_revert")
        snapshot = json.loads(watchdog_path.read_text(encoding="utf-8"))
        self.assertEqual(
            snapshot.get("repeated_task_reverts", {}).get("rev-1"),
            1,
            f"unexpected snapshot: {snapshot}",
        )

    def test_accepted_outcome_does_not_increment_revert(self):
        """Non-reverted outcomes must NOT call record_task_revert."""
        import json
        from pathlib import Path
        from homunculus.daemon import Daemon
        from homunculus.models import (
            EpisodeRecord, GeneratedTask, TaskQueueEntry, utc_now,
        )
        from homunculus.config import load_config
        from homunculus.storage import ArtifactStore

        root = Path(self._tmp.name)
        source_toml = Path("homunculus.example.toml").read_text(encoding="utf-8")
        config_path = root / "config.toml"
        config_path.write_text(
            source_toml.replace('path = "."', f'path = "{root.as_posix()}"', 1),
            encoding="utf-8",
        )
        settings = load_config(config_path)
        store = ArtifactStore(settings)
        store.ensure_layout()

        task = GeneratedTask(task_id="ok-1", source="introspection", prompt="p")
        store.append_to_queue(TaskQueueEntry(
            task_id=task.task_id, task=task, queued_at=utc_now(),
            status="pending",
        ))

        class AcceptingOrchestrator:
            def run_episode(self, request):
                return EpisodeRecord(
                    episode_id="ep-ok",
                    task_id=request.task_id,
                    outcome="accepted",
                )

        daemon = Daemon(
            settings,
            orchestrator=AcceptingOrchestrator(),
            store=store,
        )
        daemon.run_once()

        watchdog_path = settings.paths.runtime_dir / "watchdog.json"
        if watchdog_path.exists():
            snapshot = json.loads(watchdog_path.read_text(encoding="utf-8"))
            self.assertEqual(
                snapshot.get("repeated_task_reverts", {}),
                {},
                "accepted outcome must not touch revert counters",
            )
```

If `EpisodeRecord` requires additional fields (e.g. `created_at`, `rationale`), inspect `homunculus/models.py` and pass the minimum set that lets the object construct — the test only cares about `outcome` and `task_id`.

- [ ] **Step 2: Run tests and verify they fail**

```
python -m unittest tests.test_daemon.WatchdogRevertWiringTests -v
```

Expected: `test_reverted_outcome_increments_watchdog_revert_counter` FAILS because `watchdog.json` does not get written (no caller of `record_task_revert` exists in the daemon). The accepted-outcome test may pass vacuously.

- [ ] **Step 3: Wire the watchdog call in `homunculus/daemon.py`**

Locate the existing branch (around `daemon.py:489-491`):

```python
                if outcome == "accepted":
                    accepted += 1
                elif outcome == "reverted":
                    reverted += 1
```

Replace it with:

```python
                if outcome == "accepted":
                    accepted += 1
                elif outcome == "reverted":
                    reverted += 1
                    # Phase-5 watchdog: record the revert so
                    # repeat_revert:<task_id> can surface once the
                    # per-task threshold is crossed. Wrapped so a
                    # watchdog I/O failure never crashes a cycle.
                    try:
                        self._watchdog.record_task_revert(task.task_id)
                        self._watchdog.save()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Watchdog revert recording failed for %s: %s",
                            task.task_id, exc,
                        )
```

- [ ] **Step 4: Run the new tests and then the full daemon suite**

```
python -m unittest tests.test_daemon.WatchdogRevertWiringTests -v
python -m unittest tests.test_daemon -v
```

Expected: ALL PASS, including every prior test in `test_daemon.py`.

- [ ] **Step 5: Commit**

```powershell
git add homunculus/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): wire watchdog.record_task_revert on reverted outcome

Watchdog defined record_task_revert + the repeat_revert:<id> threshold
since the Phase-5 autonomy landing, but no caller existed. The flag
could never fire; any poison task that reverted repeatedly went
unobserved. Now the reverted branch records + persists the counter,
and autonomy reporter will surface repeat_revert:* via watchdog_flags."
```

---

## Wave 4 — Guardrail Regex Compiled At Config Load

### Task 7: Introduce `CompiledGuardrailRule` dataclass

**Files:**
- Modify: `homunculus/config.py`

- [ ] **Step 1: Extend the config dataclasses**

Open `homunculus/config.py`. Near the top of the file (in the dataclass definitions block, before `GuardrailSettings`), add:

```python
@dataclass(frozen=True)
class CompiledGuardrailRule:
    """A guardrail pattern with its regex pre-compiled at config load.

    ``pattern`` is the original string (kept for diagnostics / serialization
    round-trips). ``regex`` is the compiled counterpart used by
    :class:`GuardrailEngine`. Compilation happens once in
    :func:`_parse_rules` so a malformed pattern fails ``load_config``
    rather than the first episode."""

    pattern: str
    message: str
    regex: "re.Pattern[str]"
```

Ensure `import re` is at the top of the file. Do NOT remove the existing `GuardrailRule` class yet — Task 8 will migrate fields on the consumer side before deletion.

- [ ] **Step 2: Leave consumers unchanged for this task**

This task is a pure additive definition. Compile-at-load wiring happens in Task 8. No test runs yet.

- [ ] **Step 3: Commit**

```powershell
git add homunculus/config.py
git commit -m "refactor(config): add CompiledGuardrailRule dataclass (no wiring yet)

Preparatory step for the launch-time-compile change. Keeps the
original pattern string alongside the compiled regex for diagnostics.
No consumer uses this type yet; consumer switch lands in the next commit."
```

---

### Task 8: `_parse_rules` compiles patterns at load time; `GuardrailEngine` consumes pre-compiled

**Files:**
- Modify: `homunculus/config.py` (`_parse_rules` and `GuardrailSettings`)
- Modify: `homunculus/policy.py` (`GuardrailEngine.evaluate`)
- Modify: `tests/test_orchestrator.py` (or wherever guardrail tests live — see Step 1)

- [ ] **Step 1: Write the failing test**

First, confirm where guardrail tests currently live:

```
python -c "import subprocess; r = subprocess.run(['grep','-rln','GuardrailEngine\\|guardrails','tests/'], capture_output=True, text=True); print(r.stdout)"
```

Add this test to the file returned by the grep (most likely `tests/test_orchestrator.py`; if no such file exists, create `tests/test_policy.py` with the usual `unittest` boilerplate):

```python
class GuardrailCompileAtLoadTests(unittest.TestCase):
    """Invalid regex must surface at load_config, not mid-episode.

    Rationale: a soak that runs into an invalid guardrail crashes
    episode N+1 with a re.error traceback at a moment the operator
    can't reach the console. Fail loud at launch instead."""

    def _write_config(self, root, extra_block: str):
        import tempfile
        from pathlib import Path
        source = Path("homunculus.example.toml").read_text(encoding="utf-8")
        config_path = Path(root) / "config.toml"
        config_path.write_text(
            source.replace('path = "."', f'path = "{Path(root).as_posix()}"', 1)
            + "\n" + extra_block,
            encoding="utf-8",
        )
        return config_path

    def test_invalid_block_pattern_fails_load_config(self):
        import tempfile
        import re as _re
        from homunculus.config import load_config
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                '[guardrails]\n'
                'block_patterns = [\n'
                '  { pattern = "(unclosed", message = "bad regex" }\n'
                ']\n',
            )
            with self.assertRaises(_re.error):
                load_config(path)

    def test_valid_block_pattern_is_precompiled(self):
        import tempfile
        import re as _re
        from homunculus.config import load_config, CompiledGuardrailRule
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                '[guardrails]\n'
                'block_patterns = [\n'
                '  { pattern = "rm -rf", message = "destructive" }\n'
                ']\n',
            )
            settings = load_config(path)
            rules = settings.guardrails.block_patterns
            self.assertEqual(len(rules), 1)
            self.assertIsInstance(rules[0], CompiledGuardrailRule)
            self.assertIsInstance(rules[0].regex, _re.Pattern)
            self.assertEqual(rules[0].pattern, "rm -rf")

    def test_engine_uses_precompiled_regex(self):
        """GuardrailEngine.evaluate must call the pre-compiled
        ``regex.search``, not ``re.search`` on the string pattern.
        Prove it by handing the engine a rule whose ``pattern`` string
        is gibberish and whose ``regex`` matches anything — the engine
        must honor the regex, proving it doesn't recompile."""
        import re as _re
        from homunculus.config import CompiledGuardrailRule, GuardrailSettings
        from homunculus.policy import GuardrailEngine

        catch_all = CompiledGuardrailRule(
            pattern="(this would not compile",
            message="blocked",
            regex=_re.compile(r".*"),
        )
        settings = GuardrailSettings(
            block_patterns=[catch_all], warn_patterns=[]
        )
        engine = GuardrailEngine(settings)
        decision = engine.evaluate("hello", candidate_patch=None, memories=[])
        self.assertFalse(decision.allowed)
        self.assertIn("blocked", decision.blocked_reasons)
```

- [ ] **Step 2: Run tests and verify they fail**

```
python -m unittest tests.test_orchestrator.GuardrailCompileAtLoadTests -v
```

(Substitute `tests.test_policy.GuardrailCompileAtLoadTests` if you created a new file.)

Expected: all three tests FAIL — `load_config` currently accepts an invalid regex (it never compiles); the engine uses `re.search(rule.pattern, body, ...)` which recompiles every call and ignores `rule.regex`.

- [ ] **Step 3: Update `_parse_rules` in `homunculus/config.py`**

Find `_parse_rules` (called from `load_config` at `config.py:305-307`). Replace its body with:

```python
def _parse_rules(raw: Any) -> list[CompiledGuardrailRule]:
    """Parse a list of ``{pattern, message}`` TOML tables into
    :class:`CompiledGuardrailRule` instances. Compiles the regex at
    load so a malformed pattern crashes startup, not episode N.
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"guardrails.*_patterns must be a list of tables, got {type(raw).__name__}"
        )
    rules: list[CompiledGuardrailRule] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"guardrails pattern #{i} must be a table with "
                f"'pattern' and 'message' keys"
            )
        pattern = entry.get("pattern")
        message = entry.get("message")
        if not isinstance(pattern, str) or not isinstance(message, str):
            raise ValueError(
                f"guardrails pattern #{i} requires string 'pattern' "
                f"and 'message' keys; got {entry!r}"
            )
        compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        rules.append(CompiledGuardrailRule(
            pattern=pattern, message=message, regex=compiled
        ))
    return rules
```

If `_parse_rules` has a different signature (e.g. takes a section name), preserve that signature and only change the return type + compile behavior.

Change `GuardrailSettings` (should already exist near `config.py:176`) to annotate both list fields as `list[CompiledGuardrailRule]`:

```python
@dataclass
class GuardrailSettings:
    block_patterns: list[CompiledGuardrailRule]
    warn_patterns: list[CompiledGuardrailRule]
```

If `GuardrailRule` has external consumers outside `policy.py` (grep to confirm), keep the old class as a deprecated alias:

```python
# Backwards compat — external users that unpacked the raw string are
# deprecated. Remove after Phase 6.
GuardrailRule = CompiledGuardrailRule
```

- [ ] **Step 4: Update `GuardrailEngine.evaluate` in `homunculus/policy.py`**

Replace the entire `evaluate` method body with:

```python
    def evaluate(
        self,
        prompt: str,
        candidate_patch: str | None,
        memories: list[MemoryRecord],
    ) -> GuardrailDecision:
        body = f"{prompt}\n{candidate_patch or ''}"
        warnings: list[str] = []
        blocked: list[str] = []
        memory_refs: list[str] = []

        for rule in self.settings.warn_patterns:
            if rule.regex.search(body):
                warnings.append(rule.message)

        for rule in self.settings.block_patterns:
            if rule.regex.search(body):
                blocked.append(rule.message)

        for memory in memories:
            if memory.category in {"warning", "failure"}:
                warnings.append(f"Relevant {memory.category}: {memory.content[:120]}")
                memory_refs.append(memory.id)

        return GuardrailDecision(
            allowed=not blocked,
            warnings=warnings,
            blocked_reasons=blocked,
            memory_refs=memory_refs,
        )
```

Remove the `import re` in `policy.py` if it becomes unused (let linter drive this).

- [ ] **Step 5: Run targeted + full suite**

```
python -m unittest tests.test_orchestrator.GuardrailCompileAtLoadTests -v
python -m unittest discover -v 2>&1 | tail -20
```

Expected: all three new tests PASS. Full suite PASS (326 → 333 or similar — any delta is the new tests). If any pre-existing test fails, it likely mocked `rule.pattern` directly; update it to construct via `CompiledGuardrailRule(pattern="...", message="...", regex=re.compile("..."))`.

- [ ] **Step 6: Commit**

```powershell
git add homunculus/config.py homunculus/policy.py tests/test_orchestrator.py
git commit -m "fix(policy): compile guardrail regex at load_config

Before: re.search(rule.pattern, ...) recompiled the regex on every
episode, and a malformed pattern crashed the first episode that
matched. Now: _parse_rules returns CompiledGuardrailRule with the
regex compiled once at load, and the engine calls rule.regex.search.
Malformed patterns fail load_config with re.error so the operator
sees them at launch, not after two minutes of daemon startup."
```

---

## Wave 5 — Phase Closure

### Task 9: Soak acceptance smoke verifying all four fixes integrate

**Files:**
- Modify: `tests/test_autonomy.py` (end-to-end integration test)

- [ ] **Step 1: Write the integration test**

Append to `tests/test_autonomy.py`:

```python
class SignalFidelityIntegrationTests(unittest.TestCase):
    """End-to-end: a full-cycle daemon run populates a realistic
    task_history + watchdog.json, then generate_report returns
    non-zero SC2 counts AND reflects the watchdog's revert signal.
    This is the regression guard for all four Wave 1–4 fixes."""

    def test_full_cycle_populates_real_report_fields(self):
        import json
        import tempfile
        from pathlib import Path
        from homunculus.autonomy.reporter import generate_report
        from homunculus.daemon import Daemon
        from homunculus.config import load_config
        from homunculus.storage import ArtifactStore
        from homunculus.models import (
            EpisodeRecord, GeneratedTask, TaskQueueEntry, utc_now,
        )

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            source_toml = Path("homunculus.example.toml").read_text(encoding="utf-8")
            config_path = root_path / "config.toml"
            config_path.write_text(
                source_toml.replace('path = "."', f'path = "{root_path.as_posix()}"', 1),
                encoding="utf-8",
            )
            settings = load_config(config_path)
            store = ArtifactStore(settings)
            store.ensure_layout()

            # Enqueue one introspection task (should become self-directed)
            # and one user task (should become suggestion) and one task
            # that reverts (should surface via watchdog once threshold hit).
            for i, (source, outcome) in enumerate([
                ("introspection", "accepted"),
                ("user", "accepted"),
                ("introspection", "reverted"),
            ]):
                task = GeneratedTask(
                    task_id=f"t-{i}", source=source, prompt=f"task {i}",
                )
                store.append_to_queue(TaskQueueEntry(
                    task_id=task.task_id, task=task, queued_at=utc_now(),
                    status="pending",
                ))

            class ScriptedOrch:
                def __init__(self):
                    self._outcomes = iter(
                        ["accepted", "accepted", "reverted"]
                    )

                def run_episode(self, request):
                    return EpisodeRecord(
                        episode_id=f"ep-{request.task_id}",
                        task_id=request.task_id,
                        outcome=next(self._outcomes),
                    )

            daemon = Daemon(
                settings, orchestrator=ScriptedOrch(), store=store,
            )
            daemon.run_once()

            report = generate_report(
                runtime_dir=settings.paths.runtime_dir,
                traces_dir=settings.paths.traces_dir,
                models_dir=settings.paths.models_dir,
            )
            # B3: self-directed = introspection-sourced accepted task.
            self.assertEqual(report.self_directed_tasks_completed, 1)
            # B3: suggestion = user-sourced accepted task.
            self.assertEqual(report.suggestion_tasks_completed, 1)
            # Watchdog wiring: reverted counter persisted for the
            # reverted task id (not yet over threshold, so the flag
            # is empty — that's OK, we're asserting the counter, not
            # the derived flag).
            watchdog_path = settings.paths.runtime_dir / "watchdog.json"
            snapshot = json.loads(watchdog_path.read_text(encoding="utf-8"))
            self.assertEqual(
                snapshot.get("repeated_task_reverts", {}).get("t-2"),
                1,
            )
```

If `EpisodeRecord` or `TaskQueueEntry` require other fields to construct, thread them through based on `homunculus/models.py`. The test's intent — after one daemon cycle, the report shows one self-directed + one suggestion + one revert in the watchdog — is the contract; adapt wiring details to the real shape.

- [ ] **Step 2: Run integration test**

```
python -m unittest tests.test_autonomy.SignalFidelityIntegrationTests -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

```
python -m unittest discover -q 2>&1 | tail -10
```

Expected: `OK` with N tests ran (should be ≥ 326 + new tests).

- [ ] **Step 4: Update `CLAUDE.md`**

Open `CLAUDE.md`. Under `### Safety Boundaries`, add a new bullet:

```markdown
- Guardrail regex is compiled at `load_config`. Invalid patterns crash the process at launch, not mid-episode. `GuardrailEngine` consumes `CompiledGuardrailRule` instances with `.regex` already compiled.
```

Under `## Architecture` / `### Module Structure`, add:

```markdown
- `homunculus/autonomy/sources.py` - SC2 source-name vocabulary (`SELF_DIRECTED_SOURCES`, `SUGGESTION_SOURCES`, `classify_source`)
```

- [ ] **Step 5: Commit**

```powershell
git add tests/test_autonomy.py CLAUDE.md
git commit -m "test(autonomy): integration test for signal-fidelity fixes

Exercises B3 + B4 + watchdog wiring in a single daemon cycle. Guards
against regression in any single fix causing SC2 to silently zero
again."
```

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-16-autonomy-signal-fidelity.md`.

Follow-up plans — NOT covered here, each gets its own plan when this one lands:

1. **`2026-04-XX-orchestrator-safety.md`** — B1 (`commit_to_source` `git add -A`), B2 (`outcome==error` event-log skip), S1 (teacher retry/backoff), S2 (memory offline fallback), student-subprocess hygiene.
2. **`2026-04-XX-config-hygiene-v2.md`** — S3 (example.toml missing 6 evolution keys), S4 (`_validate_interval` silent coerce), S5 (unknown-key guard extended beyond `[evolution]`), S6 (`autonomy-accept --soak-log` orphan flag).
3. **Task 14 E2E** — introspection → task_generator → daemon closed-loop assertion test (partial gap from baseline plan Task 14).

Two execution options for THIS plan:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
