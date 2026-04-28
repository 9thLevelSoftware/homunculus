from __future__ import annotations

import json
from typing import Any, Protocol
from urllib import error, request

from .models import BlockerRef, IssueRecord, TrackerConfig


class TrackerError(RuntimeError):
    pass


class IssueTracker(Protocol):
    def fetch_candidate_issues(self) -> list[IssueRecord]:
        ...

    def fetch_issues_by_states(self, state_names: list[str]) -> list[IssueRecord]:
        ...

    def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> dict[str, str]:
        ...

    def update_issue_state(self, issue_id: str, state_name: str) -> None:
        ...


class LinearTracker:
    """Linear GraphQL adapter.

    Query construction is isolated here because Linear schema details drift over
    time. Tests exercise normalization and transport behavior without requiring
    a live Linear workspace.
    """

    def __init__(self, config: TrackerConfig, *, timeout_seconds: int = 30) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def fetch_candidate_issues(self) -> list[IssueRecord]:
        return self._fetch_issues_by_states(list(self.config.active_states), label=self.config.label)

    def fetch_issues_by_states(self, state_names: list[str]) -> list[IssueRecord]:
        return self._fetch_issues_by_states(state_names, label=None)

    def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> dict[str, str]:
        if not issue_ids:
            return {}
        data = self._graphql(
            """
            query IssueStates($ids: [ID!]) {
              issues(first: 100, filter: { id: { in: $ids } }) {
                nodes {
                  id
                  state { name }
                }
              }
            }
            """,
            {"ids": issue_ids},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        return {
            str(node.get("id")): str((node.get("state") or {}).get("name", ""))
            for node in nodes
            if node.get("id")
        }

    def update_issue_state(self, issue_id: str, state_name: str) -> None:
        state_id = self._find_state_id(state_name)
        self._graphql(
            """
            mutation UpdateIssueState($id: String!, $stateId: String!) {
              issueUpdate(id: $id, input: { stateId: $stateId }) {
                success
              }
            }
            """,
            {"id": issue_id, "stateId": state_id},
        )

    def _find_state_id(self, state_name: str) -> str:
        data = self._graphql(
            """
            query WorkflowStates {
              workflowStates(first: 250) {
                nodes { id name }
              }
            }
            """,
            {},
        )
        wanted = state_name.lower()
        for node in data.get("workflowStates", {}).get("nodes", []):
            if str(node.get("name", "")).lower() == wanted:
                return str(node["id"])
        raise TrackerError(f"Linear workflow state not found: {state_name}")

    def _fetch_issues_by_states(self, state_names: list[str], label: str | None) -> list[IssueRecord]:
        issues: list[IssueRecord] = []
        after: str | None = None
        while True:
            data = self._graphql(
                """
                query CandidateIssues(
                  $projectSlug: String!,
                  $states: [String!],
                  $after: String,
                  $label: String
                ) {
                  issues(
                    first: 50,
                    after: $after,
                    filter: {
                      project: { slugId: { eq: $projectSlug } },
                      state: { name: { in: $states } },
                      labels: { name: { eq: $label } }
                    }
                  ) {
                    nodes {
                      id
                      identifier
                      title
                      description
                      priority
                      url
                      branchName
                      createdAt
                      updatedAt
                      state { name }
                      labels { nodes { name } }
                      relations {
                        nodes {
                          type
                          relatedIssue {
                            id
                            identifier
                            createdAt
                            updatedAt
                            state { name }
                          }
                        }
                      }
                    }
                    pageInfo { hasNextPage endCursor }
                  }
                }
                """,
                {
                    "projectSlug": self.config.project_slug,
                    "states": state_names,
                    "after": after,
                    "label": label or self.config.label,
                },
            )
            issue_page = data.get("issues", {})
            issues.extend(self._normalize_issue(node) for node in issue_page.get("nodes", []))
            page_info = issue_page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
        return issues

    def _normalize_issue(self, node: dict[str, Any]) -> IssueRecord:
        labels = [
            str(label.get("name", "")).lower()
            for label in (node.get("labels") or {}).get("nodes", [])
            if label.get("name")
        ]
        blockers: list[BlockerRef] = []
        for relation in (node.get("relations") or {}).get("nodes", []):
            relation_type = str(relation.get("type", "")).lower()
            if "block" not in relation_type:
                continue
            related = relation.get("relatedIssue") or {}
            blockers.append(
                BlockerRef(
                    id=related.get("id"),
                    identifier=related.get("identifier"),
                    state=(related.get("state") or {}).get("name"),
                    created_at=related.get("createdAt"),
                    updated_at=related.get("updatedAt"),
                )
            )
        return IssueRecord(
            id=str(node.get("id", "")),
            identifier=str(node.get("identifier", "")),
            title=str(node.get("title", "")),
            description=node.get("description"),
            priority=node.get("priority"),
            state=str((node.get("state") or {}).get("name", "")),
            branch_name=node.get("branchName"),
            url=node.get("url"),
            labels=labels,
            blocked_by=blockers,
            created_at=node.get("createdAt"),
            updated_at=node.get("updatedAt"),
        )

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if not self.config.api_key:
            raise TrackerError("LINEAR_API_KEY is required for Linear tracker dispatch")
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = request.Request(
            self.config.endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": self.config.api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.URLError as exc:
            raise TrackerError(f"Linear transport failed: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TrackerError(f"Linear returned invalid JSON: {raw[:200]}") from exc
        if parsed.get("errors"):
            raise TrackerError(f"Linear GraphQL errors: {parsed['errors']}")
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise TrackerError("Linear GraphQL response missing data object")
        return data
