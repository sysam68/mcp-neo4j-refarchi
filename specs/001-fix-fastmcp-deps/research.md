# Research: Fix FastMCP Dependencies Error

## Decisions

### FastMCP initialization compatibility

- **Decision**: Use the supported FastMCP constructor signature and remove
  unsupported arguments.
- **Rationale**: The crash occurs due to an unexpected constructor argument; the
  safest fix is to align with the supported API to restore startup reliability.
- **Alternatives considered**:
  - Pin an older FastMCP version that still accepts the argument.
  - Add a compatibility shim that conditionally passes the argument.

### Regression coverage

- **Decision**: Add a regression test that starts the server and verifies tool
  metadata exposure.
- **Rationale**: This validates successful startup and guards against future
  constructor signature regressions.
- **Alternatives considered**:
  - Manual smoke testing only.
