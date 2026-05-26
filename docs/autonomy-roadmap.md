# Autonomous Self-Improvement Roadmap

Generated: 2026-05-26
Status: reference roadmap for autonomy hardening and continuous self-improvement

This roadmap translates the autonomy review findings into sequenced engineering work. It is intended to be referenced by future implementation tasks, planning sessions, and acceptance reviews.

## Research Insight Map

| ID | Research finding addressed |
| --- | --- |
| R1 | Training/evaluation/promotion is not daemon-owned. |
| R2 | Candidate evaluation trusts external or weak metrics. |
| R3 | Promotion gates lack absolute quality floors. |
| R4 | Dataset curation equates “tests passed” with “good training data.” |
| R5 | Bootstrap seed data and training readiness are weak. |
| R6 | Auto-commit/source mutation is not transactional. |
| R7 | Accepted outcome can be recorded even when source commit fails. |
| R8 | Empty verification command lists can pass vacuously. |
| R9 | Guardrails are regex-only and lack structural patch policy. |
| R10 | Preflight/watchdog/rollback are mostly advisory. |
| R11 | Crashed `in_progress` queue tasks can be lost. |
| R12 | Storage is append-heavy, unindexed, and partially non-atomic. |
| R13 | Memory/context scaling is brittle. |
| R14 | Coverage/reporting schema mismatch hides quality regressions. |
| R15 | Comparative learning is mostly unwired. |
| R16 | Merge validation, active model adoption, and rollback semantics are incomplete. |
| R17 | Generated self-improvement tasks are too vague. |
| R18 | Symphony machine commits can fail acceptance signature rules. |

## Phase 0 — Stabilize Unsafe Runtime Behaviors

Estimated timeline: 1–2 weeks  
Priority: critical foundation

### Objectives

- Prevent unsafe or misleading source mutation.
- Ensure autonomous runs fail closed when verification is absent or startup health is poor.
- Fix known reporting/signature defects that can invalidate autonomy evidence.

### Core Deliverables

1. Transactional auto-commit path:
   - Capture source HEAD/status before applying patch.
   - Apply patch to source.
   - Re-run source verification.
   - Commit only after source verification passes.
   - Roll back with `reset --hard <pre_head>` and `clean -fd` on any failure.
2. Outcome model refinement:
   - Distinguish `verified_in_worktree`, `source_applied`, `source_verified`, `committed`, and `accepted_for_training` states.
   - Exclude uncommitted or partially applied episodes from training data.
3. Verification fail-closed behavior:
   - Reject autonomous workspaces with empty `verification_commands`.
   - Add `doctor` and `autonomy-preflight` checks for non-empty verification suites.
4. Coverage metric schema fix:
   - Standardize on `coverage_percent`.
   - Make missing coverage data visible in reports and acceptance.
5. Symphony commit signature fix:
   - Add `Episode-ID`, `Task-ID`, or `Symphony-Run-ID` footers to all machine commits.
   - Update acceptance SC6 to recognize approved machine signatures.
6. Merge-worker crash fix:
   - Ensure merge worker imports or creates `MergeResult` correctly on exceptions.

### Required Resources

- One backend/runtime engineer.
- Existing unit test suite.
- Temporary git repositories for task-runner and orchestrator tests.

### Critical Dependencies

- Current `TaskRunner`, `EpisodeOrchestrator`, `ArtifactStore`, and acceptance tests.
- Git availability in test environments.

### Risk Mitigation

- Add regression tests for commit failure rollback, source verification failure rollback, empty verification rejection, Symphony signature acceptance, and coverage metric extraction.
- Keep manual `apply-episode` recovery intact during transition.

### Success Criteria

- Source workspace remains clean after simulated auto-commit failure.
- No episode enters SFT/DPO unless source commit succeeded or explicit manual recovery succeeded.
- Empty verification suites fail preflight.
- Coverage appears correctly in `autonomy-report`.
- Symphony unattended commits no longer fail SC6.

