"""The ping logger writes what wibatemp's did: the same fields with the same types."""

import datetime
from unittest.mock import MagicMock, patch

import numpy as np

from src.data_collection import pinglogger

# Field names of a point wibatemp's pinglogger wrote on 2026-11-01
WIBATEMP_FIELDS = {
    "packet_loss",
    "packets_lost",
    "stats_lost_ratio",
    "stats_packets_lost",
    "stats_packets_returned",
    "stats_packets_sent",
    "stats_success_ratio",
    "rtt_avg_ms",
    "rtt_std_ms",
    "rtt_min_ms",
    "rtt_p5_ms",
    "rtt_p10_ms",
    "rtt_p25_ms",
    "rtt_p50_ms",
    "rtt_p75_ms",
    "rtt_p90_ms",
    "rtt_p95_ms",
    "rtt_max_ms",
}


def _output(rtts, sent=20, extra=""):
    lines = ["PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data."]
    lines += [
        f"64 bytes from 8.8.8.8: icmp_seq={i} ttl=118 time={t} ms" for i, t in enumerate(rtts, 1)
    ]
    lines += [extra, "", "--- 8.8.8.8 ping statistics ---"]
    lost = round(100 * (sent - len(rtts)) / sent)
    lines += [f"{sent} packets transmitted, {len(rtts)} received, {lost}% packet loss, time 9502ms"]
    return "\n".join(lines) + "\n"


def test_a_full_run_has_wibatemps_fields_and_types():
    rtts = [1.95, 1.52, 1.83, 2.10] * 5
    fields = pinglogger.parse_ping(_output(rtts))
    assert set(fields) == WIBATEMP_FIELDS
    counts = {"stats_packets_lost", "stats_packets_returned", "stats_packets_sent"}
    for name in WIBATEMP_FIELDS:
        assert isinstance(fields[name], int if name in counts else float), name
    assert fields["rtt_p50_ms"] == round(float(np.percentile(rtts, 50)), 3)
    assert fields["rtt_std_ms"] == round(float(np.std(rtts)), 3)


def test_lost_replies_are_counted_as_wibatemp_counted_them():
    fields = pinglogger.parse_ping(_output([2.0] * 8))
    assert fields["packet_loss"] == fields["packets_lost"] == fields["stats_lost_ratio"] == 0.6
    assert fields["stats_success_ratio"] == 0.4
    assert (fields["stats_packets_lost"], fields["stats_packets_returned"]) == (12, 8)


def test_a_duplicate_or_too_fast_reply_is_left_out_of_the_times():
    duplicate = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=50.0 ms (DUP!)"
    fields = pinglogger.parse_ping(_output([0.4, 2.0, 3.0], extra=duplicate))
    assert (fields["rtt_min_ms"], fields["rtt_max_ms"]) == (2.0, 3.0)
    assert fields["stats_packets_returned"] == 3


def test_a_run_with_every_reply_lost_still_writes_the_loss():
    fields = pinglogger.parse_ping(_output([]))
    assert fields["packet_loss"] == 1.0
    assert not [name for name in fields if name.startswith("rtt_")]


def test_no_route_gives_no_point():
    assert pinglogger.parse_ping("connect: Network is unreachable\n") is None


def test_the_written_point_keeps_the_counts_integers():
    fields = pinglogger.parse_ping(_output([2.0] * 8))
    with (
        patch.object(pinglogger, "get_config") as config,
        patch.object(pinglogger, "InfluxClient") as client,
    ):
        config.return_value = MagicMock(influxdb_bucket_network="network", influxdb_org="area51")
        pinglogger.write_point(fields, datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc))
    kwargs = client.return_value.write_with_retry.call_args.kwargs
    assert kwargs["bucket"] == "network"
    line = kwargs["record"].to_line_protocol()
    assert line.startswith("network ")
    assert "stats_packets_sent=20i" in line and "stats_packets_lost=12i" in line
    assert "packet_loss=0.6" in line


def test_main_writes_a_run_and_refuses_one_without_statistics():
    with (
        patch.object(pinglogger, "run_ping", return_value=_output([2.0] * 20)),
        patch.object(pinglogger, "write_point") as write,
    ):
        assert pinglogger.main([]) == 0
    assert write.call_count == 1
    with (
        patch.object(pinglogger, "run_ping", return_value="ping: unknown host\n"),
        patch.object(pinglogger, "write_point") as write,
    ):
        assert pinglogger.main([]) == 1
    assert write.call_count == 0


def test_a_dry_run_pings_but_writes_nothing():
    with (
        patch.object(pinglogger, "run_ping", return_value=_output([2.0] * 20)) as ping,
        patch.object(pinglogger, "write_point") as write,
    ):
        assert pinglogger.main(["--dry-run"]) == 0
    assert ping.call_count == 1
    assert write.call_count == 0
