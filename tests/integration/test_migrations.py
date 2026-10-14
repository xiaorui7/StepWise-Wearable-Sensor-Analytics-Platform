from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from stepwise.db import Base
from stepwise.settings import get_settings


@pytest.mark.postgres
def test_postgres_migration_from_empty_and_existing_schema(postgres_env, monkeypatch):
    _, url, engine = postgres_env
    # This is the fixture's unique test schema, never the application's public tables.
    Base.metadata.drop_all(engine)
    monkeypatch.setenv("STEPWISE_DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260928_0001")
        identity = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO analyses (id,status,created_at,sensor_config,input_metadata) "
                    "VALUES (:id,'QUEUED',:created_at,'{}','{}')"
                ),
                {"id": identity, "created_at": datetime.now(UTC)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            preserved = connection.execute(
                text("SELECT attempts,idempotency_key FROM analyses WHERE id=:id"), {"id": identity}
            ).one()
            assert preserved.attempts == 0 and preserved.idempotency_key is None
        command.downgrade(config, "base")
        assert "analyses" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert {"analyses", "artifacts"} <= set(inspect(engine).get_table_names())
    finally:
        get_settings.cache_clear()
