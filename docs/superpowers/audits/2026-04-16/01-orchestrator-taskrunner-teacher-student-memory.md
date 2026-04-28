# Audit: Orchestrator / TaskRunner / Teacher / Student / Memory
Date: 2026-04-16

## Summary

The episode lifecycle and auto-commit wiring in this bucket are materially in
better shape than the existing spec-alignment plan implies. Task 16
(`commit_to_source` wiring, `DaemonSettings.auto_commit_on_accept`) is
**already fully resolved at HEAD** — the flag exists (`config.py:114`),
`_auto_commit` is invoked on accepted outcomes (`loop.py:126-136`), and
`tests/test_orchestrator.py` now exercises both the enabled and disabled
branches end-to-end against a real git repo. The remaining risks are
smaller: one real **BLOCKER** where no `outcome == "error"` episode is
persisted to `episodes.jsonl` (the final `append_episode` is outside the
`if outcome != "error"` guard for the *event*, but the reflect-stage memory
write is skipped entirely on guardrail-blocked paths and silently-swallowed
memory failures); a **BLOCKER** in `commit_to_source` that stages
**every** dirty file in the source repo with `git add -A`, not just the
applied patch; three **SILENT-DROP** defects (teacher has no retry/backoff,
student has no graceful mlx-lm-missing fallback, engram has no offline
fallback to `InMemory`); and several polish items. Plan-touching findings
(Tasks 5, 9, 12) are **out of scope of this bucket** and no related code
lives here — no evidence they should be cross-filed against this bucket.

## Cross-reference: Existing Plan Tasks

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| 5 | `_validate_coherence` fails closed without backend | never-accurate (for this bucket) | Task 5 lives in `homunculus/evolution/validation.py`, not in this bucket. No validator/guardrail in orchestrator/runner/teacher/student/memory defaults to pass when deps missing. `teacher.py` raises `RuntimeError` on missing JSON fields; `runner.apply_patch` raises when git is missing. Only borderline case: `StaticTeacher` test-double returns whatever caller provides (expected). |
| 9 | Suggestion archival on blocked/error outcomes | still-valid (but out of bucket) | Lives in `homunculus/daemon.py:215-222`, not in this audit's files. Confirmed: `run_episode` persists `outcome="blocked"` and `outcome="error"` episodes correctly (`loop.py:190-228`), so the upstream archival fix can depend on these outcomes being produced honestly. |
| 12 | Misc fixes (types/paths/NameError/append_to_queue) | out of bucket | Every file listed in Task 12 lives outside this bucket (introspection/, evolution/, daemon.py, cli.py, runtime.py). No equivalent issues found in this bucket's files. |
| 16 | Wire `commit_to_source` into the orchestrator | **resolved** | `config.py:114` defines `auto_commit_on_accept: bool = True`; `loop.py:126-136` calls `_auto_commit` when accepted; `loop.py:310-359` implements `_auto_commit` with apply → commit → event → SHA flow, logging failures but never raising; `tests/test_orchestrator.py:138-289` has `AutoCommitWiringTests` with two end-to-end tests (enabled asserts commit made + SHA recorded + `auto_commit` event emitted; disabled asserts no commit + source clean). Plan's original test stub (MagicMock-based) was superseded by the stronger real-git test that actually executes `commit_to_source`. |

**Net take on plan vs. HEAD in this bucket:** Task 16 can be marked complete
in the plan; Tasks 5/9/12 don't touch this bucket (no cross-file action
required here).

## New Findings (not in existing plan)

### BLOCKER — `commit_to_source` stages the entire working tree, not just the applied patch
- **File:** `homunculus/task_runner/runner.py:162-188`
- **Class:** (a) unwired integration + potential correctness hole
- **Observed:** `commit_to_source` runs `git add -A` then `git commit`. If the
  source workspace contains ANY file outside what `apply_patch` wrote —
  e.g. an editor swap file, a `.pyc`, a test artifact from a previous
  verification, a user's concurrent edit slipped past preflight — those are
  all swept into the auto-commit alongside the patch. The spec phrases this
  as "Accepted patches are auto-committed to the source repo"; the actual
  behavior is "whatever is dirty becomes the auto-commit."
