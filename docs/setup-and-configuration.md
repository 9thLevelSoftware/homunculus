# Setup and Configuration

This document covers how to install `homunculus`, how to wire it to your environment, and how to configure it safely for first launch.

## 1. Install the project

Create a virtual environment and install the package:

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Optional but usually required for production:

- install `mlx-lm` if you want local student inference and SFT to run for real
- install any toolchain needed by your workspace verification commands

## 2. Prepare external services

`homunculus` does not bundle its teacher or memory backends. You need both:

- a teacher model exposed through an OpenAI-compatible HTTP API
- an Engram server reachable over HTTP

Environment variables expected by the example config:

```powershell
$env:OPENAI_API_KEY = "..."
$env:ENGRAM_MCP_BEARER_TOKEN = "..."
```

If you change the env var names in config, the runtime will read those names instead.

## 3. Create a working config

Copy the example file:

```powershell
Copy-Item homunculus.example.toml homunculus.toml
```

Then edit `homunculus.toml`.

### Teacher section

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

Guidance:

- `base_url + endpoint` must form a valid POST target
- the runtime expects an OpenAI-style response envelope with `choices[0].message.content`
- content may be a plain string or a list of text parts
- the teacher output must decode to JSON with `plan`, `candidate_patch`, and `rationale`

### Student section

```toml
[student]
model_id = "Qwen/Qwen2.5-Coder-3B-Instruct"
generate_command = ["python", "-m", "mlx_lm.generate"]
train_command = ["python", "-m", "mlx_lm.lora"]
max_tokens = 800
batch_size = 1
grad_accumulation_steps = 8
prompt_masking = true
qlora = true
adapter_root = "models/adapters"
timeout_seconds = 60
train_timeout_seconds = 3600
```

Guidance:

- `generate_command` and `train_command` are executed as subprocess commands
- timeouts are enforced, so set them high enough for your machine
- `adapter_root` is where candidate adapter outputs are written

### Memory section

```toml
[memory]
base_url = "http://127.0.0.1:4200"
search_endpoint = "/search"
store_endpoint = "/store"
bearer_token_env = "ENGRAM_MCP_BEARER_TOKEN"
timeout_seconds = 10
```

Expected behavior:

- `search_endpoint` accepts `{ "query", "filters", "limit" }`
- `store_endpoint` accepts `{ "content", "category", "metadata" }`
- search results must map cleanly to memory records with `id`, `category`, `content`, and optional `metadata`

### Thresholds and promotion

These sections control training cadence and promotion gates.

```toml
[thresholds]
train_after_samples = 100
train_after_days = 7
max_self_generated_ratio = 0.5
min_eval_success_delta = 0.01
failure_growth_threshold = 2

[promotion]
require_human_approval = true
allow_zero_canary_regressions = true
min_task_success_delta = 0.01
max_tool_misuse_increase = 0.0
max_retry_increase = 0.0
```

Recommended launch defaults:

- keep `require_human_approval = true`
- keep `allow_zero_canary_regressions = true`
- keep `max_self_generated_ratio = 0.5` or lower

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

What these do:

- `traces_dir`: events, episodes, and patch artifacts
- `datasets_dir`: append-only SFT/DPO data plus snapshot materializations
- `models_dir`: adapter registry and adapter outputs
- `runtime_dir`: temporary linked worktrees for isolated episode execution

### DPO

The current launch-safe default is disabled:

```toml
[dpo]
enabled = false
min_successful_sft_promotions = 3
env = { PYTORCH_ENABLE_MPS_FALLBACK = "1" }
```

Leave this disabled unless you intentionally extend the runtime to support live DPO.

### Guardrails

Guardrail rules are regex-based. Example:

```toml
[[guardrails.block_patterns]]
pattern = "rm\\s+-rf"
message = "Destructive recursive delete is blocked."
```

Use block rules for actions that must never proceed automatically. Use warn rules for actions that are suspicious but not necessarily forbidden.

### Workspaces

Each workspace maps a logical name to one source repo plus the commands used to accept or reject a patch.

```toml
[workspaces.self]
path = "."

[[workspaces.self.verification_commands]]
name = "unit-tests"
kind = "test"
command = "python -m unittest discover -s tests -v"
timeout_seconds = 120
```

Rules:

- workspaces must be git repos
- the source repo must be clean before `run-episode`
- commands run in the isolated worktree during episode execution
- commands run in the source repo during `apply-episode`

Recommended command split:

- use `kind = "test"` for tests
- use any other `kind` such as `lint` for lint/format/static checks

### Canary commands

Canary commands are not fully wired into the CLI yet, but the config already carries the evaluation shape expected by the training manager.

## 4. Seed data

SFT snapshot generation requires both:

- approved self-generated `train` samples
- non-empty `valid` and `test` splits

It also expects a seed corpus at `seed_sft_path`.

The training snapshot logic enforces the self-generated ratio against that seed corpus. If there is no seed data, the snapshot will fail to materialize.

## 5. Initialize artifacts

Run:

```powershell
python -m homunculus.cli init-artifacts --config homunculus.toml
```

This creates the expected directory structure and empty append-only files.

## 6. Validate the environment

Run:

```powershell
python -m homunculus.cli doctor --config homunculus.toml
```

`doctor` checks:

- `git` availability
- teacher auth env presence
- Engram auth env presence
- `mlx_lm` importability
- writable artifact directories
- workspace cleanliness
- Engram reachability

Do not ignore a failing `doctor` result before launch.

## 7. First-launch checklist

Before you trust the system on a real codebase:

- `doctor` passes
- the workspace is clean
- the workspace verification commands are strong enough to reject bad patches
- the teacher endpoint returns structured JSON in the expected shape
- the seed SFT file exists and contains valid records
- you have a human review path for `apply-episode`
- you have a human review path for `promote-candidate`

## 8. Common setup mistakes

Teacher auth env missing:

- `doctor` will flag `teacher_auth_env`
- fix the env var or change `teacher.api_key_env`

Engram unreachable:

- `doctor` will flag `engram_reachable`
- fix `memory.base_url`, the endpoints, or auth

Workspace blocked:

- `run-episode` will not operate on a dirty repo
- commit, stash, or discard your own changes manually before running the agent

No training snapshot:

- you likely do not have seed data, holdout splits, or approved self-generated train samples yet
