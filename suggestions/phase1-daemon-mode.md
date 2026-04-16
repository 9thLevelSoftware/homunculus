# Add Continuous Daemon Mode

## Priority
HIGH

## What
Implement continuous daemon mode in homunculus/daemon.py that:
1. Runs on a configurable interval (read from config, default 8 hours)
2. Executes multiple episodes per cycle (up to max_episodes_per_cycle from config)
3. Persists daemon state to runtime/daemon_state.json between cycles
4. Handles SIGTERM/SIGINT gracefully (finish current episode, save state, exit)

## Why
This enables fully autonomous operation. Currently the daemon only supports --once mode.
Continuous mode is required for the agent to run unattended and improve itself over time.

## Success Criteria
- `python -m homunculus.daemon --config homunculus.toml` runs continuously
- Ctrl+C stops gracefully after current episode completes
- State persists across restarts
- Config interval is respected

## Hints
- Look at existing daemon.py structure
- Add DaemonSettings to config.py with cycle_interval_minutes, max_episodes_per_cycle
- Use signal module for SIGTERM/SIGINT handling
- State file should include: started_at, last_cycle_at, cycles_completed, total_episodes
