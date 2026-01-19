# Data Model: Handle POST Disconnect Errors

## HTTP Request

- **state**: body-read, disconnected, responded
- **disconnect_reason**: client disconnect or send failure

## Stateless Session

- **status**: running, shutting_down, healthy
- **error_handled**: boolean

## Error Response

- **type**: disconnect
- **message**: human-readable summary
- **response_written**: boolean
