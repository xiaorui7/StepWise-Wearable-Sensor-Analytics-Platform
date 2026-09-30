# ADR 0002: Redis and RQ for Background Jobs

## Status

Accepted.

## Context

Analysis includes parsing, plotting, and report generation and should not run inside an HTTP request.
Jobs require simple enqueueing, worker execution, failure handling, and persisted application state.
There is no event-streaming or multi-consumer requirement.

## Decision

Use Redis as the queue backend and RQ as the Python worker library. PostgreSQL, rather than RQ, is
the source of truth for `QUEUED`, `RUNNING`, `SUCCEEDED`, and `FAILED` states.

## Consequences

- The implementation remains small and easy to run with Docker Compose.
- Jobs call the same typed application service used by tests and the CLI.
- Redis/RQ does not provide Kafka-style replay, partitions, or streaming semantics. StepWise does not
  need those capabilities.
- A larger workflow system could offer retries and orchestration, but would add operational weight
  without improving this project's current requirements.
