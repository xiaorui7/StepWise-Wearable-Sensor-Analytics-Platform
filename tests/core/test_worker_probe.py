from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from stepwise.worker_probe import worker_ready


@pytest.mark.parametrize("name,age,expected", [
    ("stepwise-pod-a-random", 300, True),
    ("stepwise-pod-a-random", 600, False),
    ("stepwise-pod-b-random", 1, False),
    ("stepwise-pod-a-random", -10, False),
])
def test_probe_uses_named_worker_and_rq_idle_ttl(name, age, expected):
    now = datetime.now(UTC)
    worker = SimpleNamespace(name=name, hostname=None, worker_ttl=420,
                             last_heartbeat=now - timedelta(seconds=age))
    assert worker_ready(worker, "pod-a", now, 60) is expected
