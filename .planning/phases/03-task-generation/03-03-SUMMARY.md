---
status: Complete
wave: 2
agent: engineering-ai-engineer
---

# Plan 03-03 Summary: Suggestion Resonance Scanner

## Status: Complete

## Files Modified
- `homunculus/suggestions.py` — Added resonance scoring methods
- `tests/test_suggestions.py` — 8 new test cases

## Capabilities
- **RESONANCE_KEYWORDS**: 10 keyword categories covering all introspection modes
  - error, testing, async, performance, security, documentation, refactor, patching, planning, lifecycle
- **_extract_keywords()**: Extracts category matches from text using substring matching
- **score_resonance()**: Jaccard similarity with decay weighting (1.0 → 0.4 over results)
- **read_pending_with_resonance()**: Boosts suggestion priorities by resonance, clamps to [0, 1]

## Keyword Categories
| Category | Keywords |
|----------|----------|
| error | error, exception, handling, try, catch, raise, fail, retry |
| testing | test, coverage, assert, unittest, pytest, mock, gap, suite |
| async | async, await, concurrent, parallel, thread, coroutine |
| performance | performance, speed, optimize, cache, fast, slow, memory |
| security | security, auth, permission, token, secret, credential |
| documentation | doc, readme, comment, docstring, todo |
| refactor | refactor, clean, simplify, extract, rename, consolidate |
| patching | patch, diff, change, modify, edit, fix |
| planning | plan, step, approach, strategy, design |
| lifecycle | execute, reflect, curate, assess, preflight |

## Verification Results
| Command | Result |
|---------|--------|
| python -m unittest tests.test_suggestions -v | All tests passed (8 new) |
| RESONANCE_KEYWORDS check | 10 categories |
