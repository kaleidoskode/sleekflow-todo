"""Pure status-transition rules. No database, no HTTP."""

from app.domain.enums import Status
from app.errors import BlockedByDependencies, InvalidTransition

# Targets that require every dependency to be complete. `archived` is excluded
# deliberately: parking a blocked task is always legitimate.
DEPENDENCY_GUARDED_TARGETS = frozenset({Status.IN_PROGRESS, Status.COMPLETED})


def validate_transition(current: Status, target: Status, unmet_dependency_count: int) -> None:
    """Raise if moving from `current` to `target` is not permitted.

    The machine is permissive by design — reopening and unarchiving are both allowed.
    The dependency rule is the only real constraint.
    """
    if current is target:
        raise InvalidTransition(f"Todo is already in status '{target}'.")

    if target in DEPENDENCY_GUARDED_TARGETS and unmet_dependency_count > 0:
        raise BlockedByDependencies(
            f"Cannot move to '{target}' while {unmet_dependency_count} "
            f"dependenc{'y is' if unmet_dependency_count == 1 else 'ies are'} incomplete.",
            extra={"unmet_dependency_count": unmet_dependency_count},
        )
