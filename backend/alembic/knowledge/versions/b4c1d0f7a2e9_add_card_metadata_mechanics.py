"""add card_metadata.mechanics

A literal restatement of the card's rules text, written by the metadata
generator *before* any of the judgment fields it shares a row with -- see
`knowledge_pipeline/metadata_generator/schema.py` for why it is the
blueprint's first field, and `docs/data-model.md#cardmetadata` for what it
is for.

Added NOT NULL with a transient empty-string default so any metadata rows
already in the corpus survive the upgrade, then the default is dropped so the
schema matches the model exactly and a later autogenerate has nothing to say
about it. An empty `mechanics` therefore means "written before this column
existed" -- the metadata generator never writes one (`min_length=1`), and a
re-run overwrites the row in place.

The two steps are deliberately **not** in one `batch_alter_table` block. Batch
mode collapses everything in the block into a single SQLite table rebuild, so
the transient default would never exist at the moment rows are copied across,
and the upgrade fails on any database that already holds metadata:
`IntegrityError: NOT NULL constraint failed`. The add has to be its own
statement, with the backfill already committed, before the default is dropped.
Postgres -- the real target -- is happy either way, which is exactly why this
is worth a comment: the failure only shows up against SQLite and only when the
table is non-empty.

Revision ID: b4c1d0f7a2e9
Revises: 586d2486ef8f
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c1d0f7a2e9'
down_revision: Union[str, Sequence[str], None] = '586d2486ef8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Step one, on its own: ADD COLUMN with a default, which both dialects
    # apply in place and which backfills every existing row.
    op.add_column(
        'card_metadata',
        sa.Column('mechanics', sa.String(), nullable=False, server_default=''),
    )

    # Step two, in its own batch block so the rebuild above has already
    # happened: drop the default now that nothing relies on it.
    with op.batch_alter_table('card_metadata', schema=None) as batch_op:
        batch_op.alter_column(
            'mechanics',
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('card_metadata', schema=None) as batch_op:
        batch_op.drop_column('mechanics')
