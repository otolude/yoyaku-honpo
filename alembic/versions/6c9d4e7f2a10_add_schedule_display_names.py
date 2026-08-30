"""add schedule display names

Revision ID: 6c9d4e7f2a10
Revises: 8e5b2f1c4a90
Create Date: 2026-08-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6c9d4e7f2a10"
down_revision: str | Sequence[str] | None = "8e5b2f1c4a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FORMAT_CHARACTERS_SQL = (
    r"U&'[\00AD\0600-\0605\061C\06DD\070F\0890-\0891\08E2\180E"
    r"\200B-\200F\202A-\202E\2060-\2064\2066-\206F\FEFF\FFF9-\FFFB"
    r"\+0110BD\+0110CD\+013430-\+01343F\+01BCA0-\+01BCA3"
    r"\+01D173-\+01D17A\+0E0001\+0E0020-\+0E007F]'"
)


def upgrade() -> None:
    """Add nullable names and backfill every existing row to unset."""
    op.add_column("schedules", sa.Column("display_name", sa.String(length=32)))
    op.add_column(
        "schedules",
        sa.Column(
            "display_name_source",
            sa.String(length=8),
            server_default=sa.text("'unset'"),
            nullable=False,
        ),
    )
    op.execute("UPDATE schedules SET display_name = NULL, display_name_source = 'unset'")
    op.create_check_constraint(
        op.f("ck_schedules_display_name_source_valid"),
        "schedules",
        "display_name_source IN ('ai', 'manual', 'unset')",
    )
    op.create_check_constraint(
        op.f("ck_schedules_display_name_matches_source"),
        "schedules",
        "(display_name_source = 'unset' AND display_name IS NULL) OR "
        "(display_name_source IN ('ai', 'manual') "
        "AND display_name IS NOT NULL "
        "AND char_length(display_name) BETWEEN 1 AND 32 "
        "AND display_name = btrim(display_name) "
        "AND display_name !~ '[[:cntrl:]]' "
        f"AND display_name !~ {_FORMAT_CHARACTERS_SQL})",
    )


def downgrade() -> None:
    """Remove name fields only when no persisted name would be lost."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM schedules
                WHERE display_name_source IN ('ai', 'manual')
                   OR display_name IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: schedules contains persisted display names';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(op.f("ck_schedules_display_name_matches_source"), "schedules", type_="check")
    op.drop_constraint(op.f("ck_schedules_display_name_source_valid"), "schedules", type_="check")
    op.drop_column("schedules", "display_name_source")
    op.drop_column("schedules", "display_name")
