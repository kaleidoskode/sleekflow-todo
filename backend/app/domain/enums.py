from enum import IntEnum, StrEnum


class Status(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Priority(IntEnum):
    LOW = 10
    MEDIUM = 20
    HIGH = 30


class RecurrenceUnit(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
