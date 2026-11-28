"""The hotwater CLI and its helper: requests for the tick, the anchor, and
the refusal to write as another user."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import hot_water_cli as cli
from src.control import hot_water_boost as hb
from src.control import hot_water_controller as hwc

REPO = Path(__file__).parents[2]
HELPER = REPO / "hotwater.sh"
BASH = shutil.which("bash")


class TestUser:
    def test_the_unit_runs_as_pi(self):
        assert cli.unit_user() == "pi"

    def test_another_user_is_refused(self):
        with pytest.raises(PermissionError):
            cli.check_user("pi", "root")
        cli.check_user("pi", "pi")

    def test_main_refuses_to_write_as_another_user(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "root")
        monkeypatch.setenv(cli.RUN_DIR_ENV, str(tmp_path))
        with pytest.raises(PermissionError):
            cli.main(["circulate", "10"])
        assert not (tmp_path / hwc.REQUEST_FILE).exists()


class TestRequests:
    def test_circulate_writes_a_request_for_the_tick(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "pi")
        monkeypatch.setenv(cli.RUN_DIR_ENV, str(tmp_path))
        assert cli.main(["circulate", "30"]) == 0
        request = json.loads((tmp_path / hwc.REQUEST_FILE).read_text())
        assert request["action"] == "circulate" and request["minutes"] == 30

    def test_pause_writes_a_request(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "pi")
        monkeypatch.setenv(cli.RUN_DIR_ENV, str(tmp_path))
        assert cli.main(["pause", "60"]) == 0
        assert json.loads((tmp_path / hwc.REQUEST_FILE).read_text())["action"] == "pause"

    def test_no_request_without_the_tick_folder(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            cli.write_request(tmp_path / "missing", "pause", 5, 0)

    def test_negative_minutes_are_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "pi")
        monkeypatch.setenv(cli.RUN_DIR_ENV, str(tmp_path))
        assert cli.main(["pause", "-1"]) == 1

    def test_staging_has_its_own_folder(self, monkeypatch):
        monkeypatch.delenv(cli.RUN_DIR_ENV, raising=False)
        monkeypatch.setattr(cli, "REPO", Path("/opt/redhouse-staging"))
        assert cli.run_dir() == Path("/run/redhouse-staging-hot-water")


class TestAnchor:
    def test_local_time_is_stored_in_utc(self):
        assert cli.parse_local("2026-11-27 22:00") == hb.parse_utc("2026-11-27T20:00:00Z")
        assert cli.parse_local("2027-06-04 22:00") == hb.parse_utc("2027-06-04T19:00:00Z")

    def test_the_anchor_command_writes_the_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "pi")
        monkeypatch.setattr(cli, "ANCHOR_FILE", tmp_path / "a.json")
        assert cli.main(["anchor", "2026-11-20 22:01"]) == 0
        assert hb.read_anchor(tmp_path / "a.json") == cli.parse_local("2026-11-20 22:01")

    def test_a_dst_hour_is_a_clear_error(self):
        with pytest.raises(ValueError):
            cli.parse_local("2026-10-25 03:30")
        with pytest.raises(ValueError):
            cli.parse_local("2027-03-28 03:30")

    def test_commission_is_refused_in_staging(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "pi")
        monkeypatch.setattr(cli, "REPO", Path("/opt/redhouse-staging"))
        monkeypatch.setattr(cli, "ANCHOR_FILE", tmp_path / "a.json")
        monkeypatch.setattr(cli, "unit_user", lambda: "pi")
        assert cli.main(["commission"]) == 1

    def test_a_future_anchor_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.getpass, "getuser", lambda: "pi")
        monkeypatch.setattr(cli, "ANCHOR_FILE", tmp_path / "a.json")
        assert cli.main(["anchor", "2099-01-01 00:00"]) == 1
        assert not (tmp_path / "a.json").exists()


class TestStatus:
    def test_status_reads_without_writing(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv(cli.RUN_DIR_ENV, str(tmp_path))
        assert cli.main(["status"]) == 0
        assert "Next expiry" in capsys.readouterr().out


@pytest.mark.skipif(BASH is None, reason="bash not available")
class TestHelper:
    def _install(self, tmp_path):
        bin_dir = tmp_path / "bin"
        python = tmp_path / "install" / "venv" / "bin" / "python"
        stubs = {
            bin_dir / "id": 'echo "$STUB_USER"',
            bin_dir / "sudo": 'echo "via sudo $1 $2"; shift 2; exec "$@"',
        }
        for path, body in list(stubs.items()) + [(python, 'echo "ran $*"')]:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="\n") as f:
                f.write(f"#!/bin/bash\n{body}\n")
            path.chmod(0o755)
        shutil.copy(HELPER, tmp_path / "install" / "hotwater.sh")
        return tmp_path

    def _run(self, install, user, *args):
        env = dict(
            os.environ, PATH=f"{install / 'bin'}{os.pathsep}{os.environ['PATH']}", STUB_USER=user
        )
        helper = install / "install" / "hotwater.sh"
        return subprocess.run(
            [BASH, str(helper), *args], capture_output=True, text=True, env=env, timeout=60
        )

    def test_the_syntax_is_valid(self):
        result = subprocess.run([BASH, "-n", str(HELPER)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_pi_runs_the_cli(self, tmp_path):
        result = self._run(self._install(tmp_path), "pi", "status")
        assert result.returncode == 0 and "hot_water_cli.py status" in result.stdout

    def test_another_user_goes_through_sudo_as_pi(self, tmp_path):
        result = self._run(self._install(tmp_path), "root", "pause", "5")
        assert "via sudo -u pi" in result.stdout and "hot_water_cli.py pause 5" in result.stdout


def test_the_production_deploy_links_the_helper():
    text = (REPO / "deployment" / "deploy_production.sh").read_text(encoding="utf-8")
    assert 'ln -sfn "$DEPLOY_DIR/hotwater.sh" /usr/local/bin/hotwater' in text
