from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import AppError

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise AppError(422, "INVALID_TIMEZONE", "The backup policy timezone is invalid.") from exc


def parse_schedule(expression: str) -> tuple[set[int], ...]:
    fields = expression.split()
    if len(fields) != 5:
        raise AppError(422, "INVALID_BACKUP_SCHEDULE", "A five-field cron schedule is required.")
    parsed: list[set[int]] = []
    try:
        for raw, (minimum, maximum) in zip(fields, _FIELD_RANGES, strict=True):
            parsed.append(_parse_field(raw, minimum, maximum))
    except (TypeError, ValueError) as exc:
        raise AppError(422, "INVALID_BACKUP_SCHEDULE", "The cron schedule is invalid.") from exc
    return tuple(parsed)


def next_occurrence(expression: str, timezone: str, after: datetime) -> datetime:
    fields = parse_schedule(expression)
    raw_fields = expression.split()
    zone = validate_timezone(timezone)
    cursor = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = cursor + timedelta(days=366 * 2)
    while cursor <= limit:
        local = cursor.astimezone(zone)
        cron_weekday = (local.weekday() + 1) % 7
        day_of_month_matches = local.day in fields[2]
        day_of_week_matches = cron_weekday in fields[4]
        if raw_fields[2] == "*":
            day_matches = day_of_week_matches
        elif raw_fields[4] == "*":
            day_matches = day_of_month_matches
        else:
            day_matches = day_of_month_matches or day_of_week_matches
        if (
            local.minute in fields[0]
            and local.hour in fields[1]
            and local.month in fields[3]
            and day_matches
        ):
            return cursor
        cursor += timedelta(minutes=1)
    raise AppError(
        422,
        "BACKUP_SCHEDULE_OUT_OF_RANGE",
        "The schedule has no occurrence in the supported range.",
    )


def _parse_field(raw: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for item in raw.split(","):
        base, separator, step_raw = item.partition("/")
        step = int(step_raw) if separator else 1
        if step < 1:
            raise ValueError("step must be positive")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_raw, end_raw = base.split("-", 1)
            start, end = int(start_raw), int(end_raw)
        else:
            start = end = int(base)
        if start < minimum or end > maximum or start > end:
            raise ValueError("field is outside its range")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("empty field")
    return values
