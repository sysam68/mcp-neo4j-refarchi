# Quickstart: Fix FastMCP Dependencies Error

## Goal

Validate the server starts without runtime exceptions and exposes tool metadata.

## Steps

1. Start the MCP server with a standard configuration.
2. Confirm the process reaches a ready state without exceptions.
3. Request tool metadata and verify a non-empty tool list.
4. Start the server with an incompatible configuration and verify the error
   includes a resolution hint.
5. Start the server in read-only mode and confirm write tools are unavailable.
6. Validate tool/resource identifiers match the published list.

## Expected Results

- Server startup completes successfully for supported configurations.
- Tool metadata is available after startup.
- Incompatible configurations return concise, actionable errors.
- Read-only mode excludes write tools.
- Tool identifiers remain unchanged.

## Validation Results (2026-01-18)

- Startup smoke: Passed (integration startup tests).
- Tool metadata listing: Passed (tools available post-startup).
- Incompatible config error: Passed (startup error is actionable).
- Read-only mode validation: Passed (write tool disabled).
- Tool identifier snapshot: Passed (tool names unchanged).
