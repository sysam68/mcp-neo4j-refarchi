# Feature Specification: Fix FastMCP Dependencies Error

**Feature Branch**: `001-fix-fastmcp-deps`  
**Created**: 2026-01-18  
**Status**: Draft  
**Input**: User description: "Server fails to start due to an unexpected FastMCP
constructor argument."

## Clarifications

### Session 2026-01-18
- Q: Which FastMCP compatibility approach should we use? -> A: Support only the currently published FastMCP constructor (remove unsupported args).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start the server without runtime errors (Priority: P1)

Operators need the MCP server to start reliably so they can run Cypher queries
without startup crashes.

**Why this priority**: Server startup is the entry point for all usage.

**Independent Test**: Can be fully tested by starting the server with standard
configuration and verifying it reaches a ready state and exposes tools.

**Acceptance Scenarios**:

1. **Given** a valid configuration, **When** the operator starts the server,
   **Then** the server starts without runtime exceptions and reports readiness.
2. **Given** the server is running, **When** a client requests tool metadata,
   **Then** the tool list is returned successfully.

---

### User Story 2 - Receive actionable startup errors (Priority: P2)

Operators need clear guidance when the server cannot start due to incompatible
runtime settings.

**Why this priority**: Actionable errors reduce downtime and support burden.

**Independent Test**: Can be fully tested by starting the server with an
incompatible configuration and verifying a clear error is returned.

**Acceptance Scenarios**:

1. **Given** an incompatible runtime configuration, **When** the operator starts
   the server, **Then** the server exits with a concise error message describing
   the incompatibility and how to resolve it.

---

### Edge Cases

- What happens when the MCP runtime is incompatible with expected configuration?
- How does the system handle missing optional settings without crashing?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST start without runtime exceptions for supported
  configurations.
- **FR-002**: System MUST preserve existing tool/resource/prompt identifiers.
- **FR-003**: System MUST provide a concise, actionable error on incompatible
  startup configuration.
- **FR-004**: System MUST accept existing configuration options without
  behavioral regressions.
- **FR-005**: System MUST include a regression test that covers successful
  startup with a supported configuration.
- **FR-006**: System MUST use the supported FastMCP constructor signature and
  avoid unsupported arguments.

### Quality & MCP Requirements

- **QR-001**: Linting, formatting, and static analysis gates MUST pass without
  new violations.
- **QR-002**: Tests MUST cover success, error, and configuration boundary
  behaviors for startup.
- **QR-003**: MCP tool/resource contracts MUST remain unchanged and validated.

## Assumptions

- Operators have access to startup logs to read error messages.
- The server continues to support the published MCP runtime compatibility range.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can start the server successfully on the first attempt in
  smoke runs.
- **SC-002**: Startup smoke tests complete with zero runtime exceptions.
- **SC-003**: Tool and resource identifiers match the pre-fix published list.
- **SC-004**: Support requests related to this startup failure drop to zero after
  release.
