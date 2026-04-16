---
status: Complete
wave: 2
agent: data-analytics-engineer
---

# Plan 02-05 Summary: Comparative Introspection Mode

## Status: Complete

## Files Created
- `homunculus/introspection/comparative.py`

## Files Modified
- `homunculus/introspection/__init__.py` (export ComparativeMode)

## Capabilities
- **Episode grouping by comparison_group**: `_group_by_comparison()` uses defaultdict to group episodes, skipping those without a comparison_group
- **Winner vs loser identification**: `_has_comparison_pair()` checks for at least one accepted episode and one non-accepted episode
- **Patch analysis**: `_analyze_patch()` extracts patch_lines, additions, deletions, plan_steps, attempt index, and failure_stage
- **Patch comparison**: `_compare_patches()` generates insights about size differences, plan complexity, and attempt timing
- **Group analysis**: `_analyze_group()` separates winners/losers, creates stats findings, and compares first winner vs first loser
- **Pattern aggregation**: `_aggregate_patterns()` calculates averages across all winners/losers and identifies size patterns, plan patterns, and dominant failure stages
- **Graceful handling**: Returns helpful recommendations when no comparison_group exists or no comparable pairs exist
- **Actionable recommendations**: Generates specific advice based on patterns (e.g., "Winning patches tend to be smaller")

## Verification Results
| Command | Result |
|---------|--------|
| python -m unittest discover | 26 passed, 0 failed |
| Protocol compliance check | OK |
