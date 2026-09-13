"""Tests for InfluxDB degradation detection.

Fixtures reproduce the two measured states of the real instance on
2026-09-13: degraded after 162 days of uptime, and healthy after a
restart.
"""

from unittest.mock import MagicMock, patch

from src.monitoring import influx_health as ih

DEGRADED = """\
go_gc_duration_seconds{quantile="0.5"} 0.257196836
go_gc_duration_seconds{quantile="1"} 3.526113996
go_goroutines 18696
go_memstats_sys_bytes 2.618152024e+09
storage_tsm_files_total{bucket="a",id="1"} 1
""" + "".join(
    'storage_tsm_files_total{bucket="a",id="%d"} 1\n' % i for i in range(2, 2937)
)

HEALTHY = """\
go_gc_duration_seconds{quantile="0.5"} 0.000161597
go_gc_duration_seconds{quantile="1"} 0.12
go_goroutines 18510
go_memstats_sys_bytes 1.63e+09
""" + "".join(
    'storage_tsm_files_total{bucket="a",id="%d"} 1\n' % i for i in range(1, 1730)
)


class TestMetricParsing:
    def test_reads_gauge(self):
        assert ih._gauge(DEGRADED, "go_memstats_sys_bytes") == 2618152024.0
        assert ih._gauge(DEGRADED, "go_goroutines") == 18696.0

    def test_missing_gauge_is_none(self):
        assert ih._gauge(DEGRADED, "no_such_metric") is None

    def test_reads_gc_median_not_max(self):
        """The max is a since-restart high-water mark, so use the median."""
        assert ih._gc_pause_median(DEGRADED) == 0.257196836

    def test_counts_shards(self):
        assert ih._shard_count(DEGRADED) == 2936
        assert ih._shard_count(HEALTHY) == 1729


class TestMetricWarnings:
    def test_degraded_state_warns_on_all_three(self):
        out = ih.metric_warnings(DEGRADED)
        assert len(out) == 3
        joined = " ".join(out)
        assert "GC pauses" in joined
        assert "reserving" in joined
        assert "shards" in joined

    def test_healthy_state_is_silent(self):
        assert ih.metric_warnings(HEALTHY) == []

    def test_goroutines_are_not_a_signal(self):
        """They barely moved across the restart, so must not warn."""
        assert "goroutine" not in " ".join(ih.metric_warnings(DEGRADED)).lower()

    def test_absent_metrics_do_not_warn(self):
        assert ih.metric_warnings("# nothing useful here\n") == []


class TestProbeQuery:
    def test_returns_elapsed_on_success(self):
        influx = MagicMock()
        elapsed = ih.probe_query_seconds(influx)
        assert elapsed is not None and elapsed >= 0
        influx.query_api.query.assert_called_once()

    def test_returns_none_on_failure(self):
        influx = MagicMock()
        influx.query_api.query.side_effect = Exception("read timed out")
        assert ih.probe_query_seconds(influx) is None


class TestCheckInfluxdbPerformance:
    def test_slow_probe_warns(self):
        influx = MagicMock()
        with patch.object(ih, "probe_query_seconds", return_value=2.5):
            with patch.object(ih, "_fetch_metrics", return_value=HEALTHY):
                failures, warnings = ih.check_influxdb_performance(influx, "http://x", "t")
        assert failures == []
        assert any("slow" in w for w in warnings)

    def test_failed_probe_is_a_failure_not_a_warning(self):
        influx = MagicMock()
        with patch.object(ih, "probe_query_seconds", return_value=None):
            with patch.object(ih, "_fetch_metrics", return_value=HEALTHY):
                failures, warnings = ih.check_influxdb_performance(influx, "http://x", "t")
        assert any("probe query failed" in f for f in failures)

    def test_healthy_instance_is_clean(self):
        influx = MagicMock()
        with patch.object(ih, "probe_query_seconds", return_value=0.03):
            with patch.object(ih, "_fetch_metrics", return_value=HEALTHY):
                failures, warnings = ih.check_influxdb_performance(influx, "http://x", "t")
        assert failures == [] and warnings == []

    def test_degraded_instance_warns(self):
        influx = MagicMock()
        with patch.object(ih, "probe_query_seconds", return_value=1.98):
            with patch.object(ih, "_fetch_metrics", return_value=DEGRADED):
                failures, warnings = ih.check_influxdb_performance(influx, "http://x", "t")
        assert failures == []
        assert len(warnings) == 4

    def test_unreachable_metrics_warns_and_stops(self):
        influx = MagicMock()
        with patch.object(ih, "probe_query_seconds", return_value=0.03):
            with patch.object(ih, "_fetch_metrics", return_value=None):
                failures, warnings = ih.check_influxdb_performance(influx, "http://x", "t")
        assert failures == []
        assert warnings == ["Cannot read InfluxDB metrics endpoint"]


class TestThresholdsSitBetweenTheMeasuredStates:
    """A threshold outside the range would never warn, or always warn."""

    def test_latency(self):
        assert 0.03 < ih.QUERY_LATENCY_WARN_SECONDS < 1.98

    def test_gc_pause(self):
        assert 0.00016 < ih.GC_PAUSE_WARN_SECONDS < 0.257

    def test_sys_bytes(self):
        assert 1.63e9 < ih.SYS_BYTES_WARN < 2.618e9

    def test_shards(self):
        assert 1729 < ih.SHARD_COUNT_WARN < 2936