- **Expected:** Commit should include only the files touched by the
  canonical patch (e.g. parse filenames from the diff and `git add <paths>`),
  or, more cheaply, assert `git status --porcelain` shows only the expected
  diff before staging. The `apply_patch` call in `_auto_commit`
  (`loop.py:326`) happens on the source workspace *after* a clean preflight
  snapshot was taken in `execute_patch`, but there's a window
  (verification → _auto_commit) where a concurrent filesystem change could
  slip in and be captured.
- **Impact:** Auto-committed episodes can silently carry payload the user
  didn't sanction. Hardest to notice because the commit message and events
  will look clean.
- **Proposed fix:** Before `git add -A`, either (a) diff-against-HEAD and
  only stage files the canonical patch claims to touch, or (b) compare
  `git status --porcelain` output to an expected file list derived from
  `teacher_response.candidate_patch` / `execution.canonical_patch`. Fallback:
  require `--intent-to-add` mode with strict path allowlist.

### BLOCKER — `outcome == "error"` episodes never emit `episode_completed` but ARE persisted; the imbalance masks dashboard counts
- **File:** `homunculus/orchestrator/loop.py:249-260`
- **Class:** (f) missing persistence on terminal outcome (partial)
- **Observed:** On `outcome == "error"`, `append_episode(episode)` is called
  (line 249) — so the episode record **is** in `episodes.jsonl` — but the
  terminal `episode_completed` event is conditionally skipped
  (`if outcome != "error":` at line 250). The `episode_failed` event is
  emitted inside the `except Exception` block (line 218-228), but ONLY from
  that path. A guardrail-blocked episode (`outcome == "blocked"` via the
  in-try branch at line 93) flows straight to the `append_episode` at line
  249 and DOES emit `episode_completed` because `outcome != "error"` — OK.
  However, the `except WorkspacePreflightError` path sets `outcome="blocked"`
  and ALSO emits `episode_completed` for this — inconsistent with the
  error-path behavior, and the downstream daemon uses terminal events to
  trigger suggestion archival (Task 9) and cycle counters.
- **Expected:** Every terminal state should emit exactly one terminal
  lifecycle event whose `type` is derivable (`episode_completed` or
  `episode_failed`). Consumers iterating events to count completed episodes
  will silently drop error-outcome episodes.
- **Impact:** Any metrics/autonomy consumer that counts
  `type == "episode_completed"` events (vs. iterating `episodes.jsonl`) will
  undercount errors. Pairs badly with Task 9 archival, which is event-driven
  in some code paths.
- **Proposed fix:** Always emit a terminal event. Either unify to
  `episode_completed` (with `outcome` and `error` fields) for every path, or
  keep two event types but guarantee exactly one fires for every terminal
  outcome, including `error`.

### SILENT-DROP — `OpenAICompatibleTeacher` has no retry, no backoff, no circuit breaker
- **File:** `homunculus/orchestrator/teacher.py:16-66`
- **Class:** (c) fail-open handling + (b) silent-drop on transient failures
- **Observed:** Any `URLError` (network blip) or `HTTPError` (e.g. 429/503)
  immediately raises `RuntimeError` and propagates up; `run_episode` catches
  it as a generic `Exception` at `loop.py:203` and the episode is marked
  `outcome="error"`, `failure_stage="plan"`. No retry on transient failures;
  no distinction between 5xx/429 (retryable) and 4xx (permanent). No
  `TeacherSettings` field for retry count or backoff.
- **Expected:** OpenAI-compatible clients typically implement at least
  `max_retries` with exponential backoff on transient codes (429/500/502/
  503/504). The `TeacherSettings` dataclass (`config.py:25-33`) only exposes
  `temperature`, `max_tokens`, `timeout_seconds` — no retry field, so the
  user's intent can't even be expressed in TOML.
- **Impact:** Under any real inference endpoint where transient 429s are
  common, every such flake burns a whole episode to the `error` outcome,
  inflating failure counters and poisoning curation.
