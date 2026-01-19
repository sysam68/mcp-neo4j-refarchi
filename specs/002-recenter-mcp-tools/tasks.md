# Tasks: Recenter MCP Tools

**Input**: Design documents from `/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/002-recenter-mcp-tools/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are required by the spec for tools/list, resources/list, and prompts/list coverage.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm current MCP surface before changes

- [X] T001 Inventory current MCP tools/resources/prompts in `src/mcp_neo4j_cypher/server.py` (FR-010)
- [X] T002 Inventory current MCP surface metadata in `manifest.json` (FR-010)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared helpers needed by multiple stories

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 [P] Add node JSON sanitization helper in `src/mcp_neo4j_cypher/utils.py`
- [X] T004 Add ToolError JSON mapping helper for Neo4j failures in `src/mcp_neo4j_cypher/server.py`

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Discover labels and schema via tools (Priority: P1) 🎯 MVP

**Goal**: Replace dynamic resources with read-only tools for labels, coreConcept, and schema snapshot.

**Independent Test**: tools/list and tool calls return expected JSON payloads.

### Tests for User Story 1

- [X] T005 [P] [US1] Add tools/list expectations for new tools in `tests/integration/test_server_tools_IT.py`
- [X] T006 [P] [US1] Add get_db_labels empty array test in `tests/integration/test_server_tools_IT.py`
- [X] T007 [P] [US1] Add get_coreConcept JSON shape test in `tests/integration/test_server_tools_IT.py`
- [X] T008 [P] [US1] Add neo4j_schema_snapshot tool test in `tests/integration/test_server_tools_IT.py`
- [X] T009 [P] [US1] Add ToolError JSON test for Neo4j failure in `tests/integration/test_server_tools_IT.py`
- [X] T010 [P] [US1] Add neo4j_schema_snapshot sample_size boundary test in `tests/integration/test_server_tools_IT.py`
- [X] T011 [P] [US1] Add no-auth tool access test in `tests/integration/test_server_tools_IT.py`
- [X] T032 [P] [US1] Add idempotent/open-world behavior test in `tests/integration/test_server_tools_IT.py`

### Implementation for User Story 1

- [X] T012 [US1] Remove dynamic schema/labels/coreConcept resources in `src/mcp_neo4j_cypher/server.py`
- [X] T013 [US1] Add get_db_labels tool in `src/mcp_neo4j_cypher/server.py`
- [X] T014 [US1] Add get_coreConcept tool with sanitized JSON output in `src/mcp_neo4j_cypher/server.py`
- [X] T015 [US1] Convert neo4j_schema_snapshot to tool with sample_size param in `src/mcp_neo4j_cypher/server.py`
- [X] T016 [US1] Wire ToolError JSON handling for Neo4j tool failures in `src/mcp_neo4j_cypher/server.py`
- [X] T017 [US1] Add tool docstrings for new tools in `src/mcp_neo4j_cypher/server.py`

**Checkpoint**: User Story 1 is fully functional and testable independently

---

## Phase 4: User Story 2 - Validate label existence and follow reference guidance (Priority: P2)

**Goal**: Provide reference resource + prompt and rename label template resource.

**Independent Test**: resources/list and prompts/list expose new entries and reference content can be read.

### Tests for User Story 2

- [X] T018 [P] [US2] Add resources/list expectations in `tests/integration/test_server_tools_IT.py`
- [X] T019 [P] [US2] Add prompts/list expectations in `tests/integration/test_server_tools_IT.py`
- [X] T020 [P] [US2] Add resource read test for missing label returns empty array in `tests/integration/test_server_tools_IT.py`
- [X] T021 [P] [US2] Add case-insensitive label lookup test in `tests/integration/test_server_tools_IT.py`
- [X] T022 [P] [US2] Add no-auth resource access test in `tests/integration/test_server_tools_IT.py`

### Implementation for User Story 2

- [X] T023 [US2] Rename label template resource and enforce case-insensitive lookup in `src/mcp_neo4j_cypher/server.py`
- [X] T024 [US2] Add static refarchi resource content in `src/mcp_neo4j_cypher/server.py`
- [X] T025 [US2] Remove legacy prompts and add neo4j_refarchi_prompt in `src/mcp_neo4j_cypher/server.py`

**Checkpoint**: User Stories 1 and 2 are independently functional

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, manifest, and release notes alignment

- [X] T026 [P] Update MCP surface in `manifest.json`
- [X] T027 [P] Update MCP surface documentation and examples in `README.md`
- [X] T028 [P] Update MCP metadata references in `server.json`
- [X] T029 [P] Update release notes in `CHANGELOG.md`
- [X] T030 Run quickstart validation in `specs/002-recenter-mcp-tools/quickstart.md` (FR-010)
- [X] T031 Run lint/format/type gates using `pyproject.toml` and `pyrightconfig.json`
- [X] T033 [P] Update contracts doc in `specs/002-recenter-mcp-tools/contracts/mcp-contracts.md`
- [X] T034 [P] Add migration guidance/deprecation or major bump note in `README.md` and `CHANGELOG.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion - blocks all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational completion
- **User Story 2 (Phase 4)**: Depends on Foundational completion
- **Polish (Phase 5)**: Depends on User Stories completion

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies on other stories
- **User Story 2 (P2)**: No dependencies on other stories (can run after Phase 2)

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Tool/resource registration before metadata updates
- Story completion requires tests passing

### Parallel Opportunities

- T003, T005, T006, T007, T008, T009, T010, T011, T032, T018, T019, T020, T021, T022, T026, T027, T028, T029, T033, T034 can run in parallel
- US1 and US2 work can proceed in parallel after Phase 2 if staffed

---

## Parallel Example: User Story 1

```bash
Task: "Add tools/list expectations for new tools in tests/integration/test_server_tools_IT.py"
Task: "Add get_db_labels integration test in tests/integration/test_server_tools_IT.py"
Task: "Add get_coreConcept JSON shape test in tests/integration/test_server_tools_IT.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. Validate User Story 1 independently

### Incremental Delivery

1. Complete Setup + Foundational
2. Deliver User Story 1 (MVP)
3. Deliver User Story 2
4. Complete Polish phase

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Stop at checkpoints to validate independently
