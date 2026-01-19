# Feature Specification: Recenter MCP Tools

**Feature Branch**: `002-recenter-mcp-tools`  
**Created**: 2026-01-18  
**Status**: Draft  
**Input**: User description: "Recenter MCP access around tools for labels, coreConcept, and schema snapshot; remove dynamic resources; add a reference resource and orientation prompt."

## Clarifications

### Session 2026-01-18

- Q: When `resource://neo4j/labels/{label}` is queried for a missing label, what
  should the response be? → A: Return an empty JSON array with no metadata.
- Q: What is the expected JSON shape for `get_coreConcept` results? → A: Return
  objects with `id`, `labels`, and `properties`.
- Q: Is authentication required for these MCP tools/resources? → A: No
  authentication required.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Discover labels and schema via tools (Priority: P1)

Operators and agents need a clear tool-based way to list labels and inspect the
schema without relying on dynamic resources.

**Why this priority**: This is the core interface change that must be reliable
for all downstream usage.

**Independent Test**: Can be fully tested by listing tools, calling the tools,
and confirming JSON responses and read-only behavior.

**Acceptance Scenarios**:

1. **Given** the server is running, **When** the client calls `tools/list`,
   **Then** the tool list includes `get_db_labels` and `neo4j_schema_snapshot`.
2. **Given** the server is running, **When** the client calls `get_db_labels`,
   **Then** a JSON array of label strings is returned.

---

### User Story 2 - Validate label existence and follow reference guidance (Priority: P2)

Operators and agents need a reference resource and prompt that explain how to
check label existence and query coreConcept nodes.

**Why this priority**: Clear guidance reduces confusion after removing dynamic
resources and ensures consistent usage.

**Independent Test**: Can be fully tested by listing resources/prompts and
reading the reference resource content.

**Acceptance Scenarios**:

1. **Given** the server is running, **When** the client calls `resources/list`,
   **Then** it includes `resource://neo4j/labels/{label}` (name
   `check_label_existance`) and `resource://neo4j/refarchi`.
2. **Given** the server is running, **When** the client calls `prompts/list`,
   **Then** it includes `neo4j_refarchi_prompt` and excludes the removed prompts.

---

### Edge Cases

- `get_db_labels` returns an empty JSON array when no labels exist.
- A label lookup for a missing label returns an empty JSON array.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST remove dynamic resources for schema, labels, and
  coreConcept data.
- **FR-002**: System MUST provide tool `get_db_labels` that returns a JSON array
  of labels.
- **FR-003**: System MUST provide tool `get_coreConcept` that returns sanitized
  JSON of coreConcept nodes with `id`, `labels`, and `properties`.
- **FR-004**: System MUST provide tool `neo4j_schema_snapshot` with a
  `sample_size` parameter aligned to server configuration defaults.
- **FR-005**: System MUST expose resource `resource://neo4j/labels/{label}` with
  name `check_label_existance`, case-insensitive lookup behavior, and an empty
  JSON array when no nodes match.
- **FR-006**: System MUST expose static resource `resource://neo4j/refarchi`
  describing tool usage and label checks.
- **FR-007**: System MUST expose prompt `neo4j_refarchi_prompt` that instructs
  the agent to read `resource://neo4j/refarchi`.
- **FR-008**: System MUST remove prompts `neo4j_label_lookup` and
  `neo4j_core_concepts_prompt` from the prompt list.
- **FR-009**: Tools created or changed in this feature MUST be read-only,
  idempotent, and open-world; Neo4j errors MUST surface as ToolError with JSON
  responses.
- **FR-011**: The MCP tools/resources added or changed in this feature MUST not
  require authentication.
- **FR-010**: Documentation, manifest, and tests MUST reflect the new tool and
  resource surface.
- **FR-012**: Removal of existing tools/resources/prompts MUST include migration
  guidance and either a deprecation window or a major version bump.

### Quality & MCP Requirements

- **QR-001**: Linting, formatting, and static analysis gates MUST pass.
- **QR-002**: Tests MUST cover tools/list, resources/list, and prompts/list
  expectations.
- **QR-003**: MCP tool and resource contracts MUST include updated names and
  URIs.

### Key Entities *(include if feature involves data)*

- **MCP Tool**: Read-only operations that return JSON results.
- **MCP Resource**: Static or templated reference content exposed via
  `resources/read`.
- **MCP Prompt**: Orientation text that guides agent behavior.

## Assumptions

- Clients can migrate to the new tool and resource names without data migration.
- The server continues to operate in read-only mode for these tools.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `tools/list` returns the new tools and excludes removed resources
  as tools in 100% of test runs.
- **SC-002**: `resources/list` returns only the new reference and template
  resources, with no deprecated URIs.
- **SC-003**: `prompts/list` returns the new orientation prompt and excludes
  removed prompts.
- **SC-004**: Documentation and manifest examples are updated in one release
  cycle with zero regressions in automated tests.
