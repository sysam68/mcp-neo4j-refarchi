# Research Notes: Handle POST Disconnect Errors

## Decision 1: Treat client disconnect as expected event

**Decision**: Handle ClientDisconnect during POST body reads as a normal
shutdown path and prevent it from bubbling as an unhandled exception group.

**Rationale**: Client disconnects are common and should not crash stateless
sessions or server loops.

**Alternatives considered**: Return a generic 500 error or retry read once.

## Decision 2: Skip response write when connection is closed

**Decision**: If the response channel is already closed, skip writing an error
response and log a handled disconnect message.

**Rationale**: Avoids ClosedResourceError while keeping the server healthy.

**Alternatives considered**: Always attempt a structured error response.
