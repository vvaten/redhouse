"""The hot water timer: its units, its RAM folder and the staging mock relay."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import run_hot_water as rhw

REPO = Path(__file__).parents[2]
UNIT = REPO / "deployment" / "systemd" / "redhouse-hot-water.service"
TIMER = REPO / "deployment" / "systemd" / "redhouse-hot-water.timer"
GUARD_TIMER = REPO / "deployment" / "systemd" / "redhouse-load-guard.timer"
BASH = shutil.which("bash")


def _values(text: str, key: str) -> list:
    return [ln.split("=", 1)[1].strip() for ln in text.splitlines() if ln.startswith(f"{key}=")]


class TestUnits:
    def test_the_state_lives_in_ram_between_runs(self):
        text = UNIT.read_text(encoding="utf-8")
        assert _values(text, "RuntimeDirectory") == ["redhouse-hot-water"]
        assert _values(text, "RuntimeDirectoryPreserve") == ["yes"]

    def test_it_starts_after_the_boot_restore(self):
        assert (
            "redhouse-pump-restore.service" in _values(UNIT.read_text(encoding="utf-8"), "After")[0]
        )

    def test_the_deadline_is_under_the_unit_timeout(self):
        timeout = int(_values(UNIT.read_text(encoding="utf-8"), "TimeoutStartSec")[0])
        assert rhw.DEADLINE_S < timeout

    def test_the_timer_uses_the_guard_minutes_at_50_s(self):
        guard = _values(GUARD_TIMER.read_text(encoding="utf-8"), "OnCalendar")[0]
        mine = _values(TIMER.read_text(encoding="utf-8"), "OnCalendar")[0]
        assert mine == guard.replace(":30 UTC", ":50 UTC")

    def test_the_auto_off_timer_covers_a_skipped_minute(self):
        """The timer skips one minute in 15, so two ticks can be 120 s apart."""
        section = yaml.safe_load((REPO / "config" / "config.yaml").read_text(encoding="utf-8"))
        assert section["hot_water"]["relay_on_timer_s"] > 120


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_staging_gets_its_own_runtime_directory(tmp_path):
    text = (REPO / "deployment" / "generate_staging_systemd.sh").read_text(encoding="utf-8")
    block = re.search(r"    sed \\\n(.*?)\"\$src_file\"", text, re.DOTALL)
    exprs = re.findall(r"-e '([^']+)'", block.group(1))
    target = tmp_path / "hw.service"
    target.write_text(UNIT.read_text(encoding="utf-8"), encoding="utf-8")
    args = " ".join(f"-e '{e}'" for e in exprs)
    subprocess.run([BASH, "-c", f"sed -i {args} '{target.as_posix()}'"], check=True, timeout=60)
    assert _values(target.read_text(encoding="utf-8"), "RuntimeDirectory") == [
        "redhouse-staging-hot-water"
    ]


class TestMockRelay:
    def test_the_mock_keeps_its_state_until_its_timer_runs_out(self, tmp_path):
        relay = rhw.mock_relay("u", tmp_path, 1000)
        assert not relay.ison
        relay.on(180)
        rhw.save_mock(relay, tmp_path, 1000, 180)
        assert rhw.mock_relay("u", tmp_path, 1100).ison
        assert not rhw.mock_relay("u", tmp_path, 1181).ison

    def test_an_off_is_kept(self, tmp_path):
        relay = rhw.mock_relay("u", tmp_path, 0)
        relay.off()
        rhw.save_mock(relay, tmp_path, 0, 180)
        assert not rhw.mock_relay("u", tmp_path, 10).ison


class TestHelpers:
    def test_run_dir_is_systemds_folder(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path))
        assert rhw.run_dir() == tmp_path
        monkeypatch.delenv("RUNTIME_DIRECTORY")
        assert rhw.run_dir() == REPO / "data"

    def test_boot_time_is_a_number(self):
        assert rhw.boot_time() >= 0.0

    def test_a_failing_read_returns_its_fallback(self):
        def broken(*args):
            raise TimeoutError("down")

        assert rhw._guarded("x", broken, None)(1, 2) is None
