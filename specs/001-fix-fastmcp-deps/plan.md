# Implementation Plan: Fix FastMCP Dependencies Error

**Branch**: `001-fix-fastmcp-deps` | **Date**: 2026-01-18 | **Spec**: /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/001-fix-fastmcp-deps/spec.md
**Input**: Feature specification from /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/001-fix-fastmcp-deps/spec.md

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Fix MCP server startup by aligning FastMCP initialization with supported
constructor arguments and adding regression coverage for successful startup and
clear error messaging.

## Technical Context

**Language/Version**: Python 3.10+ (repo targets >=3.10)  
**Primary Dependencies**: fastmcp, neo4j driver, pydantic, tiktoken  
**Storage**: Neo4j database (external)  
**Testing**: pytest, pytest-asyncio  
**Target Platform**: Linux server/container runtime  
**Project Type**: single  
**Performance Goals**: Maintain existing startup time expectations (no new targets)  
**Constraints**: No tool/resource identifier changes; supported FastMCP constructor only  
**Scale/Scope**: Single MCP server startup path and tool metadata exposure  

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Code quality gates: linting, formatting, and static analysis planned.
- Testing standards: success/error/boundary tests and contract tests planned.
- MCP conformity: tool/resource naming, schemas, and error handling aligned.

## Project Structure

### Documentation (this feature)

```text
/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/001-fix-fastmcp-deps/
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

- Confirm the supported FastMCP constructor signature and the recommended
  approach to dependency injection.
- Identify the safest compatibility path to preserve MCP tool contracts during
  startup changes.

### Findings

- Decision: Align FastMCP initialization with the supported constructor
  signature and remove unsupported arguments.
  Rationale: Eliminates startup crash while preserving current tool/resource
  registration.
  Alternatives considered: Pin an older FastMCP version, wrap constructor in
  compatibility shim.

- Decision: Add a regression test that exercises server startup and tool
  metadata exposure using the supported FastMCP API.
  Rationale: Prevents recurrence of startup regressions and validates MCP
  contract stability.
  Alternatives considered: Manual smoke test only.

## Phase 1: Design & Contracts

### Data Model

See /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/001-fix-fastmcp-deps/data-model.md
for modeled startup configuration and error reporting entities.

### Contracts

See /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/001-fix-fastmcp-deps/contracts/
for MCP server readiness and tool metadata contracts.

### Quickstart

See /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/001-fix-fastmcp-deps/quickstart.md
for validation steps.

### Agent Context Update

Run: /Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/servers/mcp-neo4j-cypher/.specify/scripts/bash/update-agent-context.sh codex

### Constitution Check (Post-Design)

- Code quality gates: Covered by plan tasks and existing linting.
- Testing standards: Regression and boundary tests planned.
- MCP conformity: Contracts validated and identifiers preserved.

## Phase 2: Planning

Phase 2 tasks are captured in tasks.md by `/speckit.tasks`.
