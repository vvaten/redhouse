"""CheckWatt API responses for tests, shaped like the real ones."""

import datetime
from typing import Optional

from src.common.local_time import HELSINKI

MINUTE = datetime.timedelta(minutes=1)


def _wall(instant: datetime.datetime) -> str:
    return instant.astimezone(HELSINKI).replace(tzinfo=None).isoformat()


def _utc(instant: datetime.datetime) -> str:
    return instant.astimezone(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _meter(minutes: list, end_offset: datetime.timedelta, values: Optional[list]) -> list:
    """Measurements dated by wall time; a Date already given is left out, as
    the API does with the second pass of a repeated hour."""
    measurements: list[dict] = []
    for minute in minutes:
        date = _wall(minute + end_offset)
        if all(m["Date"] != date for m in measurements):
            measurements.append({"Date": date, "Value": float(minute.timestamp())})
    for measurement, value in zip(measurements, values or []):
        measurement["Value"] = value
    return measurements


def checkwatt_response(
    start_utc: datetime.datetime, end_utc: datetime.datetime, values: Optional[list] = None
) -> dict:
    """A delta response for the real minutes from start_utc to end_utc. The
    SoC is dated by the start of each minute, the five deltas by its end.
    Without values, each value is the epoch of its minute."""
    count = int((end_utc - start_utc) / MINUTE)
    minutes = [start_utc + i * MINUTE for i in range(count)]
    meters = []
    for col in range(6):
        # A delta needs its whole minute, so the last one has none yet
        measured = minutes[:-1] if col else minutes
        offset = MINUTE if col else datetime.timedelta(0)
        measurements = _meter(measured, offset, values[col] if values else None)
        meters.append(
            {
                "DateFromUTC": _utc(start_utc),
                "DateToUTC": _utc(end_utc),
                "DateFromLocal": _wall(start_utc),
                "DateToLocal": _wall(end_utc),
                "Measurements": measurements,
            }
        )
    return {
        "Grouping": "delta",
        "DateFrom": _wall(start_utc),
        "DateTo": _wall(end_utc),
        "Meters": meters,
    }
