"""add leads table

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "calling", "completed", "failed", name="lead_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("call_outcome", sa.String(length=255), nullable=True),
        sa.Column("is_dnd_checked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["broker_id"], ["brokers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_leads_property_id", "leads", ["property_id"])
    op.create_index("ix_leads_broker_id", "leads", ["broker_id"])
    op.create_index("ix_leads_status", "leads", ["status"])


def downgrade() -> None:
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_index("ix_leads_broker_id", table_name="leads")
    op.drop_index("ix_leads_property_id", table_name="leads")
    op.drop_table("leads")
    op.execute("DROP TYPE IF EXISTS lead_status")
