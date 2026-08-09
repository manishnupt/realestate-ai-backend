"""add property suggestions and last_suggestion_run_at

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("last_suggestion_run_at", sa.DateTime(timezone=False), nullable=True))

    # Unlike migration 0004's add_column case, create_table DOES implicitly
    # CREATE TYPE for enum columns on a brand-new table — no explicit .create()
    # needed (and calling it explicitly here would double-create and fail).
    op.create_table(
        "property_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "field", sa.Enum("description", "amenities", "price", name="suggestion_field"), nullable=False
        ),
        sa.Column("current_value", sa.Text(), nullable=False),
        sa.Column("suggested_value", sa.Text(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="suggestion_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "source_lead_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=False), nullable=True),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_property_suggestions_property_id", "property_suggestions", ["property_id"])
    op.create_index("ix_property_suggestions_status", "property_suggestions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_property_suggestions_status", table_name="property_suggestions")
    op.drop_index("ix_property_suggestions_property_id", table_name="property_suggestions")
    op.drop_table("property_suggestions")
    op.execute("DROP TYPE IF EXISTS suggestion_status")
    op.execute("DROP TYPE IF EXISTS suggestion_field")
    op.drop_column("properties", "last_suggestion_run_at")
