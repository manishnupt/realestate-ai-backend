"""add call outcome extraction fields and recording url to leads

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    lead_outcome = sa.Enum(
        "interested",
        "not_interested",
        "callback_requested",
        "no_answer",
        "wrong_number",
        name="lead_outcome",
    )
    # Unlike create_table, add_column does not implicitly CREATE TYPE for a
    # Postgres enum — it must be created explicitly first.
    lead_outcome.create(op.get_bind(), checkfirst=True)

    op.add_column("leads", sa.Column("outcome", lead_outcome, nullable=True))
    op.add_column("leads", sa.Column("outcome_reason", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("extracted_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("leads", sa.Column("outcome_extracted_at", sa.DateTime(timezone=False), nullable=True))
    op.add_column("leads", sa.Column("recording_url", sa.String(length=1024), nullable=True))
    op.create_index("ix_leads_outcome", "leads", ["outcome"])


def downgrade() -> None:
    op.drop_index("ix_leads_outcome", table_name="leads")
    op.drop_column("leads", "recording_url")
    op.drop_column("leads", "outcome_extracted_at")
    op.drop_column("leads", "extracted_details")
    op.drop_column("leads", "outcome_reason")
    op.drop_column("leads", "outcome")
    op.execute("DROP TYPE IF EXISTS lead_outcome")
