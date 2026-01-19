# HTTP Disconnect Handling Contracts

## Behavior Contracts

- Client disconnect during POST body read does not crash the server.
- If the response channel is closed, no response is written.
- If the connection is still open, a structured error response is returned.

## Logging Contracts

- Disconnect events are logged as handled.
- Unhandled exception groups are not emitted for disconnect scenarios.
