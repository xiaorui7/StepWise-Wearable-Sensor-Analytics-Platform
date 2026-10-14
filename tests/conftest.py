import os
from uuid import uuid4

import pytest
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from stepwise.db import Base


@pytest.fixture
def postgres_env(tmp_path):
    url = os.getenv("STEPWISE_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("STEPWISE_TEST_POSTGRES_URL is not configured")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions, scoped_url.render_as_string(hide_password=False), engine
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def redis_env():
    url = os.getenv("STEPWISE_TEST_REDIS_URL")
    if not url:
        pytest.skip("STEPWISE_TEST_REDIS_URL is not configured")
    connection = Redis.from_url(url)
    connection.ping()
    yield connection
    # Test queues/jobs use unique names and explicit job cleanup, never FLUSHDB.
    connection.close()
