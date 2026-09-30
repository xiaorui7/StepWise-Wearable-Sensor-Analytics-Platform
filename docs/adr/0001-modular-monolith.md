# ADR 0001: Modular Monolith with a Background Worker

## Status

Accepted.

## Context

The original senior-design prototype combined file parsing, gait calculations, report generation,
and HTTP integration in large scripts. The portfolio version needs clear ownership boundaries and
asynchronous processing, but its workload and team size do not justify independently deployed
domain services.

## Decision

StepWise uses one Python package with modules for the domain core, reports, persistence, API, and
jobs. FastAPI and an RQ worker run as separate processes from the same codebase. PostgreSQL stores
job state and structured results, while a shared persistent volume stores uploads and reports.

## Consequences

- Domain calculations can be imported by the API, worker, tests, and CLI without network calls.
- A worker prevents chart/report generation from blocking HTTP requests.
- One repository and migration history keep local setup and debugging manageable.
- The API and worker still require coordinated deployment because they share code and schema.
- If distinct teams or scaling profiles emerge later, module boundaries provide candidates for
  extraction. That complexity is not justified now.
