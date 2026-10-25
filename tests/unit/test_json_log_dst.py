"""JSON backup logs keep both passes of the repeated autumn hour apart."""

import datetime
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from freezegun import freeze_time

from src.common.json_logger import JSONDataLogger
from src.tools import replay_json_logs as replay


def test_the_two_passes_of_0315_get_different_file_names(tmp_path):
    logger = JSONDataLogger("shelly_em3", log_dir=str(tmp_path))
    first = logger._get_log_filename(datetime.datetime(2026, 10, 25, 3, 15, 0, fold=0))
    second = logger._get_log_filename(datetime.datetime(2026, 10, 25, 3, 15, 0, fold=1))
    assert (first.name, second.name) == ("20261025_031500.json", "20261025_031500-2.json")


def test_a_logged_time_carries_its_utc_offset(tmp_path):
    logger = JSONDataLogger("shelly_em3", log_dir=str(tmp_path))
    assert logger.log_data({"x": 1})
    entry = logger.load_log(next(Path(logger.log_dir).glob("*.json")))
    assert datetime.datetime.fromisoformat(entry["timestamp"]).tzinfo is not None


def test_recent_logs_keep_their_real_order_through_the_repeated_hour(tmp_path):
    logger = JSONDataLogger("shelly_em3", log_dir=str(tmp_path))
    names = ["20261025_035000.json", "20261025_031000-2.json"]  # 00:50 and 01:10 UTC
    for name, utc_minute in zip(names, (50, 70)):
        path = Path(logger.log_dir) / name
        path.write_text("{}")
        moment = datetime.datetime(2026, 10, 25, tzinfo=datetime.timezone.utc).timestamp()
        os.utime(path, (moment + 60 * utc_minute, moment + 60 * utc_minute))

    with freeze_time("2026-10-25 02:00:00"):
        recent = logger.get_recent_logs(days=1)

    assert [path.name for path in recent] == list(reversed(names))


def test_the_name_filter_reads_a_second_pass_name():
    assert replay._parse_log_filename_time(Path("20261025_031500-2.json")) == "20261025_031500"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "logged, utc",
    [("2026-10-25T03:15:00+03:00", (0, 15)), ("2026-10-25T03:15:00+02:00", (1, 15))],
    ids=["summer-pass", "winter-pass"],
)
async def test_each_pass_replays_at_its_own_moment(tmp_path, logged, utc):
    with patch("src.tools.replay_json_logs.JSONDataLogger") as logger_class:
        logger_class.return_value = Mock(
            load_log=Mock(return_value={"timestamp": logged, "data": {"s": 1.0}, "metadata": {}})
        )
        with patch("src.data_collection.temperature.write_temperatures_to_influx") as write:
            write.return_value = True
            assert await replay.replay_log_file(tmp_path / "x.json", "temperature", dry_run=False)
    assert write.call_args.kwargs["timestamp"] == datetime.datetime(2026, 10, 25, *utc)