Research alignment: R6, R7, R8, R14, R18.

## Phase 1 — Enforce Runtime Circuit Breakers and Queue Resilience

Estimated timeline: 2–3 weeks  
Priority: critical for unattended operation

### Objectives

- Ensure daemon startup and long-running execution are recoverable.
- Prevent silent task loss after crashes.
- Convert watchdog from passive observer into configurable safety controller.

### Core Deliverables

1. Daemon preflight enforcement:
   - Continuous mode runs `run_preflight()` before first cycle.
   - Default behavior blocks startup on failed preflight.
   - Add explicit override flag for development only.
2. Queue leasing and recovery:
   - Add `lease_owner`, `lease_started_at`, `heartbeat_at`, and `max_attempts` to queue entries.
   - Requeue or fail stale `in_progress` tasks on startup.
   - Preserve attempt count and last error.
3. Watchdog policy actions:
   - Add configurable actions: pause daemon, disable auto-commit, disable training/evolution, generate recovery task, enter safe-idle mode.
   - Surface active flags in daemon result and autonomy report.
4. Queue backpressure:
   - Enforce max queue size.
   - Honor `GeneratedTask.expires_at`.
   - Add source quotas for introspection versus suggestion tasks.
   - Compact completed and expired queue entries.

### Required Resources

- One backend engineer.
- One QA/test engineer for crash/restart simulation.

### Critical Dependencies

- Phase 0 outcome semantics.
- Existing `ArtifactStore` queue APIs.
- Existing watchdog model.

### Risk Mitigation

- Introduce new queue fields with backward-compatible deserialization.
- Add migration-tolerant readers for old queue entries.
- Run repeated crash simulation tests against temporary repos.

### Success Criteria

- A daemon crash after marking `in_progress` does not permanently lose work.
- Continuous daemon refuses to start with dirty workspace, failing tests, stale worktrees, or empty verification.
- Watchdog threshold breach changes daemon behavior according to policy.
- Queue remains bounded during repeated introspection task generation.

Research alignment: R10, R11, R17.

## Phase 2 — Close the Autonomous Training/Evaluation/Promotion Loop

Estimated timeline: 3–5 weeks  
Priority: core self-improvement capability

### Objectives

- Move training, evaluation, and promotion from manual CLI operation into daemon-managed state transitions.
- Replace external metrics files with computed evaluation evidence.
- Prevent weak candidates from becoming active.

### Core Deliverables

1. Training state machine:
   - Detect new eligible curated samples.
   - Materialize snapshot.
   - Train candidate.
   - Persist training start/success/failure events.
   - Retry or quarantine failed training runs.
2. Built-in evaluator:
   - Compute `EvaluationMetrics` internally from fixed canary coding tasks, held-out repo tasks, compile/test pass rate, retry counts, tool misuse checks, and memory-usefulness probes.
3. Promotion gate hardening:
   - Add minimum compile pass rate, minimum task success rate, maximum retry count, maximum tool misuse rate, and zero critical regression requirements.
   - Require fresh evaluation timestamp.
   - Require snapshot integrity check.
4. Daemon integration:
   - After episode curation, daemon checks whether training should run.
   - After training, daemon evaluates.
   - After evaluation, daemon promotes or rejects.
   - Promotion/rejection reasons become append-only events.
5. CLI remains manual override:
   - Keep `train-sft`, `evaluate-candidate`, and `promote-candidate`.
   - Mark metrics-file evaluation as manual/non-autonomous path.

### Required Resources

- One backend/runtime engineer.
- One ML/evaluation engineer.
- Local model runtime or simulated evaluation backend for tests.

### Critical Dependencies

- Phase 0 curation correctness.
- Phase 1 watchdog controls.
- Stable artifact registry and snapshot generation.

### Risk Mitigation

- Start with simulated evaluator in tests.
- Gate real training behind config.
- Add dry-run mode that produces planned state transitions without training.
- Never auto-promote without evaluator-produced metrics.