- **Proposed fix:** Add `max_retries: int = 2`, `retry_backoff_seconds: float = 1.0`
  to `TeacherSettings`; wrap `urlopen` in a retry loop honoring `Retry-After`
  on 429 and exponential backoff on 5xx. Keep 4xx (except 408/429)
  non-retryable.

### SILENT-DROP — `LocalStudentRunner` crashes when `mlx-lm` subprocess is missing on PATH; no graceful degradation
- **File:** `homunculus/orchestrator/student.py:13-33`
- **Class:** (b) silent-drop / hard fail when an optional dep is absent
- **Observed:** If `generate_command[0]` (e.g. `mlx_lm.generate`) isn't on
  PATH, `subprocess.run` raises `FileNotFoundError` (NOT caught here), which
  propagates into `run_episode`'s generic `Exception` handler and marks the
  episode `error`. CLAUDE.md's "Dependencies" section says "Optional:
  `mlx-lm` for real local inference" — but the code treats it as required.
- **Expected:** Either (a) degrade to a `StudentResponse(text=None, raw={"reason": "mlx-lm missing"})`
  (mirroring `returncode != 0` path), or (b) explicitly document that
  `mlx-lm` is required for `LocalStudentRunner` and make the caller route
  around it via config / use `StaticStudent`.
- **Impact:** Users running `homunculus doctor` OK then `run-episode`
  without mlx-lm installed get an opaque `error` outcome with failure_stage
  = "plan" and a confusing `FileNotFoundError`.
- **Proposed fix:** Catch `FileNotFoundError` / `PermissionError` and return
  a `StudentResponse` with empty text + structured raw; log at warning level.

### SILENT-DROP — `EngramMemoryClient` has no offline fallback; any network failure blows up an episode
- **File:** `homunculus/memory_client/engram.py:23-38`
- **Class:** (b) silent-drop network partition
- **Observed:** `_request` raises `RuntimeError` on any `HTTPError`/
  `URLError`. Called from `loop.py:70` (`get_active_context`) — which is
  NOT wrapped in a per-stage try/except inside the try — the exception
  lands in the outer `except Exception` handler at `loop.py:203`, marking
  the episode `error`. One existing test (`FailingMemoryClient` in
  `tests/test_orchestrator.py:21-23`) validates exactly this behavior as
  if it were correct — test cements a fail-closed memory contract that has
  no business breaking episodes.
- **Expected:** The memory backend is an optional enrichment, not a
  gating dependency. CLAUDE.md's "recall" stage should degrade to an empty
  memories list when Engram is unreachable, with a logged warning.
- **Impact:** A 5-second Engram outage kills every in-flight episode with
  `failure_stage="recall"`; combined with the teacher no-retry issue, the
  system is fragile on a flaky LAN.
- **Proposed fix:** In `loop.py` around `get_active_context`, catch and
  log, then proceed with `memories = []`. Or: provide a
  `FallbackMemoryClient` that wraps Engram + InMemory and catches exceptions
  on the live path.

### POLISH — `LocalStudentRunner` does not propagate `subprocess.TimeoutExpired`
- **File:** `homunculus/orchestrator/student.py:23-30`
- **Class:** (d) / (h) — edge-case handling
- **Observed:** `subprocess.run(..., timeout=self.settings.timeout_seconds)`
  raises `TimeoutExpired` on timeout, which is NOT caught. Unlike the
  `returncode != 0` path that returns an empty-text `StudentResponse`, a
  timeout becomes an episode error.
- **Expected:** Match the returncode != 0 semantics: return
  `StudentResponse(text=None, raw={"reason": "timeout"})`.
- **Impact:** Minor — turns a student timeout into an episode-level error
  rather than a soft "no hint".
- **Proposed fix:** Wrap in `try/except TimeoutExpired`.

