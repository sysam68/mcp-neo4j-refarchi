# Quickstart: Recenter MCP Tools

## Validate Tool Surface

1. Call `tools/list` and confirm `get_db_labels`, `get_coreConcept`, and
   `neo4j_schema_snapshot` are present.
2. Call `get_db_labels` and verify a JSON array of label strings.
3. Call `get_coreConcept` and verify objects include `id`, `labels`, and
   `properties`.

## Validate Resource Surface

1. Call `resources/list` and confirm `resource://neo4j/labels/{label}` (name
   `check_label_existance`) and `resource://neo4j/refarchi` are present.
2. Read `resource://neo4j/refarchi` and confirm it describes tool usage.

## Validate Prompt Surface

1. Call `prompts/list` and confirm `neo4j_refarchi_prompt` is present and
   deprecated prompts are removed.
