"""add dnd_numbers, lead retry fields, per-broker call limits, call_attempts

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("brokers", sa.Column("call_concurrency_limit", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("brokers", sa.Column("daily_call_limit", sa.Integer(), nullable=False, server_default="100"))

    op.add_column("leads", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("leads", sa.Column("next_attempt_at", sa.DateTime(timezone=False), nullable=True))

    op.create_table(
        "dnd_numbers",
        sa.Column("phone_number", sa.String(length=32), primary_key=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
    )

    # create_table implicitly CREATEs TYPE for enum columns on a brand-new table
    # (see migration 0005's note) — no explicit .create() needed here either.
    op.create_table(
        "call_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dialed_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.Column("outcome", sa.String(length=255), nullable=True),
        sa.Column("stt_latency_ms", sa.Integer(), nullable=True),
        sa.Column("llm_latency_ms", sa.Integer(), nullable=True),
        sa.Column("tts_latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_stage", sa.Enum("stt", "llm", "tts", name="pipeline_stage"), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["broker_id"], ["brokers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_call_attempts_lead_id", "call_attempts", ["lead_id"])
    op.create_index("ix_call_attempts_broker_id", "call_attempts", ["broker_id"])
    op.create_index("ix_call_attempts_dialed_at", "call_attempts", ["dialed_at"])


def downgrade() -> None:
    op.drop_index("ix_call_attempts_dialed_at", table_name="call_attempts")
    op.drop_index("ix_call_attempts_broker_id", table_name="call_attempts")
    op.drop_index("ix_call_attempts_lead_id", table_name="call_attempts")
    op.drop_table("call_attempts")
    op.execute("DROP TYPE IF EXISTS pipeline_stage")

    op.drop_table("dnd_numbers")

    op.drop_column("leads", "next_attempt_at")
    op.drop_column("leads", "attempt_count")

    op.drop_column("brokers", "daily_call_limit")
    op.drop_column("brokers", "call_concurrency_limit")
