import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.domain.enums import Priority, Status
from app.models.todo import Todo


async def test_todo_defaults(session):
    todo = Todo(name="Write the plan")
    session.add(todo)
    await session.commit()

    stored = (await session.execute(select(Todo))).scalar_one()
    assert stored.status is Status.NOT_STARTED
    assert stored.priority == Priority.MEDIUM
    assert stored.version == 1
    assert stored.unmet_dependency_count == 0
    assert stored.deleted_at is None
    assert stored.id is not None


async def test_self_dependency_is_rejected_by_constraint(session):
    todo = Todo(name="A")
    session.add(todo)
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(
            text("INSERT INTO todo_dependencies (todo_id, depends_on_id) VALUES (:i, :i)"),
            {"i": todo.id},
        )
        await session.commit()
    # The IntegrityError leaves the session in a failed-transaction state;
    # roll it back so the fixture's teardown (TRUNCATE) doesn't hit
    # PendingRollbackError.
    await session.rollback()


async def test_status_enum_sorts_in_lifecycle_order(session):
    """Sorting by status must be lifecycle order, not alphabetical."""
    for name, status in [
        ("d", Status.ARCHIVED),
        ("b", Status.IN_PROGRESS),
        ("a", Status.NOT_STARTED),
        ("c", Status.COMPLETED),
    ]:
        session.add(Todo(name=name, status=status))
    await session.commit()

    ordered = (await session.execute(select(Todo).order_by(Todo.status))).scalars().all()
    assert [t.name for t in ordered] == ["a", "b", "c", "d"]