### Success Criteria

- A daemon cycle can autonomously progress from accepted episodes to curated samples, snapshot, trained candidate, evaluated candidate, and promoted/rejected candidate.
- No candidate can be promoted from user-supplied metrics in unattended mode.
- First candidate must meet absolute quality floors, not just beat a zero baseline.
- Promotion evidence is reproducible from stored artifacts.

Research alignment: R1, R2, R3.

## Phase 3 — Harden Dataset Curation and Bootstrap Readiness

Estimated timeline: 3–5 weeks  
Priority: prevent drift and enable fresh installs

### Objectives

- Prevent low-quality accepted patches from contaminating training data.
- Provide a trusted bootstrap path when seed datasets are empty.
- Protect valid/test splits from leakage or self-generated drift.

### Core Deliverables

1. Curation confidence model:
   - Add `candidate`, `quarantined`, `approved_for_sft`, `approved_for_dpo`, and `rejected` sample states.
   - Persist curation decision evidence.
2. Quality filters:
   - Reject or quarantine empty/no-op patches, patches with no meaningful source changes, excessive deletion patches, protected-path changes, runtime artifact changes, weak verification evidence, and duplicate semantic patches.
3. Trusted bootstrap corpus:
   - Ship minimal SFT seed records.
   - Ship fixed valid/test holdouts.
   - Add bootstrap curriculum of small deterministic repo tasks.
   - Separate bootstrap ratio rules from normal drift-control rules.
4. Snapshot integrity:
   - Compose snapshot once.
   - Write to temporary directory.
   - Include per-file hashes/counts.
   - Atomically promote completed snapshot.
   - Validate snapshot before training.
5. Split leakage checks:
   - Ensure same task/prompt/diff family cannot leak across train/valid/test.
   - Track selected episode IDs and source provenance.

### Required Resources

- One ML/data engineer.
- One backend engineer.
- Curated seed examples.

### Critical Dependencies

- Phase 2 evaluator design.
- Existing `DatasetBuilder`, `ArtifactStore`, and snapshot APIs.

### Risk Mitigation

- Introduce quarantine without deleting historical data.
- Keep existing dataset files readable.
- Add strict validation only for new autonomous training snapshots.
- Use deterministic tests for split assignment and leakage.

### Success Criteria

- Fresh checkout can build a valid bootstrap snapshot without human-created runtime artifacts.
- Accepted patches are not automatically SFT-approved unless curation gates pass.
- Snapshot manifests include hashes, counts, selected IDs, and config hash.
- Training refuses partial or corrupt snapshots.

Research alignment: R4, R5, R12.

## Phase 4 — Structural Patch Safety and Self-Modification Policy

Estimated timeline: 2–4 weeks  
Priority: safe recursive self-modification

### Objectives

- Move beyond regex guardrails.
- Enforce mechanical safety boundaries before patch application.
- Make high-risk self-modifications explicit and auditable.

### Core Deliverables

1. Patch parser and policy engine:
   - Parse touched files, additions, deletions, binary changes, and renames.
   - Compute patch risk score.
2. Protected path policy:
   - Deny or require elevated mode for auth/secrets files, `.github/`, `.kilo/`, CI config, model registry, runtime artifacts, datasets, training/evolution code, and workflow config.
3. Change-size limits:
   - Max files changed.
   - Max added/deleted lines.
   - Max binary payload size.
   - Max protected-file count.
4. Secret and credential scanning:
   - Scan candidate patches before worktree apply.
   - Block common token/key patterns.
5. Risk-aware routing:
   - Low-risk patches can auto-commit.
   - Medium-risk patches require stronger verification.
   - High-risk patches are blocked or require explicit configured approval mode.

### Required Resources

- One security-minded backend engineer.
- Test fixtures with malicious/destructive patches.

### Critical Dependencies

- Phase 0 transactional mutation.
- Phase 3 curation confidence states.

### Risk Mitigation

