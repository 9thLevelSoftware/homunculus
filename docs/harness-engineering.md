# Harness Engineering Standard

This repo aligns to OpenAI's [Harness engineering: leveraging Codex in an
agent-first world](https://openai.com/index/harness-engineering/) by making the
repository itself the harness: agent-readable docs, executable checks, isolated
runtime feedback, and continuous cleanup.

## Principles

1. Humans steer; agents execute.
2. `AGENTS.md` stays short and points to deeper docs.
3. `docs/` is the source of truth for current behavior.
4. Runtime behavior must be mechanically checkable.
5. Architectural boundaries are enforced by tests and custom checks, not memory.
6. Autonomous throughput is acceptable only when validation is cheap and visible.
7. Cleanup is continuous, explicit, and tracked as first-class work.

## Repository Harness

The harness has four layers:

- **Map:** `AGENTS.md`, `CLAUDE.md`, and `docs/index.md` route agents to the
  right context without flooding the prompt.
- **Truth:** `docs/architecture.md`, `docs/operator-guide.md`, and
  `docs/setup-and-configuration.md` describe the implemented system.
- **Feedback:** verification commands, `doctor`, `autonomy-preflight`,
  `autonomy-report`, `autonomy-accept`, and `harness-check` expose health.
- **Runtime:** worktree-isolated episodes, append-only traces, model registry,
  introspection, task generation, and evolution form the autonomous loop.

## Autonomous Defaults

The standard operating posture is high-throughput and local-first:

- `daemon.auto_commit_on_accept = true`
- `evolution.auto_promote = true`
- `evolution.auto_apply = true`
- `daemon.target_workspace = "self"`

Accepted patches are verified in an isolated worktree before they are applied to
the source workspace and committed. Candidate models must still pass promotion
metrics, merge validation, and acceptance reporting.

## Enforcement

`python -m homunculus.cli harness-check --strict` must pass locally and in CI.
It validates:

- required map and docs files exist
- `AGENTS.md` stays under the line budget and links to the current docs
- current docs do not contain known stale manual-approval guidance
- autonomous defaults are explicit in config
- CI runs both harness checks and the unit suite

## GitHub And CI

This checkout has no configured git remote, so the implementation is
remote-optional. The repository includes GitHub Actions configuration for any
future remote, but local autonomy remains runnable with plain git, PowerShell,
and Python.

When a remote is added, the intended PR loop is:

1. run `harness-check`, unit tests, and `doctor`
2. open a short-lived PR from an agent branch
3. let CI and agent review feedback drive fixes
4. merge when checks pass, escalating only for product judgment or unsafe ops

## Cleanup Cadence

Treat stale docs, noisy tests, oversized files, repeated warning patterns, and
unindexed planning artifacts as harness debt. Prefer small cleanup tasks that
improve future agent legibility over large delayed rewrites.
