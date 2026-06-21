from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260621_0003"
down_revision = "20260621_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("voice_worker_job", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("voice_worker_job", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("voice_worker_job", "heartbeat_at")
    op.drop_column("voice_worker_job", "lease_expires_at")
