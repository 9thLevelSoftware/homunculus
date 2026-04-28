---
tracker:
  kind: linear
  endpoint: https://api.linear.app/graphql
  api_key: "$LINEAR_API_KEY"
  project_slug: homunculus-autonomy-14b2016c004f
  label: symphony
  active_states:
    - Todo
    - In Progress
    - Rework
    - Validating
    - Merging
  terminal_states:
    - Done
    - Closed
    - Canceled
    - Cancelled
    - Duplicate
polling:
  interval_ms: 30000
workspace:
  root: runtime/symphony_workspaces
hooks:
  timeout_ms: 60000
agent:
  max_concurrent_agents: 1
  max_turns: 20
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    validating: 1
    merging: 1
codex:
  command: codex --profile homunculus-local app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000
homunculus:
  config_path: homunculus.toml
  source_workspace: .
  base_branch: master
  branch_prefix: codex/
  runner: homunculus
  fallback_runner: homunculus
  auto_merge: true
  artifact_curation: true
  verification_workspace: self
  in_progress_state: In Progress
  failed_state: Rework
  done_state: Done
  merge_gates:
    - python -m homunculus.cli harness-check --strict
    - python -m unittest discover -q
---
You are working on Linear issue {{ issue.identifier }}.

Title: {{ issue.title }}
State: {{ issue.state }}
URL: {{ issue.url }}
Labels: {{ issue.labels }}

Description:
{{ issue.description }}

Operate autonomously in the provided workspace only. Keep changes focused on the
issue acceptance criteria, run the configured validation, and leave durable
evidence in the Homunculus artifacts. Do not ask for human follow-up unless
required credentials, tools, or permissions are missing.
