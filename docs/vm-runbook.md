# Ubuntu GPU VM Runbook

This runbook describes the intended local model host for unattended Symphony
runs.

## Provisioning

1. Install Ubuntu on the GPU VM.
2. Install NVIDIA drivers and verify `nvidia-smi`.
3. Install Git, Python 3.11+, and build tooling.
4. Install Ollama and pull the first coding model:

```bash
ollama pull qwen2.5-coder:14b-instruct-q4_K_M
ollama serve
```

5. Clone Homunculus and install it editable:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## Environment

Set secrets in the VM environment, not in repo files:

```bash
export LINEAR_API_KEY=...
export OPENAI_API_KEY=ollama-local
```

Configure Codex with a local provider/profile that points at:

```text
http://127.0.0.1:11434/v1
```

Use the profile name referenced by `WORKFLOW.md`: `homunculus-local`.

## Smoke

Run these before enabling unattended dispatch:

```bash
python -m homunculus.cli harness-check --strict
python -m unittest discover -q
python -m homunculus.cli symphony-check --workflow WORKFLOW.md
python -m homunculus.cli symphony-status --json
```

Then create a single low-risk Linear issue with the `symphony` label and run:

```bash
python -m homunculus.cli symphony-run --workflow WORKFLOW.md --once
```

Only move to continuous mode after the smoke run creates a branch, records a
run, validates gates, and either merges or records a clear retryable failure.

## Soak

Use the staged soak plan:

1. single issue smoke
2. 24-hour unattended run
3. 7-day acceptance run

At the end of each stage, archive:

- `runtime/symphony_state.json`
- `runtime/symphony_runs.jsonl`
- `runtime/symphony_logs/`
- `traces/`
- `python -m homunculus.cli autonomy-report --config homunculus.toml --json`