### POLISH — `MemoryContract` protocol in `base.py` is not used as a type enforcement surface
- **File:** `homunculus/memory_client/base.py:8-19`
- **Class:** (h) orphaned protocol (partial)
- **Observed:** `MemoryContract` is imported by `loop.py:8` and used in the
  `EpisodeOrchestrator.__init__` type hint, but neither `EngramMemoryClient`
  nor `InMemoryMemoryClient` declares `Protocol` conformance or inherits.
  The Protocol works structurally (both classes define all four methods
  compatibly). Signatures match, but there's no static check nor
  `@runtime_checkable`. This is *fine* today but easy to drift; an
  `isinstance` check would fail without `@runtime_checkable`.
- **Expected:** Either add `@runtime_checkable`, or drop the Protocol and
  use a concrete base class; decide on one contract.
- **Impact:** Low; a future refactor could add a fifth method to the
  protocol and nobody would notice.
- **Proposed fix:** Add `@runtime_checkable` decorator OR add an assertion
  `assert isinstance(memory_client, MemoryContract)` at `__init__`.

### POLISH — `commit_to_source` runs `git commit` even if there was an earlier unrelated staged change
- **File:** `homunculus/task_runner/runner.py:170-188`
- **Class:** (g) TOCTOU-like race with concurrent edits (related to BLOCKER above)
- **Observed:** `git status --porcelain` is checked (line 173) then
  `git add -A` then `git commit`. If a file changes between those steps, it
  is included. Not concurrent-safe if a user runs another tool in the same
  source repo.
- **Expected:** The source workspace must already be under a per-workspace
  lock (the daemon has one); if run interactively (via CLI), document
  that the source repo must not be edited during an episode.
- **Impact:** Narrow — matters only under concurrent editing.
- **Proposed fix:** Document constraint; optionally take a `.lock` file
  briefly around the stage+commit pair.

### POLISH — `read_patch` runs `git diff --binary` against the worktree but `apply_patch` rejects empty/whitespace patches silently
- **File:** `homunculus/task_runner/runner.py:72-88` and `190-191`
- **Class:** (d) — hidden no-op branch
- **Observed:** `apply_patch` returns `False` if `patch.strip() == ""`; no
  exception, no log. `execute_patch` then proceeds to verification on the
  unpatched tree — which can plausibly "pass" (verification is run on the
  clean tree). The orchestrator would mark this `outcome="accepted"`, send
  it to curation, and `canonical_patch` is whatever `git diff` shows in an
  unmodified worktree (empty).
- **Expected:** An empty candidate_patch should not be accepted — either
  routed to `outcome="error"` or explicitly flagged as "no-op". Curation
  MUST NOT ingest empty diffs.
- **Impact:** Empty-patch episodes can pollute SFT datasets as "successful"
  empty diffs. Mitigated slightly because `dataset_builder` likely filters,
  but the orchestrator has no explicit check.
- **Proposed fix:** In `loop.py` execute branch, if
  `execution.canonical_patch` is empty AND `teacher_response.candidate_patch`
  was non-empty, set `outcome="error"` with `error_type="EmptyPatch"`. Or
  guard in `dataset_builder`.

### POLISH — `_auto_commit` logs but does NOT set `error_message` on the episode when apply_patch or commit fails
- **File:** `homunculus/orchestrator/loop.py:310-359`
- **Class:** (f) partial observability loss on terminal outcome
- **Observed:** When `_auto_commit` fails (apply or commit), it returns
  `None` and logs via `logger.error`, but the episode is still marked
  `outcome="accepted"` (it was accepted during verification) and
  `commit_sha=None`. No failure trace in `error_type`/`error_message` on
  the episode itself — you have to grep the log.
- **Expected:** Either (a) consider this a degraded-but-accepted outcome
  (current behavior, but annotate the record with something like
  `auto_commit_status="failed: <reason>"`), or (b) append a dedicated
  `auto_commit_failed` event so downstream analysis can see the split.
- **Impact:** Artifact/log mismatch — `commit_sha=None` on an "accepted"
  episode could mean "disabled" or "failed"; callers can't tell.
- **Proposed fix:** Append an `auto_commit_failed` event in the `except`
  branches of `_auto_commit`, carrying `error_type`/`error_message`.

