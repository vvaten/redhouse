"""Helsinki time, which the house and the electricity market run on."""

import datetime

import pytz

HELSINKI = pytz.timezone("Europe/Helsinki")


def helsinki_date(date_offset: int = 0) -> datetime.date:
    """The Helsinki date date_offset days from today, whatever the host zone."""
    return datetime.datetime.now(HELSINKI).date() + datetime.timedelta(days=date_offset)
