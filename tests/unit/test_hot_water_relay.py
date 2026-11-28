"""The hot water circulation relay: auto-off on every ON, readback, and never
the ventilation relay."""

from unittest.mock import Mock, patch

import pytest
import requests

from src.control.hardware_implementations import (
    GarageRelay,
    HotWaterRelay,
    MockHotWaterRelay,
    RelayState,
)

URL = "http://192.168.1.188/relay/0"


def _reply(body):
    response = Mock()
    response.json.return_value = body
    response.raise_for_status.return_value = None
    return response


def _relay(**kwargs):
    return HotWaterRelay(URL, sleep=lambda s: None, **kwargs)


class TestCommands:
    @patch("src.control.hardware_implementations.requests.get")
    def test_on_always_carries_the_timer(self, get):
        get.return_value = _reply({"ison": True, "has_timer": True})
        assert _relay().on(180) == RelayState(True, True)
        assert get.call_args[0][0] == URL + "?turn=on&timer=180"

    def test_on_without_a_timer_is_refused(self):
        with pytest.raises(ValueError):
            _relay().on(0)

    @patch("src.control.hardware_implementations.requests.get")
    def test_an_on_that_reads_back_without_its_timer_fails(self, get):
        get.return_value = _reply({"ison": True, "has_timer": False})
        assert _relay().on(180) is None

    @patch("src.control.hardware_implementations.requests.get")
    def test_off_reads_back_the_state(self, get):
        get.return_value = _reply({"ison": False, "has_timer": False})
        assert _relay().off() == RelayState(False, False)
        assert get.call_args[0][0] == URL + "?turn=off"

    @patch("src.control.hardware_implementations.requests.get")
    def test_state_tells_a_manual_on_by_its_missing_timer(self, get):
        get.return_value = _reply({"ison": True, "has_timer": False})
        assert _relay().state() == RelayState(True, False)
        assert get.call_args[0][0] == URL

    @patch("src.control.hardware_implementations.requests.get")
    def test_a_reply_without_ison_is_no_answer(self, get):
        get.return_value = _reply({"error": "x"})
        assert _relay(tries=1).state() is None

    @patch("src.control.hardware_implementations.requests.get")
    def test_default_off_writes_the_power_on_setting(self, get):
        get.return_value = _reply({"default_state": "off", "ison": False})
        assert _relay().default_off() == "off"
        assert get.call_args[0][0] == "http://192.168.1.188/settings/relay/0?default_state=off"


class TestTries:
    @patch("src.control.hardware_implementations.requests.get")
    def test_the_tick_makes_one_short_try(self, get):
        get.side_effect = requests.ConnectionError("down")
        relay = _relay(tries=1, timeout_s=3.0)
        assert relay.on(180) is None
        assert get.call_count == 1
        assert get.call_args[1]["timeout"] == 3.0

    @patch("src.control.hardware_implementations.requests.get")
    def test_the_cli_retries(self, get):
        get.side_effect = [requests.ConnectionError("down"), _reply({"ison": False})]
        assert _relay(tries=3).off() == RelayState(False, False)
        assert get.call_count == 2


class TestNotTheVentilation:
    @pytest.mark.parametrize("url", ["http://192.168.1.5/relay/0", "http://192.168.1.5"])
    def test_the_ventilation_host_is_refused(self, url):
        with pytest.raises(ValueError):
            HotWaterRelay(url)

    def test_a_host_that_only_starts_like_it_is_accepted(self):
        assert HotWaterRelay("http://192.168.1.55/relay/0").url.endswith("/relay/0")


class TestGarageRelayUnchanged:
    @patch("src.control.hardware_implementations.requests.get")
    def test_garage_off_still_needs_its_flip_back(self, get):
        get.return_value = _reply({"ison": False, "has_timer": False})
        assert GarageRelay(URL, sleep=lambda s: None).set(False, 4500) is None


class TestMock:
    def test_mock_records_and_reports(self):
        relay = MockHotWaterRelay(URL)
        assert relay.on(180) == RelayState(True, True)
        assert relay.state() == RelayState(True, True)
        assert relay.off() == RelayState(False, False)
        assert relay.commands == [("on", 180), ("off", None)]
        assert relay.default_off() == "off"
