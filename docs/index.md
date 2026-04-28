# Documentation Index

This directory is the repository-local source of truth for agents and operators.
`AGENTS.md` is only the entry map.

## Current Docs

- [Harness Engineering](harness-engineering.md) - agent-first operating standard,
  adapted from OpenAI's harness engineering article.
- [Architecture and Artifacts](architecture.md) - implemented runtime structure,
  lifecycle, persisted records, and safety boundaries.
- [Operator Guide](operator-guide.md) - daily commands, autonomous runs, reporting,
  recovery, and acceptance.
- [Setup and Configuration](setup-and-configuration.md) - TOML sections,
  environment variables, launch checks, and common setup mistakes.
- [Quality Score](quality-score.md) - current grades, gaps, and cleanup cadence.

## Planning Artifacts

`.planning/` contains historical plans, reviews, phase state, and audit logs.
Use it as evidence for why decisions were made. When current behavior conflicts
with `.planning/`, prefer the docs in this directory and update stale planning
summaries only when they are actively referenced by a current runbook.

`docs/superpowers/` is also historical planning/audit material. It may mention
removed gates or rejected designs because those files preserve prior review
context. Do not treat it as current operating guidance unless this index or an
operator runbook links to a specific file for the current phase.

## Freshness Rules

- Architecture docs must describe behavior that exists in code.
- Operator docs must use commands that work from the repository root.
- Config docs must match `homunculus.example.toml` and the dataclasses in
  `homunculus/config.py`.
- Any repeated agent instructions must be links, not copied blocks.
- `python -m homunculus.cli harness-check --strict` enforces the baseline.
