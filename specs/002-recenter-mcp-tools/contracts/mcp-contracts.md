# MCP Contracts: Recenter MCP Tools

## Tools

- **get_db_labels**
  - Input: none
  - Output: JSON array of label strings
  - Behavior: read-only, idempotent, open-world

- **get_coreConcept**
  - Input: none
  - Output: JSON array of objects with `id`, `labels`, `properties`
  - Behavior: read-only, idempotent, open-world

- **neo4j_schema_snapshot**
  - Input: `sample_size` (default from config)
  - Output: JSON schema snapshot
  - Behavior: read-only, idempotent, open-world

## Resources

- **resource://neo4j/labels/{label}** (name: `check_label_existance`)
  - Output: JSON array of nodes with `id`, `labels`, `properties` (empty array if none)
  - Matching: case-insensitive label lookup

- **resource://neo4j/refarchi** (name: `neo4j_refarchi`)
  - Output: static reference text

## Prompts

- **neo4j_refarchi_prompt**
  - Intent: instructs agent to read `resource://neo4j/refarchi`

## Errors

- Neo4j failures return ToolError with JSON payload for all tools.
