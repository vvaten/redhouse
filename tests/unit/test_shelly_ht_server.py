"""The Shelly H&T receiver keeps wibatemp's format, and forgets nothing on restart."""

import errno
import json
import os
import socket
import stat
import threading
import time
import urllib.request
from http.server import HTTPServer
from unittest.mock import patch

import pytest

from src.data_collection import shelly_ht_server as server
from src.data_collection import temperature


def test_a_report_is_named_by_its_id_and_the_last_octet_of_its_sender():
    report = server.parse_report("/?id=shellyht-02D824&temp=10.12&hum=80", "192.168.1.180")
    assert report == ("shellyht-02D824-180", {"temp": 10.12, "hum": 80.0})


def test_a_report_without_an_id_names_no_sensor():
    assert server.parse_report("/?temp=10.0", "192.168.1.180") is None


def test_an_unreadable_value_is_ignored_and_the_other_kept():
    report = server.parse_report("/?id=x&temp=abc&hum=45.1", "192.168.1.191")
    assert report == ("x-191", {"hum": 45.1})


def test_a_report_updates_only_its_own_values():
    state: dict = {}
    assert server.record(state, "s-1", {"temp": 20.0, "hum": 40.0}, 100.0)
    assert server.record(state, "s-1", {"hum": 41.0}, 200.0)
    assert state == {"s-1": {"temp": 20.0, "hum": 41.0, "updated": 200.0}}


def test_a_report_without_values_changes_nothing():
    state: dict = {}
    assert not server.record(state, "s-1", {}, 100.0)
    assert state == {}


def test_the_file_is_replaced_whole_and_leaves_no_temporary_file(tmp_path):
    status = tmp_path / "data" / "status.json"
    server.write_state({"a": {"temp": 1.0, "hum": None, "updated": 1.0}}, status)
    server.write_state({"b": {"temp": 2.0, "hum": None, "updated": 2.0}}, status)
    assert json.loads(status.read_text()) == {"b": {"temp": 2.0, "hum": None, "updated": 2.0}}
    assert [p.name for p in status.parent.iterdir()] == ["status.json"]


def test_a_failed_write_keeps_the_old_file(tmp_path):
    status = tmp_path / "status.json"
    server.write_state({"a": {"temp": 1.0, "hum": None, "updated": 1.0}}, status)
    with pytest.raises(TypeError):
        server.write_state({"a": {"temp": object()}}, status)
    assert json.loads(status.read_text())["a"]["temp"] == 1.0
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"]


def _entry(temp, updated):
    return {"temp": temp, "hum": None, "updated": updated}


def test_a_restart_keeps_the_newest_reading_of_every_sensor(tmp_path):
    ours, legacy = tmp_path / "ours.json", tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"a": _entry(1.0, 100.0)}))
    assert server.load_state(ours, legacy) == {"a": _entry(1.0, 100.0)}
    ours.write_text(json.dumps({"a": _entry(2.0, 200.0), "b": _entry(3.0, 200.0)}))
    assert server.load_state(ours, legacy) == {"a": _entry(2.0, 200.0), "b": _entry(3.0, 200.0)}


def test_after_a_rollback_no_sensor_is_lost(tmp_path):
    """wibatemp's receiver starts empty: its newer file holds only sensor a."""
    ours, legacy = tmp_path / "ours.json", tmp_path / "legacy.json"
    ours.write_text(json.dumps({"a": _entry(1.0, 100.0), "b": _entry(2.0, 100.0)}))
    legacy.write_text(json.dumps({"a": _entry(5.0, 300.0)}))
    assert server.load_state(ours, legacy) == {"a": _entry(5.0, 300.0), "b": _entry(2.0, 100.0)}


