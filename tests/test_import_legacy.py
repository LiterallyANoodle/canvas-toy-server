"""The pre-database import's planning (the database half is in test_db.py)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app.import_legacy import main, plan, time_from_name

UTC = timezone.utc


@pytest.mark.parametrize("name,expected", [
    ("2024-05-01 13-45-07.png", datetime(2024, 5, 1, 13, 45, 7, tzinfo=UTC)),
    ("20240501_134507.png", datetime(2024, 5, 1, 13, 45, 7, tzinfo=UTC)),
    ("drawing 2024-05-01T13:45:07.png", datetime(2024, 5, 1, 13, 45, 7, tzinfo=UTC)),
    ("Screenshot 2024-05-01 at 1.45.07 PM.png", datetime(2024, 5, 1, 13, 45, 7, tzinfo=UTC)),
    ("2024-05-01 12.05 AM.png", datetime(2024, 5, 1, 0, 5, tzinfo=UTC)),
    ("2024-05-01_1345.png", datetime(2024, 5, 1, 13, 45, tzinfo=UTC)),
    ("2025-10-25_22.49.27.844342.png", datetime(2025, 10, 25, 22, 49, 27, tzinfo=UTC)),   # the real first one
])
def test_times_from_names(name, expected):
    assert time_from_name(name, UTC) == expected


def test_names_without_a_time_or_with_a_bad_one():
    assert time_from_name("dragon.png", UTC) is None
    assert time_from_name("2024-13-45 99-99-99.png", UTC) is None


def test_local_time_is_converted_to_utc():
    assert time_from_name("2024-07-01 12-00-00.png", ZoneInfo("America/Chicago")) == \
        datetime(2024, 7, 1, 17, 0, tzinfo=UTC)


def test_plan_orders_by_time_and_flags_problems(tmp_path, capsys):
    for name, size in [("2024-05-02 10-00-00.png", (500, 500)), ("2024-05-01 09-00-00.png", (120, 80)),
                       ("notes.txt", None), ("mystery.png", (500, 500))]:
        if size:
            Image.new("RGB", size, "white").save(tmp_path / name)
        else:
            (tmp_path / name).write_text("x")
    found, problems = plan(tmp_path, UTC, 1)
    assert [p.name for p, _ in found] == ["2024-05-01 09-00-00.png", "2024-05-02 10-00-00.png"]
    assert len(problems) == 2
    assert main([str(tmp_path)]) == 1                      # problems stop it, even as a dry run
    out = capsys.readouterr().out
    assert "No.   1" in out and "120x80" in out and "mystery.png" in out


def test_dry_run_changes_nothing(tmp_path, capsys):
    Image.new("RGB", (500, 500), "white").save(tmp_path / "2024-05-01 09-00-00.png")
    assert main([str(tmp_path)]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2024-05-01 09-00-00.png"]
