# Reliability and recovery

## Idempotent submission

Both submission endpoints accept optional `Idempotency-Key`: 1-128 printable non-space ASCII
characters. The database stores its SHA-256 digest, not the original key. The request fingerprint
includes effective configuration and the exact walking/standing bytes; equivalent configuration
defaults normalize through Pydantic. Byte-different recordings are different requests even when
they would yield the same calculations.

- Same key and fingerprint: HTTP 202 with the same analysis ID and its current state.
- Same key, different fingerprint: HTTP 409.
- No key: every submission creates a new logical analysis, including identical recordings.
- A replay does not enqueue again or revive a failed job. Use a new key for a new trial.

The database unique constraint handles concurrent callers, rather than an in-process cache. Keys
are global in this unauthenticated local application and retained as long as the analysis row. A
multi-user deployment would need authorization and a scoped key/retention policy. Request IDs are
correlation metadata, not idempotency keys.

## Attempts and retries

Defaults are three total processing attempts, a five-second retry delay, a 300-second RQ timeout,
and a 60-second recovery grace. Configure `STEPWISE_MAX_ATTEMPTS`, `STEPWISE_RETRY_DELAY_S`,
`STEPWISE_JOB_TIMEOUT_S`, and `STEPWISE_RECOVERY_GRACE_S` in the process environment.

Retryable failures are connection/timeouts, SQLAlchemy operational failures with connection
invalidation or transient SQLSTATE classes (connection, transaction rollback, server shutdown), Redis
connection/timeouts, and explicitly temporary OS errors (`EAGAIN`, `EBUSY`, `ETIMEDOUT`,
`ECONNRESET`). Invalid data, missing files, permissions, out-of-space errors, and arbitrary internal
exceptions are terminal. A processing attempt is counted only after a DB claim; RQ separately
limits dispatch retries, including failures before PostgreSQL can record a claim.

On a retryable processing failure, the owning attempt sets PostgreSQL back to `QUEUED`, records a
safe category/message, and releases the token. An exception activates RQ's bounded Retry policy.
The worker starts RQ's scheduler for delayed retries. Permanent errors clear RQ's retry budget and
raise a safe terminal exception. Successful completion clears the previous error fields.

## Execution ownership and output publication

The worker's conditional `QUEUED -> RUNNING` update increments attempts and assigns an execution
token. A completed task cannot be re-run or overwritten by a duplicate delivery. Finalization locks
the DB row and checks the active token; an obsolete worker cannot commit a result after recovery.

Each attempt writes all five outputs to an isolated staging directory. Files are flushed before a
same-filesystem directory rename. The DB transaction publishes all artifact metadata and the result
together. The API gates generated artifact access on `SUCCEEDED`, including historical artifacts.
Inputs are private; their storage keys are not exposed by the public API.

The optional S3 backend replaces staging/rename with conditional immutable object writes and
SHA-256 content checks. All required objects are verified before the same fenced DB completion
transaction. Partial object sets remain private and have no generated metadata. Object-store
timeouts, throttling and selected server/conditional-conflict errors participate in bounded RQ
retries; checksum failures and permission errors do not. There is no atomic S3 directory rename.
See [object storage and orphan cleanup](CLOUD_INFRASTRUCTURE.md).

This gives idempotent successful publication and fenced completion, not exactly-once computation.
It does not provide a distributed transaction between PostgreSQL, Redis and either storage backend.

## Recovery

For an unexpected child-process exit, the surviving RQ worker disables automatic retries and
records `EXECUTION_INTERRUPTED` when PostgreSQL is available. If the whole worker/container is
killed, its callback cannot run. Use:

```bash
docker compose exec api stepwise-reconcile
```

The reconciliation command performs these checks:

- Expired RUNNING leases are marked FAILED with their token revoked. The lease lasts the configured
  task timeout plus grace; it is a bounded execution deadline, not a continuously renewed heartbeat.
  Pre-migration rows without a lease use their start/creation time plus timeout and grace.
- QUEUED rows older than the grace period are compared with RQ. Queued, started, scheduled or
  deferred jobs are preserved; missing jobs become `QUEUE_JOB_LOST`, and finished/failed/stopped
  mismatches become `QUEUE_STATE_MISMATCH`.
- If Redis cannot be read, queued records are left unchanged. A conditional update protects against
  racing worker claims or completion during reconciliation.

Recovery records a failure rather than silently resubmitting potentially ambiguous work. A user can
explicitly submit again with a new key. The command is operator-run; no custom task scheduler is
added. Historical SUCCEEDED results remain readable even after RQ metadata expires.

## Failure windows and trade-offs

| Window | Behavior and limit |
|---|---|
| DB identity committed, process dies before inputs or enqueue | Key replays still return the same queued ID; reconciliation eventually marks the missing queue job FAILED. No durable enqueue outbox is implemented. |
| Redis accepted enqueue, response acknowledgement lost | API may return 503. A conditional failure update cannot overwrite an already claimed or completed job. The key prevents duplicate logical jobs; the outcome still needs polling/reconciliation. |
| DB retry state committed, worker dies before RQ schedules retry | PostgreSQL can temporarily say QUEUED without a live retry. Reconciliation detects the mismatch. |
| Files renamed, DB commit fails | Final directory can remain orphaned; no generated metadata/result is visible. An attempt uses a new directory on retry. |
| Some/all S3 objects uploaded, process dies or DB commit fails | Unreferenced objects remain private; a new attempt uses a new token. Cleanup requires DB-reference checks; it is not automatic. |
| DB commit succeeds, acknowledgement lost | Ownership has already been cleared. The failure handler cannot replace SUCCEEDED with FAILED. |
| Host/power failure | File flushes and atomic rename are not a backup or cross-system durability guarantee. |

Staging directories are cleaned on handled errors. Abrupt process death can leave staging or orphan
final directories; automatic garbage collection is deferred because safe removal needs a retention
policy and DB reference checks. Do not delete token directories simply because a commit reported an
error: its acknowledgement may have been lost after the commit succeeded.

## Security boundary

The API bounds actual body bytes (including chunked requests), limits each file, accepts TXT/UTF-8,
rejects invalid or non-finite numeric rows before enqueueing, validates strict configurations, and
uses generated storage names. Safe names reject both Windows and POSIX traversal separators. CORS
is configurable; it is not authentication. This local demo has no login, user isolation, rate limiting
or per-user quotas. It should not be exposed as a public service for private recordings without
those controls. Structured logs carry request/job IDs, attempts and failure categories, not raw
sensor recordings.

## Automated evidence

`tests/integration/test_reliability.py` injects publication and completion failures, proves the retry
budget, and tests stale-token fencing. `test_submission.py` covers key conflicts and rejected input.
`test_infrastructure.py` uses isolated PostgreSQL schemas and real Redis/RQ child processes to test
concurrent submission/claims, two workers, immediate and scheduled retries, terminal failures,
abrupt child exits, whole-worker termination, and lost queue jobs.

Those real-worker tests run against both local storage and MinIO when configured.
`test_object_publication.py` covers real object content, partial uploads, revoked ownership,
unreachable endpoints, failed DB finalization and corrupted downloads. Publication-time process
death is tested with actual RQ children. `scripts/failure_demo.py` records the separate local kind
outage and API-replacement experiment.
