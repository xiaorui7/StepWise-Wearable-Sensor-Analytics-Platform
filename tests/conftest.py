import os
from uuid import uuid4

import pytest
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from stepwise.db import Base
from stepwise.s3_storage import S3ArtifactStore
from stepwise.settings import Settings


@pytest.fixture
def s3_settings():
    endpoint = os.getenv("STEPWISE_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("STEPWISE_TEST_S3_ENDPOINT is not configured")
    settings = Settings(
        storage_backend="s3", s3_endpoint_url=endpoint, s3_bucket="test-" + uuid4().hex,
        s3_access_key=os.environ["STEPWISE_TEST_S3_ACCESS_KEY"],
        s3_secret_key=os.environ["STEPWISE_TEST_S3_SECRET_KEY"],
    )
    store = S3ArtifactStore(settings)
    store.client.create_bucket(Bucket=settings.s3_bucket)
    try:
        yield settings
    finally:
        # Only the uniquely named test bucket is removed; application buckets are untouched.
        for page in store.client.get_paginator("list_objects_v2").paginate(Bucket=settings.s3_bucket):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                store.client.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": keys})
        store.client.delete_bucket(Bucket=settings.s3_bucket)


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
