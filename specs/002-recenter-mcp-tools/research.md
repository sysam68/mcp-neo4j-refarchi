# Research Notes: Recenter MCP Tools

## Decision 1: Replace dynamic resources with read-only tools

**Decision**: Move labels/coreConcept/schema snapshot access into read-only tools
and remove dynamic resources.

**Rationale**: Tools provide explicit, idempotent access patterns and clearer
client expectations while reducing mutable resource exposure.

**Alternatives considered**: Keep existing resources and mark them deprecated.

## Decision 2: Standardize output shapes

**Decision**: Return sanitized JSON objects for coreConcept nodes and empty
arrays for missing labels.

**Rationale**: Simplifies client parsing and keeps outputs deterministic across
implementations.

**Alternatives considered**: Return raw Neo4j driver objects or raise ToolError
for missing labels.
