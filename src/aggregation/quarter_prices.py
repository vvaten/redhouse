"""The spot prices in force in an aggregation window, and the parts of the
window that the electricity bill prices as one."""

import datetime
from typing import Optional

QUARTER = datetime.timedelta(minutes=15)
HOUR = datetime.timedelta(hours=1)
PRICE_LOOKBACK = HOUR
# Prices are per quarter from local midnight 2025-09-30, per hour before
QUARTER_PRICES_FROM = datetime.datetime(2025, 9, 29, 21, tzinfo=datetime.timezone.utc)
# The house meter nets import and export per hour until it is replaced;
# the replacement time must be a whole hour, such as local midnight
QUARTER_NETTING_FROM: Optional[datetime.datetime] = None
PRICE_FIELDS = ("price_total", "price_sell", "price_withtax")


def spot_points(tables, buyback_margin: Optional[float] = None) -> list:
    """The points of a pivoted spot price query, sorted by time. With a
    buyback margin in c/kWh, a point without price_sell gets one from its
    spot price, as the spot price collector computes it."""
    points = []
    for table in tables:
        for record in table.records:
            point = {"time": record.get_time(), **{f: record.values.get(f) for f in PRICE_FIELDS}}
            spot = record.values.get("price")
            if point["price_sell"] is None and buyback_margin is not None and spot is not None:
                point["price_sell"] = round(spot - 0.01 * buyback_margin, 4)
            points.append(point)
    return sorted(points, key=lambda p: p["time"])


def _period(point_time) -> datetime.timedelta:
    return QUARTER if point_time >= QUARTER_PRICES_FROM else HOUR


def quarter_prices(points: list, window_start, window_end) -> list:
    """For each quarter of the window, the last price at or before its start
    if its period still runs; the price fields are None where none does."""
    quarters = []
    latest = None
    i = 0
    start = window_start
    while start < window_end:
        while i < len(points) and points[i]["time"] <= start:
            latest = points[i]
            i += 1
        if latest is not None and start - latest["time"] < _period(latest["time"]):
            prices = {f: latest[f] for f in PRICE_FIELDS}
        else:
            prices = dict.fromkeys(PRICE_FIELDS)
        quarters.append({"time": start, **prices})
        start += QUARTER
    return quarters


def nets_by_hour(moment) -> bool:
    return QUARTER_NETTING_FROM is None or moment < QUARTER_NETTING_FROM


def floor_hour(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def ceil_hour(moment):
    hour = floor_hour(moment)
    return hour if hour == moment else hour + HOUR


def _hour_price(quarters: list) -> dict:
    """The mean of the hour's quarter prices; unknown when one is missing."""
    prices: dict = {}
    for field in PRICE_FIELDS:
        values = [q[field] for q in quarters]
        prices[field] = None if None in values else sum(values) / len(values)
    return prices


def bill_periods(points: list, window_start, window_end) -> list:
    """The parts of the window that the bill prices as one, with that price:
    the hour at its mean quarter price while the meter nets by the hour,
    else each quarter at its own. Points must cover the window's hours."""
    periods = []
    hour = floor_hour(window_start)
    quarters = quarter_prices(points, hour, ceil_hour(window_end))
    while hour < window_end:
        in_hour = [q for q in quarters if hour <= q["time"] < hour + HOUR]
        if nets_by_hour(hour):
            parts = [(hour, hour + HOUR, _hour_price(in_hour))]
        else:
            parts = [
                (q["time"], q["time"] + QUARTER, {f: q[f] for f in PRICE_FIELDS}) for q in in_hour
            ]
        for start, stop, prices in parts:
            start, stop = max(start, window_start), min(stop, window_end)
            if start < stop:
                periods.append({"start": start, "stop": stop, **prices})
        hour += HOUR
    return periods


def mean_price(periods: list, field: str) -> Optional[float]:
    """The time-weighted mean of a price field over the periods that have it."""
    weighted = [
        (p[field], (p["stop"] - p["start"]).total_seconds())
        for p in periods
        if p[field] is not None
    ]
    seconds = sum(w for _, w in weighted)
    return sum(v * w for v, w in weighted) / seconds if seconds else None
