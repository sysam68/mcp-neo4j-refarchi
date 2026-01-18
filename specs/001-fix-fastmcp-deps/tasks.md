---

description: "Task list template for feature implementation"

---

# Tasks: Fix FastMCP Dependencies Error

**Input**: Design documents from `/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/001-fix-fastmcp-deps/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are REQUIRED by the specification (regression coverage).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- Paths shown below assume single project - adjust based on plan.md structure

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Create regression test scaffolding in `tests/unit/test_fastmcp_init.py`
- [X] T002 Create integration test scaffolding in `tests/integration/test_startup.py`
- [X] T003 [P] Verify lint/test configs remain valid in `pyproject.toml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 Update FastMCP initialization to use supported constructor in `src/mcp_neo4j_cypher/server.py`
- [X] T005 Add explicit startup error mapping for FastMCP init failures in `src/mcp_neo4j_cypher/server.py`
- [X] T006 [P] Add regression fixture helpers for startup config in `tests/unit/helpers.py`
- [X] T018 [P] Run lint and static analysis gates via `pyproject.toml` (ruff, pyright)
- [X] T019 Validate MCP tool/resource schemas and error formats against `specs/001-fix-fastmcp-deps/contracts/server-startup.yaml`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Start the server without runtime errors (Priority: P1) 🎯 MVP

**Goal**: Server starts cleanly and exposes tool metadata after startup.

**Independent Test**: Start the server with standard config and confirm readiness plus tool list.

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T007 [P] [US1] Unit test FastMCP init uses supported signature in `tests/unit/test_fastmcp_init.py`
- [X] T008 [P] [US1] Integration test server startup readiness in `tests/integration/test_startup.py`
- [X] T009 [P] [US1] Integration test tool metadata listing in `tests/integration/test_startup.py`

### Implementation for User Story 1

- [X] T010 [US1] Remove unsupported FastMCP constructor args in `src/mcp_neo4j_cypher/server.py`
- [X] T011 [US1] Validate tool metadata exposure remains unchanged in `src/mcp_neo4j_cypher/server.py`
- [X] T020 [US1] Verify tool/resource identifiers unchanged via metadata snapshot in `tests/integration/test_startup.py`
- [X] T021 [US1] Validate existing configuration options in `tests/integration/test_startup.py`

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Receive actionable startup errors (Priority: P2)

**Goal**: Startup failures return concise, actionable error messages.

**Independent Test**: Start the server with incompatible config and confirm error message guidance.

### Tests for User Story 2 ⚠️

- [X] T012 [P] [US2] Unit test startup error mapping in `tests/unit/test_fastmcp_init.py`
- [X] T013 [P] [US2] Integration test incompatible startup error in `tests/integration/test_startup.py`

### Implementation for User Story 2

- [X] T014 [US2] Implement actionable startup error message in `src/mcp_neo4j_cypher/server.py`
- [X] T015 [US2] Ensure error output does not leak secrets in `src/mcp_neo4j_cypher/server.py`

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T016 [P] Update quickstart validation steps in `specs/001-fix-fastmcp-deps/quickstart.md`
- [X] T017 Run quickstart validation and record results in `specs/001-fix-fastmcp-deps/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2)
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - Independent of US1 but validates error paths

### Within Each User Story

- Tests (if included) MUST be written and FAIL before implementation
- Core implementation before integration validation
- Story complete before moving to next priority

### Parallel Opportunities

- Setup tasks marked [P] can run in parallel
- Foundational tasks marked [P] can run in parallel
- Tests for a user story marked [P] can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch tests for User Story 1 together:
Task: "Unit test FastMCP init uses supported signature in tests/unit/test_fastmcp_init.py"
Task: "Integration test server startup readiness in tests/integration/test_startup.py"
Task: "Integration test tool metadata listing in tests/integration/test_startup.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo
4. Each story adds value without breaking previous stories
