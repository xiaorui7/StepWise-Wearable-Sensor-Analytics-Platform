"""Readiness only: a registered RQ worker on this host with a recent heartbeat."""
import socket
from datetime import UTC, datetime

from redis import Redis
from rq import Worker
from rq.worker import BaseWorker

from stepwise.settings import get_settings


def worker_ready(worker: BaseWorker, hostname: str, now: datetime, job_timeout_s: int) -> bool:
    # RQ may recreate only heartbeat fields after Redis keys expire during host sleep.
    # The worker name remains recoverable from the key, unlike its hostname field.
    return bool(
        worker.name.startswith(f"stepwise-{hostname}-")
        and worker.last_heartbeat is not None
        and 0 <= (now - worker.last_heartbeat).total_seconds()
        < max(worker.worker_ttl + 60, job_timeout_s + 120)
    )


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    now = datetime.now(UTC)
    healthy = any(
        worker_ready(worker, socket.gethostname(), now, settings.job_timeout_s)
        for worker in Worker.all(connection=connection)
    )
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
