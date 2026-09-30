from uuid import uuid4

import fakeredis

from stepwise.jobs import RQJobQueue


def test_rq_adapter_enqueues_analysis_job(monkeypatch) -> None:
    fake_redis = fakeredis.FakeRedis()
    monkeypatch.setattr("stepwise.jobs.redis.Redis.from_url", lambda _: fake_redis)
    queue = RQJobQueue("redis://unused/0")
    analysis_id = uuid4()
    queue.enqueue_analysis(analysis_id)
    assert queue.queue.count == 1
    job = queue.queue.jobs[0]
    assert job.id == str(analysis_id)
    assert job.args == (str(analysis_id),)
