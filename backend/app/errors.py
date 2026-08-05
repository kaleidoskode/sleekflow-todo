from typing import Any


class DomainError(Exception):
    """Base for errors that map onto an RFC 9457 Problem Details response."""

    code: str = "DOMAIN_ERROR"
    title: str = "Domain error"
    status_code: int = 422

    def __init__(self, detail: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra or {}


class NotFound(DomainError):
    code = "NOT_FOUND"
    title = "Resource not found"
    status_code = 404


class VersionConflict(DomainError):
    code = "VERSION_CONFLICT"
    title = "Version conflict"
    status_code = 409


class PreconditionRequired(DomainError):
    code = "PRECONDITION_REQUIRED"
    title = "If-Match header required"
    status_code = 428


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"
    title = "Invalid status transition"
    status_code = 422


class BlockedByDependencies(DomainError):
    code = "BLOCKED_BY_DEPENDENCIES"
    title = "Blocked by incomplete dependencies"
    status_code = 422


class DependencyCycle(DomainError):
    code = "DEPENDENCY_CYCLE"
    title = "Dependency would create a cycle"
    status_code = 422
