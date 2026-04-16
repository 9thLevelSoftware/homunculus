---
status: Complete
wave: 2
agent: engineering-ai-engineer
---

# Plan 03-02 Summary: Task Generator

## Status: Complete

## Files Created
- `homunculus/task_generator/generator.py` — TaskGenerator class with mode-specific generators

## Files Modified
- `homunculus/task_generator/__init__.py` — Export TaskGenerator
- `tests/test_task_generator.py` — 47 new test cases

## Capabilities
- **TaskGenerator.generate_from_introspection()**: Main entry point, processes IntrospectionResult list
- **Mode-specific generators**:
  - `_generate_from_metrics()`: success_rate, error_rate, retry_rate, failure_concentration
  - `_generate_from_critique()`: pattern, weakness findings (skips strengths)
  - `_generate_from_coverage()`: total_coverage, low_coverage_files, todo_count, untested_modules
  - `_generate_from_comparative()`: size_pattern, plan_pattern, failure_stage_pattern
- **Defensive parsing**: `_extract_finding_field()`, `_infer_severity()` with multiple fallbacks
- **Priority calculation**: severity_score * mode_weight, clamped to [0, 1]
- **15 prompt templates**: Clear, actionable prompts with success criteria

## Key Decisions
- Mode weights: metrics=1.0, critique=0.9, coverage=0.7, comparative=0.6
- Severity scores: critical=0.95, high=0.8, warning=0.7, medium=0.5, low=0.3, info=0.2
- Only generates tasks for actionable findings (warning+ severity)
- Try/except blocks log and skip malformed findings

## Verification Results
| Command | Result |
|---------|--------|
| python -m unittest tests.test_task_generator -v | 47 tests passed |
| Import verification | OK |
