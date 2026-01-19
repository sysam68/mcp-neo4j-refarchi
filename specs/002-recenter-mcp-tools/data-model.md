# Data Model: Recenter MCP Tools

## MCP Tool

- **id**: tool name
- **read_only**: boolean
- **inputs**: parameters and default values
- **outputs**: JSON payload shape
- **errors**: ToolError JSON on Neo4j failures

## MCP Resource

- **uri**: resource URI
- **name**: logical name
- **type**: static or templated
- **content**: reference text or derived query results

## MCP Prompt

- **name**: prompt identifier
- **intent**: guidance for agent behavior
- **resources**: referenced resource URIs
