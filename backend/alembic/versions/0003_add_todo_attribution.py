"""add todo attribution

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07 17:38:21.888264

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('todos', sa.Column('created_by_id', sa.UUID(), nullable=True))
    op.add_column('todos', sa.Column('updated_by_id', sa.UUID(), nullable=True))

    # Named explicitly: autogenerate emitted None, which leaves PostgreSQL to
    # invent a name that downgrade() then cannot drop.
    op.create_foreign_key(
        'fk_todos_created_by', 'todos', 'users', ['created_by_id'], ['id'], ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_todos_updated_by', 'todos', 'users', ['updated_by_id'], ['id'], ondelete='SET NULL'
    )

    # Attribution is read on every list page ("updated by X"), so index the
    # lookup rather than scanning.
    op.create_index('ix_todos_updated_by', 'todos', ['updated_by_id'])

    # Autogenerate wanted to DROP ix_users_username_lower here. That index is
    # functional (lower(username)) so it is invisible to the model comparison,
    # and dropping it would silently allow "Ada" and "ada" to both register
    # while AuthService looks accounts up case-insensitively. Deliberately
    # not dropped.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_todos_updated_by', table_name='todos')
    op.drop_constraint('fk_todos_updated_by', 'todos', type_='foreignkey')
    op.drop_constraint('fk_todos_created_by', 'todos', type_='foreignkey')
    op.drop_column('todos', 'updated_by_id')
    op.drop_column('todos', 'created_by_id')
