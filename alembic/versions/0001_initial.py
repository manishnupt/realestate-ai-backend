"""initial schema: brokers, properties

Revision ID: 0001
Revises:
Create Date: 2026-07-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brokers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_brokers_email", "brokers", ["email"], unique=True)

    op.create_table(
        "properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "type",
            sa.Enum("plot", "flat", name="property_type"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("area", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=255), nullable=False),
        sa.Column("price", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "price_unit",
            sa.Enum("total", "per_sqft", name="price_unit"),
            nullable=False,
        ),
        sa.Column("size_value", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "size_unit",
            sa.Enum("sqft", "acres", name="size_unit"),
            nullable=False,
        ),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("amenities", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column(
            "status",
            sa.Enum("available", "sold", "on-hold", name="property_status"),
            nullable=False,
            server_default="available",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["broker_id"], ["brokers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_properties_broker_id", "properties", ["broker_id"])


def downgrade() -> None:
    op.drop_index("ix_properties_broker_id", table_name="properties")
    op.drop_table("properties")
    op.execute("DROP TYPE IF EXISTS property_status")
    op.execute("DROP TYPE IF EXISTS price_unit")
    op.execute("DROP TYPE IF EXISTS size_unit")
    op.execute("DROP TYPE IF EXISTS property_type")

    op.drop_index("ix_brokers_email", table_name="brokers")
    op.drop_table("brokers")
