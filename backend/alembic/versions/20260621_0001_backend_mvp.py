from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260621_0001"
down_revision = None
branch_labels = None
depends_on = None

uuid_type = sa.String(length=36).with_variant(postgresql.UUID(as_uuid=True), "postgresql")
json_payload_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "voice_session",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_session")),
    )
    op.create_index(op.f("ix_voice_session_status"), "voice_session", ["status"], unique=False)

    op.create_table(
        "provider_config",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("secret_ref", sa.String(length=256), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_config")),
        sa.UniqueConstraint(
            "provider_type",
            "provider_name",
            "model",
            name=op.f("uq_provider_config_identity"),
        ),
    )
    op.create_index(op.f("ix_provider_config_provider_name"), "provider_config", ["provider_name"], unique=False)
    op.create_index(op.f("ix_provider_config_provider_type"), "provider_config", ["provider_type"], unique=False)

    op.create_table(
        "conversation_turn",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("session_id", uuid_type, nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["voice_session.id"],
            name=op.f("fk_conversation_turn_session_id_voice_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_turn")),
        sa.UniqueConstraint("session_id", "turn_index", "role", name="uq_turn_session_index_role"),
    )
    op.create_index(
        "ix_conversation_turn_session_id_turn_index",
        "conversation_turn",
        ["session_id", "turn_index"],
        unique=False,
    )

    op.create_table(
        "livekit_room_session",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("session_id", uuid_type, nullable=False),
        sa.Column("room_name", sa.String(length=128), nullable=False),
        sa.Column("user_identity", sa.String(length=128), nullable=False),
        sa.Column("worker_identity", sa.String(length=128), nullable=False),
        sa.Column("token_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["voice_session.id"],
            name=op.f("fk_livekit_room_session_session_id_voice_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_livekit_room_session")),
        sa.UniqueConstraint("room_name", name=op.f("uq_livekit_room_session_room_name")),
        sa.UniqueConstraint("session_id", name=op.f("uq_livekit_room_session_session_id")),
    )

    op.create_table(
        "voice_event",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("session_id", uuid_type, nullable=False),
        sa.Column("turn_id", uuid_type, nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", json_payload_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["voice_session.id"],
            name=op.f("fk_voice_event_session_id_voice_session"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["conversation_turn.id"],
            name=op.f("fk_voice_event_turn_id_conversation_turn"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_event")),
    )
    op.create_index(op.f("ix_voice_event_event_type"), "voice_event", ["event_type"], unique=False)
    op.create_index("ix_voice_event_session_id_created_at", "voice_event", ["session_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_voice_event_session_id_created_at", table_name="voice_event")
    op.drop_index(op.f("ix_voice_event_event_type"), table_name="voice_event")
    op.drop_table("voice_event")
    op.drop_table("livekit_room_session")
    op.drop_index("ix_conversation_turn_session_id_turn_index", table_name="conversation_turn")
    op.drop_table("conversation_turn")
    op.drop_index(op.f("ix_provider_config_provider_type"), table_name="provider_config")
    op.drop_index(op.f("ix_provider_config_provider_name"), table_name="provider_config")
    op.drop_table("provider_config")
    op.drop_index(op.f("ix_voice_session_status"), table_name="voice_session")
    op.drop_table("voice_session")
