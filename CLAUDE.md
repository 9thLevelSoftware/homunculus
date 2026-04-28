# CLAUDE.md

Claude Code should follow `AGENTS.md`.

This file is intentionally a pointer, not a duplicate manual. The repository's
agent-facing source of truth is:

- `AGENTS.md` for the short entry map
- `docs/index.md` for the documentation catalog
- `docs/harness-engineering.md` for the current agent-first engineering standard
- `docs/architecture.md` for implemented system behavior
- `docs/operator-guide.md` for operating commands and runbooks

Run these checks before considering code changes complete:

```powershell
python -m homunculus.cli harness-check --strict
python -m unittest discover -q
```