- Start in report-only mode for policy findings.
- Then enforce on autonomous daemon path.
- Preserve manual recovery path for operators.

### Success Criteria

- Malicious/destructive patches are blocked before `git apply`.
- Runtime/model/dataset artifacts cannot be accidentally committed by normal episodes.
- Protected-path changes produce explicit risk evidence.
- Policy decisions are persisted in episode records/events.

Research alignment: R9, R6, R7.

## Phase 5 — Robust Evolution, Rollback, and Active Model Adoption

Estimated timeline: 4–6 weeks  
Priority: model drift containment

### Objectives

- Make model evolution safe, measurable, and reversible.
- Define exactly when a merged model becomes the active base.
- Wire rollback into real degradation signals.

### Core Deliverables

1. Explicit model lifecycle:
   - Support `trained`, `evaluated`, `promoted_adapter`, `merge_pending`, `merged`, `validated_merged_base`, `active_base`, `rolled_back`, and `rejected` states.
2. Real merge canaries:
   - Require non-empty canary suites when evolution is enabled.
   - Canaries must evaluate `{model_path}`.
   - Include coding, coherence, regression, and safety prompts.
3. Merged model adoption:
   - Decide and implement whether validated merges become active base immediately, candidate base pending soak, or require an additional promotion gate.
   - Persist active base pointer.
4. Rollback controller:
   - Trigger rollback on failed post-promotion eval, failed merge validation, canary regression, acceptance trend regression, or watchdog critical flag.
5. Merge candidate tracking:
   - Replace timestamp-based merge selection with explicit candidate merge membership fields: `merge_id`, `merge_status`, `merged_at`, and `merge_failed_reason`.

### Required Resources

- One ML engineer.
- One backend engineer.
- GPU/local inference environment.

### Critical Dependencies

- Phase 2 evaluator.
- Phase 3 trusted holdouts.
- Phase 1 watchdog policy.

### Risk Mitigation

- Initially disable auto-adoption of merged bases.
- Require two consecutive passing evaluations before active-base switch.
- Always keep previous active candidate/base pointer.

### Success Criteria

- A validated merge has clear adoption status.
- Failed/degraded models automatically roll back or are quarantined.
- Missing canaries fail evolution validation in autonomous mode.
- Merge candidates are never skipped or re-merged due to timestamp skew.

Research alignment: R16, R2, R3, R10.

## Phase 6 — Long-Run Scaling: Storage, Memory, and Observability

Estimated timeline: 4–8 weeks  
Priority: sustained autonomy

### Objectives

- Make traces, queue, registry, snapshots, and memory reliable over long unattended runs.
- Prevent context overflow and irrelevant recall.
- Improve observability for multi-day/week soaks.

### Core Deliverables

1. Durable storage backend:
   - Migrate critical state to SQLite or indexed/checksummed append logs.
   - Add indexes for task ID, episode ID, outcome, candidate ID, snapshot ID, and timestamp.
   - Add corruption quarantine.
2. Artifact compaction:
   - Compact old queue/history/introspection records.
   - Retain patch artifacts by policy.
   - Add summary rollups for reports.
3. Persistent memory cache:
   - Local fallback cache for Engram.
   - Store/retrieve recent failures, warnings, and successful decisions.
   - Degrade gracefully during Engram outage.
4. Token/context budgeting:
   - Limit recalled memories by token budget.
   - Summarize long memory records.
   - Rank by relevance, recency, and reliability.
5. Longitudinal drift dashboard:
   - Track task-family success, module-level failures, model generation performance, coverage trend, training sample quality, and rollback events.

### Required Resources

- One backend/data engineer.
- Optional observability engineer.
- Long-running soak environment.

### Critical Dependencies

- Phase 1 queue semantics.
- Phase 2/3 artifact schemas.
- Phase 5 model lifecycle states.

### Risk Mitigation

- Add read compatibility for old JSONL artifacts.
- Migrate incrementally with export/import tools.
- Keep append-only audit logs even if SQLite becomes the query source.

