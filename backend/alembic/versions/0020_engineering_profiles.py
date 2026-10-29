"""Engineering Profile: what the assistant is told about a user's engineering work.

Revision ID: 0020_engineering_profiles
Revises: 0019_memory_foundation
Create Date: 2026-10-29

Additive only: one new table, owned by a user (cascade on delete). Nothing the live release reads
changes, and the school tables keep their data. Codes are checked by the API against the
engineering catalog (app/learning/catalog.py), not by CHECKs, so the catalog grows without a
migration; only the lists' sizes are bounded here. The downgrade drops the table (and so any
profile saved since).

Constraint names go through op.f(): they are final (see 0004_learning).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0020_engineering_profiles"
down_revision = "0019_memory_foundation"
branch_labels = None
depends_on = None

NOW = sa.text("clock_timestamp()")


def upgrade() -> None:
    op.create_table(
        "engineering_profiles",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("role", sa.String(40)),
        sa.Column("experience_level", sa.String(40)),
        sa.Column("focus_areas", pg.ARRAY(sa.String(40)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("platforms", pg.ARRAY(sa.String(40)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("certification_goal", sa.String(40)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_engineering_profiles_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_engineering_profiles")),
        sa.CheckConstraint("cardinality(focus_areas) <= 8", name=op.f("ck_engineering_profiles_focus_areas")),
        sa.CheckConstraint("cardinality(platforms) <= 12", name=op.f("ck_engineering_profiles_platforms")),
    )


def downgrade() -> None:
    op.drop_table("engineering_profiles")
