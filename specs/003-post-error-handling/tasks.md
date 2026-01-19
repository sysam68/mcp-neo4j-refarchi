# Tasks: Handle POST Disconnect Errors

**Input**: Design documents from `/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/003-post-error-handling/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are required by the spec for disconnect handling coverage.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish baseline for HTTP handling behavior

- [X] T001 Review current HTTP POST handling flow in `src/mcp_neo4j_cypher/server.py`
- [X] T002 Review MCP HTTP transport tests in `tests/integration/test_http_transport_IT.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared error-handling helpers and logging rules

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 [P] Add helper to detect client disconnect and closed response in `src/mcp_neo4j_cypher/server.py`
- [X] T004 Add structured disconnect log helper in `src/mcp_neo4j_cypher/server.py`

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Keep HTTP POST sessions stable (Priority: P1) 🎯 MVP

**Goal**: Prevent client disconnects from crashing the stateless server session.

**Independent Test**: Disconnect during POST does not stop subsequent requests.

### Tests for User Story 1

- [X] T005 [P] [US1] Add POST disconnect regression test in `tests/integration/test_http_transport_IT.py`
- [X] T006 [P] [US1] Add server health check after disconnect in `tests/integration/test_http_transport_IT.py`

### Implementation for User Story 1

- [X] T007 [US1] Handle ClientDisconnect during POST body read in `src/mcp_neo4j_cypher/server.py`
- [X] T008 [US1] Prevent stateless session crash on disconnect in `src/mcp_neo4j_cypher/server.py`

**Checkpoint**: User Story 1 is fully functional and testable independently

---

## Phase 4: User Story 2 - Provide safe error responses for disconnects (Priority: P2)

**Goal**: Provide a safe error response when possible and skip response writes if connection is closed.

**Independent Test**: Disconnect scenarios return structured errors or no response without crashes.

### Tests for User Story 2

- [X] T009 [P] [US2] Add structured error response assertion in `tests/integration/test_http_transport_IT.py`
- [X] T010 [P] [US2] Add closed-response skip assertion in `tests/integration/test_http_transport_IT.py`

### Implementation for User Story 2

- [X] T011 [US2] Emit structured error response when connection remains open in `src/mcp_neo4j_cypher/server.py`
- [X] T012 [US2] Skip response write when connection is closed in `src/mcp_neo4j_cypher/server.py`

**Checkpoint**: User Stories 1 and 2 are independently functional

---

## Phase 5: User Story 3 - Regression coverage for disconnect handling (Priority: P3)

**Goal**: Ensure disconnect handling remains stable across releases.

**Independent Test**: HTTP transport suite includes disconnect scenario coverage.

### Tests for User Story 3

- [X] T013 [P] [US3] Add disconnect scenario to regression suite in `tests/integration/test_http_transport_IT.py`

### Implementation for User Story 3

- [X] T014 [US3] Document disconnect regression scenario in `tests/integration/test_http_transport_IT.py`

**Checkpoint**: All user stories are independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, linting, and quickstart validation

- [X] T015 [P] Update disconnect handling documentation in `README.md`
- [X] T016 [P] Update release notes in `CHANGELOG.md`
- [X] T017 Run quickstart validation in `specs/003-post-error-handling/quickstart.md`
- [X] T018 Run lint/format/type gates using `pyproject.toml` and `pyrightconfig.json`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion - blocks all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational completion
- **User Story 2 (Phase 4)**: Depends on Foundational completion
- **User Story 3 (Phase 5)**: Depends on User Story 1 completion
- **Polish (Phase 6)**: Depends on User Stories completion

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies on other stories
- **User Story 2 (P2)**: No dependencies on other stories
- **User Story 3 (P3)**: Depends on User Story 1 for regression baseline

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Error handling paths before logging updates
- Story completion requires tests passing

### Parallel Opportunities

- T003, T005, T006, T009, T010, T013, T015, T016 can run in parallel

---

## Parallel Example: User Story 1

```bash
Task: "Add POST disconnect regression test in tests/integration/test_http_transport_IT.py"
Task: "Add server health check after disconnect in tests/integration/test_http_transport_IT.py"
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
4. Deliver User Story 3
5. Complete Polish phase

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Stop at checkpoints to validate independently
