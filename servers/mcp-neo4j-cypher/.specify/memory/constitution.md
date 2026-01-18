<!--
Sync Impact Report
- Version change: 1.0.0 -> 2.0.0
- Modified principles: I. Safe-By-Default Database Access -> I. Code Quality Is
  Non-Negotiable; II. Deterministic Tool Contracts -> II. Testing Standards Are
  Mandatory; III. Bounded Execution And Responses -> III. MCP Standard Conformity;
  IV. Schema-Guided Interaction -> (removed); V. Reliability Through Tests And
  Reviews -> (removed)
- Added sections: None
- Removed sections: None
- Templates requiring updates: ✅ .specify/templates/plan-template.md, ✅ .specify/templates/spec-template.md, ✅ .specify/templates/tasks-template.md
- Follow-up TODOs: None
-->
# Neo4j MCP Cypher Server Constitution

## Core Principles

### I. Code Quality Is Non-Negotiable
All code changes MUST pass linting, formatting, and static analysis gates, and MUST
maintain clear, self-documenting structure. Public-facing functions and tools MUST
include concise docstrings and explicit error handling with actionable context.

### II. Testing Standards Are Mandatory
Behavior changes MUST include automated tests covering success paths, error paths,
and configuration boundaries. Bug fixes MUST include a regression test, and any
changes to tool contracts MUST include contract-level tests.

### III. MCP Standard Conformity
All tools, prompts, and resources MUST conform to MCP specifications for naming,
schema, error reporting, and response serialization. Changes MUST preserve backward
compatibility unless a major version bump is declared with migration guidance.

## Operational Constraints

- Supported runtime configuration MUST be documented in `README.md` with defaults.
- Secrets (credentials, tokens) MUST never be logged or surfaced in tool responses.
- Neo4j connectivity settings MUST be sourced from environment variables or
  explicit CLI flags; avoid hard-coded defaults in code paths.
- Resource endpoints MUST remain read-only and safe to call in any environment.

## Development Workflow

- Every change MUST include an updated spec or task reference when behavior shifts.
- PRs MUST include tests when touching behavior, contracts, or configuration.
- Linting, formatting, and type checks MUST pass for all merges to the main branch.
- MCP conformance MUST be reviewed before merge when touching tools or resources.

## Governance
This constitution is the source of truth for delivery and review practices.
Amendments require updating this file, recording rationale in the PR, and aligning
templates and guidance docs with any new rules. Versioning follows semantic
versioning: MAJOR for breaking governance or contract changes, MINOR for new
principles or requirements, PATCH for clarifications. Compliance is reviewed
during planning and before merge, with violations documented in the plan’s
Constitution Check section.

**Version**: 2.0.0 | **Ratified**: 2026-01-18 | **Last Amended**: 2026-01-18
