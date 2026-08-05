import pytest

from app.domain.enums import Status
from app.domain.transitions import validate_transition
from app.errors import BlockedByDependencies, InvalidTransition


def test_not_started_to_in_progress_allowed_when_unblocked():
    validate_transition(Status.NOT_STARTED, Status.IN_PROGRESS, 0)


def test_not_started_to_in_progress_blocked_by_dependencies():
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.NOT_STARTED, Status.IN_PROGRESS, 2)


def test_not_started_to_completed_blocked_by_dependencies():
    """The spec only guards in_progress; leaving completed open is a one-call bypass."""
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.NOT_STARTED, Status.COMPLETED, 1)


def test_in_progress_to_completed_blocked_by_dependencies():
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.IN_PROGRESS, Status.COMPLETED, 1)


def test_in_progress_to_completed_allowed_when_unblocked():
    validate_transition(Status.IN_PROGRESS, Status.COMPLETED, 0)


def test_archiving_is_never_blocked():
    validate_transition(Status.NOT_STARTED, Status.ARCHIVED, 5)


def test_reopening_a_completed_task_is_allowed():
    validate_transition(Status.COMPLETED, Status.NOT_STARTED, 0)


def test_reopening_to_in_progress_is_still_dependency_guarded():
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.COMPLETED, Status.IN_PROGRESS, 1)


def test_unarchiving_is_allowed():
    validate_transition(Status.ARCHIVED, Status.NOT_STARTED, 0)


def test_transition_to_same_status_is_rejected():
    with pytest.raises(InvalidTransition):
        validate_transition(Status.IN_PROGRESS, Status.IN_PROGRESS, 0)
