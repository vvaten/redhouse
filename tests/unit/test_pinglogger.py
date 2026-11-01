"""The ping logger writes what wibatemp's did: the same fields with the same types."""

import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.common.config_validator import ConfigValidationError
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


# A run of iputils 20210202 on the Pi as user pi, 2026-11-01
PI_OUTPUT = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=2.10 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=118 time=2.05 ms
64 bytes from 8.8.8.8: icmp_seq=3 ttl=118 time=1.87 ms
64 bytes from 8.8.8.8: icmp_seq=4 ttl=118 time=2.15 ms
64 bytes from 8.8.8.8: icmp_seq=5 ttl=118 time=1.83 ms
64 bytes from 8.8.8.8: icmp_seq=6 ttl=118 time=1.61 ms
64 bytes from 8.8.8.8: icmp_seq=7 ttl=118 time=2.12 ms
64 bytes from 8.8.8.8: icmp_seq=8 ttl=118 time=1.76 ms
64 bytes from 8.8.8.8: icmp_seq=9 ttl=118 time=1.96 ms
64 bytes from 8.8.8.8: icmp_seq=10 ttl=118 time=1.64 ms
64 bytes from 8.8.8.8: icmp_seq=11 ttl=118 time=1.84 ms
64 bytes from 8.8.8.8: icmp_seq=12 ttl=118 time=1.90 ms
64 bytes from 8.8.8.8: icmp_seq=13 ttl=118 time=2.16 ms
64 bytes from 8.8.8.8: icmp_seq=14 ttl=118 time=1.93 ms
64 bytes from 8.8.8.8: icmp_seq=15 ttl=118 time=1.77 ms
64 bytes from 8.8.8.8: icmp_seq=16 ttl=118 time=2.04 ms
64 bytes from 8.8.8.8: icmp_seq=17 ttl=118 time=1.71 ms
64 bytes from 8.8.8.8: icmp_seq=18 ttl=118 time=2.03 ms
64 bytes from 8.8.8.8: icmp_seq=19 ttl=118 time=2.23 ms
64 bytes from 8.8.8.8: icmp_seq=20 ttl=118 time=1.99 ms

--- 8.8.8.8 ping statistics ---
20 packets transmitted, 20 received, 0% packet loss, time 9519ms
rtt min/avg/max/mdev = 1.607/1.933/2.232/0.174 ms
"""


def test_real_output_from_the_pi_gives_every_field():
    fields = pinglogger.parse_ping(PI_OUTPUT)
    assert set(fields) == WIBATEMP_FIELDS
    assert (fields["stats_packets_sent"], fields["stats_packets_returned"]) == (20, 20)
    assert (fields["rtt_min_ms"], fields["rtt_max_ms"]) == (1.61, 2.23)
    # ping prints each reply to 0.01 ms, so the mean is near its own summary
    assert abs(fields["rtt_avg_ms"] - 1.933) < 0.01


def test_the_summary_is_read_with_duplicates_and_errors():
    duplicates = PI_OUTPUT.replace("20 received,", "20 received, +1 duplicates,")
    assert pinglogger.parse_ping(duplicates)["stats_packets_returned"] == 20
    errors = (
        "From 192.168.1.1 icmp_seq=1 Destination Net Unreachable\n\n"
        "--- 8.8.8.8 ping statistics ---\n"
        "20 packets transmitted, 0 received, +20 errors, 100% packet loss, time 19015ms\n"
    )
    assert pinglogger.parse_ping(errors)["stats_success_ratio"] == 0.0


def test_ping_runs_in_the_c_locale():
    """A Finnish locale prints 2,10 ms, which the reply pattern would miss."""
    with patch.object(pinglogger.subprocess, "run") as run:
        run.return_value = MagicMock(stdout=PI_OUTPUT, stderr="")
        assert pinglogger.run_ping() == PI_OUTPUT
    assert run.call_args.kwargs["env"]["LC_ALL"] == "C"


def test_staging_cannot_write_to_the_production_bucket(monkeypatch):
    monkeypatch.setenv("STAGING_MODE", "true")
    with (
        patch.object(pinglogger, "get_config") as config,
        patch.object(pinglogger, "InfluxClient") as client,
    ):
        config.return_value = MagicMock(influxdb_bucket_network="network", influxdb_org="area51")
        with pytest.raises(ConfigValidationError):
            pinglogger.write_point(pinglogger.parse_ping(PI_OUTPUT), datetime.datetime.now())
    client.return_value.write_with_retry.assert_not_called()


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
