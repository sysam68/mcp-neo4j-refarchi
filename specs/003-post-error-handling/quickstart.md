# Quickstart: Handle POST Disconnect Errors

## Validate Disconnect Handling

1. Start the MCP server in HTTP mode.
2. Send a POST request and disconnect before the body completes.
3. Confirm the server remains running and accepts a subsequent request.

## Validate Error Response Behavior

1. Trigger a disconnect after the body is read but before response write.
2. Confirm no unhandled exception group is logged.
3. Confirm response writes are skipped when the connection is closed.
