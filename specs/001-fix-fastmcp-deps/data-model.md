# Data Model: Fix FastMCP Dependencies Error

## Entities

### StartupConfig

- **Represents**: Configuration inputs used to start the MCP server.
- **Key attributes**:
  - `read_only_mode` (boolean)
  - `timeouts` (object, includes read timeout)
  - `token_limit` (number)
  - `schema_sample_size` (number)

### StartupStatus

- **Represents**: Result of an attempted server start.
- **Key attributes**:
  - `status` (enum: ready, failed)
  - `timestamp` (datetime)
  - `message` (string)

### StartupError

- **Represents**: Structured error information when startup fails.
- **Key attributes**:
  - `error_code` (string)
  - `summary` (string)
  - `resolution_hint` (string)

## Relationships

- `StartupStatus` may reference a `StartupError` when `status` is `failed`.
