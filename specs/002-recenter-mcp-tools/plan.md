# Implementation Plan: Recenter MCP Tools

**Branch**: `002-recenter-mcp-tools` | **Date**: 2026-01-18 | **Spec**: /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/002-recenter-mcp-tools/spec.md
**Input**: Feature specification from /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/002-recenter-mcp-tools/spec.md

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Recenter MCP access around tools for labels, coreConcept, and schema snapshot by
removing dynamic resources, adding read-only tools, and publishing a reference
resource + prompt that explains usage.

## Technical Context

**Language/Version**: Python 3.10+ (repo targets >=3.10)
**Primary Dependencies**: fastmcp, neo4j driver, pydantic, tiktoken
**Storage**: Neo4j database (external)
**Testing**: pytest, pytest-asyncio
**Target Platform**: Linux server/container runtime
**Project Type**: single
**Performance Goals**: Preserve existing tool response times (no new targets)
**Constraints**: Read-only tools/resources, no auth, MCP naming/URI conformity
**Scale/Scope**: MCP server tools/resources/prompts and related documentation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Code quality gates: linting, formatting, and static analysis planned.
- Testing standards: success/error/boundary tests and contract tests planned.
- MCP conformity: tool/resource naming, schemas, and error handling aligned.

## Project Structure

### Documentation (this feature)

```text
/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/002-recenter-mcp-tools/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/servers/mcp-neo4j-cypher/
├── src/
│   └── mcp_neo4j_cypher/
├── tests/
│   ├── integration/
│   └── unit/
└── pyproject.toml
```

**Structure Decision**: Single Python service under
`/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/servers/mcp-neo4j-cypher/` with
unit and integration tests.

## Phase 0: Outline & Research

### Research Questions

- Confirm MCP tool/resource/prompt naming constraints and any update impacts on
  backward compatibility.
- Confirm JSON response shape expectations for `get_coreConcept` and label
  lookup resources.

### Findings

- Decision: Keep identifiers aligned with MCP naming/URI rules while removing
  dynamic data resources.
  Rationale: Ensures standard conformity and avoids exposing mutable resources.
  Alternatives considered: Retain legacy resources with deprecation notices.

- Decision: Define sanitized JSON output for coreConcept nodes and empty arrays
  for missing labels.
  Rationale: Keeps outputs stable and testable without leaking driver objects.
  Alternatives considered: Raw Neo4j serialization or ToolError on missing label.

## Phase 1: Design & Contracts

### Data Model

See /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/002-recenter-mcp-tools/data-model.md

### Contracts

See /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/002-recenter-mcp-tools/contracts/

### Quickstart

See /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/002-recenter-mcp-tools/quickstart.md

### Agent Context Update

Run: /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/servers/mcp-neo4j-cypher/.specify/scripts/bash/update-agent-context.sh codex

### Constitution Check (Post-Design)

- Code quality gates: linting, formatting, and static analysis remain required.
- Testing standards: contract + boundary test coverage planned for new tools.
- MCP conformity: updated identifiers, schemas, and error handling documented.

## Phase 2: Planning

Phase 2 tasks are captured in tasks.md by `/speckit.tasks`.
