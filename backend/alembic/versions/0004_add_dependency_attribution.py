"""add dependency attribution

Records who created each dependency edge, and when.

Attribution sits on the edge rather than on the dependent todo: adding a link
changes only `unmet_dependency_count`, which is deliberately maintained
without bumping `version`. Writing `updated_by_id` on the todo would therefore
change who it claims last touched it without any version change to detect,
leaving two clients quietly disagreeing.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-08

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: str | Sequence[str] | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('todo_dependencies', sa.Column('created_by_id', sa.UUID(), nullable=True))
    op.add_column(
        'todo_dependencies',
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )

    # Named explicitly for the same reason as 0003: autogenerate emits None,
    # PostgreSQL invents a name, and downgrade() then cannot drop it.
    op.create_foreign_key(
        'fk_dependencies_created_by',
        'todo_dependencies',
        'users',
        ['created_by_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_dependencies_created_by', 'todo_dependencies', type_='foreignkey')
    op.drop_column('todo_dependencies', 'created_at')
    op.drop_column('todo_dependencies', 'created_by_id')