### Success Criteria

- Reports remain fast after thousands of episodes.
- Corrupt artifact rows do not break daemon startup.
- Memory backend outage does not crash episodes.
- Teacher prompts stay within configured context budget.
- Longitudinal trends expose slow degradation.

Research alignment: R12, R13, R11, R14.

## Phase 7 — Acceptance, Soak, and Autonomy Certification

Estimated timeline: 2–4 weeks for initial certification, then recurring

### Objectives

- Prove the system can operate unattended through staged autonomy milestones.
- Convert acceptance from a manually interpreted report into reproducible certification evidence.

### Core Deliverables

1. Smoke run:
   - One low-risk autonomous task.
   - Verify patch, commit, curation, report, and rollback readiness.
2. 24-hour soak:
   - Exercise daemon cycles, queue recovery, watchdog, training dry-run or simulated training, and reporting.
3. 7-day acceptance run:
   - Require stable uptime, self-directed tasks, at least one real train/evaluate/promote/merge path, passing tests, stable metrics, and no human commits.
4. Acceptance hardening:
   - Acceptance fails if required introspection modes are stale, coverage is missing while enabled, watchdog critical flags are active, or model lifecycle has unresolved failed promotion/merge states.
5. Evidence bundle:
   - Archive `runtime/`, `traces/`, `datasets/snapshots/`, `models/registry.json`, acceptance markdown, preflight/precheck/report JSON, and git commit log.

### Required Resources

- Stable VM or local runner.
- Local model endpoint.
- Optional Linear/Symphony credentials.
- Disk/compute monitoring.

### Critical Dependencies

- Phases 0–6 complete enough for unattended operation.
- Real verification and canary suites.

### Risk Mitigation

- Run staged soak gates before 7-day run.
- Auto-disable training/evolution on watchdog critical flags.
- Preserve rollback points and full audit evidence.

### Success Criteria

- 24-hour run completes without human correction.
- 7-day run passes acceptance with reproducible evidence.
- At least one autonomous model-improvement cycle completes: curated data → trained candidate → evaluated candidate → promoted/merged or rejected with evidence.
- No source corruption, lost tasks, or untracked dirty state occurs.

Research alignment: all insights, especially R1–R5, R10, R12, R16.

## Cross-Phase Milestones

| Milestone | Target phase | Evidence |
| --- | ---: | --- |
| Source mutation is transactional | Phase 0 | Auto-commit failure tests leave repo clean. |
| Empty verification cannot pass | Phase 0 | Preflight/runner tests fail closed. |
| Daemon survives crash without losing tasks | Phase 1 | Stale lease recovery test. |
| Watchdog can pause unsafe loops | Phase 1 | Repeated failure simulation. |
| Daemon trains/evaluates/promotes without manual CLI | Phase 2 | End-to-end daemon test. |
| Training data is curated, not merely accepted | Phase 3 | Quarantine/approval records. |
| Fresh install can bootstrap snapshot | Phase 3 | Empty-runtime bootstrap test. |
| Structural patch policy blocks unsafe changes | Phase 4 | Protected-path/security tests. |
| Validated merge has active/rollback semantics | Phase 5 | Model lifecycle test. |
| Long-run storage scales | Phase 6 | Thousands-of-record benchmark. |
| 7-day acceptance evidence passes | Phase 7 | Archived report and acceptance verdict. |

## Recommended Execution Order

1. Do not start with model training. First make source mutation and verification safe.
2. Close daemon safety gaps before increasing autonomy.
3. Only after transactional commits and queue recovery are in place, wire autonomous training.
4. Only after built-in evaluation exists, allow auto-promotion.
5. Only after rollback/adoption semantics exist, allow autonomous merge/application.
6. Run staged smoke/soak gates before claiming long-term autonomy.

This roadmap moves from immediate safety fixes to durable, evidence-backed, long-running self-improvement. It should be updated whenever architecture, acceptance rules, or autonomy defaults materially change.
