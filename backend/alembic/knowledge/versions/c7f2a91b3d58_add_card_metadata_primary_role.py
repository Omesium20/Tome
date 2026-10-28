"""add card_metadata.primary_role

Which of a card's `roles` it is most defined by, written by the metadata
generator's first pass -- see
`knowledge_pipeline/metadata_generator/schema.py` for why it is asked for
directly instead of read off `roles[0]`, and
`docs/data-model.md#cardmetadata` for what it is for.

Added **nullable**, which is why this migration needs none of the transient
server_default dance `b4c1d0f7a2e9` does for `mechanics`. Null is a real value
here rather than a backfill artefact: a card can genuinely have no role in the
closed vocabulary (a basic land), so there is no honest value to backfill an
existing row with and no reason to invent one. A row written before this
column existed is indistinguishable from a roleless card, and both are
corrected the same way -- by a re-run, which overwrites the row in place.

Revision ID: c7f2a91b3d58
Revises: b4c1d0f7a2e9
Create Date: 2026-10-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f2a91b3d58'
down_revision: Union[str, Sequence[str], None] = 'b4c1d0f7a2e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'card_metadata',
        sa.Column('primary_role', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('card_metadata', schema=None) as batch_op:
        batch_op.drop_column('primary_role')
