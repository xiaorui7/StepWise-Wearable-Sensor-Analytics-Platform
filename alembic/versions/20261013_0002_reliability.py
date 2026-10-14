"""Add bounded attempts, execution fencing and submission idempotency."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_0002"
down_revision = "20260928_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("analyses") as batch:
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("execution_token", sa.String(36)))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("idempotency_key", sa.String(64)))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64)))
        batch.create_unique_constraint("uq_analysis_idempotency", ["idempotency_key"])
    with op.batch_alter_table("artifacts") as batch:
        batch.create_unique_constraint("uq_artifact_analysis_name", ["analysis_id", "name"])


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("uq_artifact_analysis_name", type_="unique")
    with op.batch_alter_table("analyses") as batch:
        batch.drop_constraint("uq_analysis_idempotency", type_="unique")
        for name in (
            "request_fingerprint",
            "idempotency_key",
            "lease_expires_at",
            "execution_token",
            "attempts",
        ):
            batch.drop_column(name)
