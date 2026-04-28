# Setup and Configuration

This document covers installation, config shape, and launch checks.

## Install

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Optional production dependencies:

- `mlx-lm` for local student inference and LoRA training
- toolchains required by workspace verification commands
- a teacher endpoint exposed through an OpenAI-compatible chat API
- an Engram-compatible memory server

## Environment

The example config expects:

```powershell
$env:OPENAI_API_KEY = "..."
$env:ENGRAM_MCP_BEARER_TOKEN = "..."
```

For local Ollama-style endpoints, `OPENAI_API_KEY` can be any non-empty value if
the endpoint ignores bearer auth.

## Config File

Copy the example and edit local endpoint details:

```powershell
Copy-Item homunculus.example.toml homunculus.toml
```

### Teacher

```toml
[teacher]
provider = "openai-compatible"
model = "gpt-5-mini"
base_url = "https://api.openai.com/v1"
endpoint = "/chat/completions"
api_key_env = "OPENAI_API_KEY"
temperature = 0.0
max_tokens = 4000
timeout_seconds = 60
```

Teacher output must decode to JSON with `plan`, `candidate_patch`, and
`rationale`.

### Student

```toml
[student]
model_id = "Qwen/Qwen2.5-Coder-3B-Instruct"
generate_command = ["python", "-m", "mlx_lm.generate"]
train_command = ["python", "-m", "mlx_lm.lora"]
adapter_root = "models/adapters"
```

`generate_command` and `train_command` are subprocess argv lists. Set timeouts
high enough for the local machine.

### Memory

```toml
[memory]
base_url = "http://127.0.0.1:4200"
search_endpoint = "/search"
store_endpoint = "/store"
bearer_token_env = "ENGRAM_MCP_BEARER_TOKEN"
timeout_seconds = 10
```

Search results must map to records with `id`, `category`, `content`, and
optional `metadata`.

### Autonomous Defaults

```toml
[daemon]
enabled = true
cycle_interval_minutes = 480
max_episodes_per_cycle = 5
suggestions_dir = "suggestions"
target_workspace = "self"
auto_commit_on_accept = true

[evolution]
auto_promote = true
auto_apply = true
auto_train_after_samples = 50
auto_merge_after_loras = 5
rollback_on_degradation = true
```

These are the expected harness defaults. Accepted episodes commit automatically
after verification. Candidate promotion is automated after metric gates pass.

### Thresholds And Promotion

```toml
[thresholds]
train_after_samples = 100
train_after_days = 7
max_self_generated_ratio = 0.5
min_eval_success_delta = 0.01
failure_growth_threshold = 2

[promotion]
allow_zero_canary_regressions = true
min_task_success_delta = 0.01
max_tool_misuse_increase = 0.0
max_retry_increase = 0.0
```

Promotion remains metric-gated even though it has no human approval flag.

### Paths

```toml
[paths]
root = "."
traces_dir = "traces"
datasets_dir = "datasets"
models_dir = "models"
runtime_dir = "runtime"
seed_sft_path = "datasets/seed/sft_seed.jsonl"
seed_dpo_path = "datasets/seed/dpo_seed.jsonl"
```

### Workspaces

```toml
[workspaces.self]
path = "."

[[workspaces.self.verification_commands]]
name = "unit-tests"
kind = "test"
command = "python -m unittest discover -s tests -v"
timeout_seconds = 120
```

Workspace rules:

- workspaces must be git repos
- source must be clean before episode execution
- commands run in the worktree during episodes
- commands run in source during `apply-episode`
- use `kind = "test"` for tests and other values, such as `lint`, for checks

### Guardrails

```toml
[[guardrails.block_patterns]]
pattern = "rm\\s+-rf"
message = "Destructive recursive delete is blocked."
```

Block rules prevent automatic execution. Warn rules are persisted as advisory
signals.

## Initialize And Validate

```powershell
python -m homunculus.cli init-artifacts --config homunculus.toml
python -m homunculus.cli harness-check --strict
python -m unittest discover -q
python -m homunculus.cli doctor --config homunculus.toml
python -m homunculus.cli autonomy-preflight --config homunculus.toml
```

`doctor` checks git, auth env vars, `mlx_lm`, writable artifact dirs, workspace
cleanliness, and Engram reachability. `autonomy-preflight` also checks tests,
stale worktrees, teacher reachability, and task readiness.

## Common Mistakes

- Missing auth env: set the configured env vars or adjust config.
- Dirty source repo: clean it before running episodes or the daemon.
- Empty seed data: SFT snapshot materialization requires seed and holdout data.
- Weak verification commands: autonomous commits are only as good as the checks.
- No remote: CI workflow files are present, but local autonomy does not require a
  GitHub remote.