def test_an_unreadable_or_half_written_file_adds_nothing(tmp_path):
    ours, legacy = tmp_path / "ours.json", tmp_path / "legacy.json"
    ours.write_text(json.dumps({"a": _entry(1.0, 100.0)}))
    legacy.write_text('{"a": {"temp": 9.0, "hum"')
    assert server.load_state(ours, legacy) == {"a": _entry(1.0, 100.0)}
    assert server.load_state(tmp_path / "none.json", tmp_path / "missing.json") == {}
    # A directory in the file's place cannot be read
    unreadable = tmp_path / "unreadable.json"
    unreadable.mkdir()
    assert server.load_state(ours, unreadable) == {"a": _entry(1.0, 100.0)}


def test_a_file_of_another_shape_adds_nothing(tmp_path):
    ours, legacy = tmp_path / "ours.json", tmp_path / "legacy.json"
    ours.write_text(json.dumps({"a": _entry(1.0, 100.0), "b": {"temp": 2.0, "updated": "x"}}))
    legacy.write_text(json.dumps([1, 2, 3]))
    assert server.load_state(ours, legacy) == {"a": _entry(1.0, 100.0)}


def test_a_report_over_http_reaches_the_file_the_collector_reads(tmp_path):
    status = tmp_path / "status.json"
    httpd = HTTPServer(("127.0.0.1", 0), server.make_handler({}, status))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/?id=TopFloorBathroom&temp=20.7&hum=45.1"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.read() == b"ok"
    finally:
        httpd.shutdown()
        httpd.server_close()

    loaded = temperature.load_shelly_ht_data(str(status))
    assert loaded["TopFloorBathroom-1"]["temp"] == 20.7
    assert loaded["TopFloorBathroom-1"]["hum"] == 45.1
    assert time.time() - loaded["TopFloorBathroom-1"]["updated"] < 60


def test_the_collector_merges_both_receivers_files(tmp_path):
    """After a rollback: wibatemp's file has the newer a-1 and lacks b-2."""
    now = time.time()
    ours, legacy = tmp_path / "ours.json", tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"a-1": _entry(5.0, now - 10)}))
    ours.write_text(json.dumps({"a-1": _entry(1.0, now - 100), "b-2": _entry(2.0, now - 100)}))
    with (
        patch.object(temperature, "SHELLY_HT_STATUS_FILE", ours),
        patch.object(temperature, "LEGACY_STATUS_FILE", legacy),
    ):
        loaded = temperature.load_shelly_ht_data()
    assert (loaded["a-1"]["temp"], loaded["b-2"]["temp"]) == (5.0, 2.0)


def test_a_taken_port_ends_the_receiver_with_a_clear_error(caplog):
    # Windows lets a second server bind a taken port, so the bind is made to fail
    taken = OSError(errno.EADDRINUSE, "Address already in use")
    with (
        patch.object(server, "load_state", return_value={}),
        patch.object(server, "HTTPServer", side_effect=taken),
    ):
        assert server.main() == 1
    assert "Port 8008 is taken" in caplog.text


def test_any_other_bind_error_is_raised():
    denied = OSError(errno.EACCES, "Permission denied")
    with (
        patch.object(server, "load_state", return_value={}),
        patch.object(server, "HTTPServer", side_effect=denied),
        pytest.raises(OSError),
    ):
        server.main()


def test_a_stalled_connection_does_not_block_the_next_report(tmp_path):
    assert server.ReportHandler.timeout == 10
    handler = server.make_handler({}, tmp_path / "status.json")
    handler.timeout = 0.5
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    stalled = socket.create_connection(("127.0.0.1", httpd.server_port))
    # Half a request line, so the server is surely busy with this connection
    stalled.sendall(b"GET /")
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/?id=x&temp=1.0"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.read() == b"ok"
    finally:
        stalled.close()
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.skipif(os.name == "nt", reason="Windows has no 0644 file mode")
def test_the_file_is_readable_by_everyone_like_wibatemps(tmp_path):
    status = tmp_path / "status.json"
    server.write_state({}, status)
    assert stat.S_IMODE(status.stat().st_mode) == 0o644
