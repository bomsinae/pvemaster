from datetime import UTC, datetime

import pytest

from app.core.errors import AppError
from app.services.backup_schedule import next_occurrence, parse_schedule


def test_cron_schedule_handles_dst_gap_and_repeated_hour() -> None:
    spring = next_occurrence(
        "30 2 * * *",
        "America/New_York",
        datetime(2026, 3, 8, 0, 0, tzinfo=UTC),
    )
    assert spring == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)

    repeated = next_occurrence(
        "30 1 * * *",
        "America/New_York",
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
    )
    assert repeated == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_cron_schedule_validates_ranges_and_timezone() -> None:
    assert 0 in parse_schedule("*/15 * * * *")[0]
    assert 45 in parse_schedule("*/15 * * * *")[0]
    with pytest.raises(AppError) as invalid_schedule:
        parse_schedule("61 * * * *")
    assert invalid_schedule.value.code == "INVALID_BACKUP_SCHEDULE"
    with pytest.raises(AppError) as invalid_timezone:
        next_occurrence(
            "0 0 * * *",
            "Not/A-Timezone",
            datetime(2026, 7, 26, tzinfo=UTC),
        )
    assert invalid_timezone.value.code == "INVALID_TIMEZONE"


def test_cron_uses_standard_day_of_month_or_day_of_week_semantics() -> None:
    next_run = next_occurrence(
        "0 9 1 * 1",
        "UTC",
        datetime(2026, 7, 26, tzinfo=UTC),
    )
    assert next_run == datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