### POLISH — `OpenAICompatibleTeacher` does not log the raw payload on parse failure
- **File:** `homunculus/orchestrator/teacher.py:58-66`
- **Class:** debuggability
- **Observed:** If `_extract_json` raises, the raw response body is lost
  because the `RuntimeError` only carries the exception message. Makes it
  very hard to diagnose "the teacher returned markdown instead of JSON".
- **Expected:** Log the raw content at debug level before raising (or
  attach to the exception).
- **Impact:** Low — just debug pain.
- **Proposed fix:** `logger.debug("teacher raw content: %s", content)` before
  calling `_extract_json`.

## Flows Traced

- **Episode happy path** (`assess → preflight → recall → plan → guardrails-pass → execute → (auto-commit) → reflect → curate`): **PASS**. Each
  stage emits a named event (`assess`, `preflight`, `recall`, `plan`,
  `execute`, `reflect`, `curate`, plus terminal `episode_completed`). On
  acceptance with `auto_commit_on_accept=True`, `_auto_commit` fires an
  additional `auto_commit` event with the SHA, and the SHA is persisted
  on the EpisodeRecord (`commit_sha` field, models.py:171). `tests/test_auto_commit.py`
  covers the `commit_to_source` primitive; `tests/test_orchestrator.py::AutoCommitWiringTests`
  covers the end-to-end orchestrator wiring.

- **Episode error path** (`except Exception` at `loop.py:203-228`):
  **DEFECTS** — see BLOCKER on outcome=="error" event asymmetry above.
  Otherwise `failure_stage`, `error_type`, `error_message` are correctly
  populated; memory store is best-effort wrapped. The `except WorkspacePreflightError`
  branch correctly produces `outcome="blocked"` with
  `failure_stage="preflight"`. Dataset builder is NOT called on error/blocked
  paths (good — curation only runs on the execute branch at line 178).

- **Auto-commit path** (`loop.py:126-136` → `_auto_commit` at 310-359):
  **DEFECTS** — the `_auto_commit` helper is correctly structured
  (returns None on failure, logs, appends `auto_commit` event on success),
  but the underlying `commit_to_source` uses `git add -A` which is broader
  than intended (BLOCKER above). Commit message is derived from task.prompt
  first-line; signature-quality fine.

- **Memory offline fallback**: **DEFECTS** — no fallback wired. `EngramMemoryClient._request`
  raises on any network failure, which propagates to the generic episode-
  error handler. `InMemoryMemoryClient` is only used directly in tests
  (`tests/test_orchestrator.py:21`), not as a runtime fallback. This matches
  SILENT-DROP finding above.

## Test Coverage Gaps

- No test for `OpenAICompatibleTeacher` parsing paths — `_extract_content`
  list-format fallback, `_extract_json` brace-bracket recovery, or
  `_validate_payload` rejections. `StaticTeacher` is always used, so this
  production code is unexercised.
- No test for `LocalStudentRunner` — `StaticStudent` covers the contract,
  but the subprocess wrapper has zero coverage (missing-binary, timeout,
  returncode != 0 paths).
- No test for `EngramMemoryClient._request` — no httpserver-style stub, no
  coverage for the HTTPError / URLError branches, bearer-token header,
  endpoint URL construction, `results`/`items`/list-root response shape
  handling.
- No test asserts that `_auto_commit` failure leaves `commit_sha=None`
  while keeping `outcome="accepted"` (current behavior is partially tested
  only in the happy-path branch).
- No test asserts that `commit_to_source` stages ONLY the expected patch
  files (current tests only assert "a commit exists"). The BLOCKER above
  would not be caught by any current test.
- No test for `execute_patch` with an empty patch string (empty-patch
  curation hazard noted above).
- No test for `run_verification` `timeout_seconds` behavior, nor for
  `shell=True` quoting hazards (currently relied upon at runner.py:98).
- No test for `_remove_worktree` failure modes (e.g., when the worktree
  was already manually removed — `check=False` masks this).
- No test for `_attempt_index` / `_failure_count` with thousands of episodes
  (both do full scans of `episodes.jsonl` each call; O(n) per episode =
  O(n²) over history, not a correctness bug but a latent perf trap).
- `MemoryContract` protocol conformance is not asserted at test time for
  either implementation.
