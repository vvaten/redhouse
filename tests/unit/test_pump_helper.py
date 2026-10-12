"""The redhouse-pump helper runs control_pump.py safely as pi."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
HELPER = REPO / "pump.sh"
PRODUCTION_DEPLOY = REPO / "deployment" / "deploy_production.sh"
BASH = shutil.which("bash")

STUBS = {
    "systemctl": 'echo "$STUB_EXECUTOR"; echo "$STUB_RESTORE"; exit 3',
    "date": 'echo "$STUB_MINUTE"',
    "id": 'echo "$STUB_USER"',
    "sudo": 'echo "via sudo $1 $2"; shift 2; exec "$@"',
}


@pytest.fixture
def install(tmp_path):
    """A copy of the helper in a fake install, with stub commands first on PATH."""
    bin_dir = tmp_path / "bin"
    python = tmp_path / "install" / "venv" / "bin" / "python"
    for path, body in [(bin_dir / name, body) for name, body in STUBS.items()] + [
        (python, 'echo "ran $*"')
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="\n") as f:
            f.write(f"#!/bin/bash\n{body}\n")
        path.chmod(0o755)
    shutil.copy(HELPER, tmp_path / "install" / "pump.sh")
    return tmp_path


def _run(install, *args, executor="inactive", restore="inactive", minute="30", user="pi"):
    env = dict(
        os.environ,
        PATH=f"{install / 'bin'}{os.pathsep}{os.environ['PATH']}",
        STUB_EXECUTOR=executor,
        STUB_RESTORE=restore,
        STUB_MINUTE=minute,
        STUB_USER=user,
    )
    helper = install / "install" / "pump.sh"
    return subprocess.run(
        [BASH, str(helper), *args], capture_output=True, text=True, env=env, timeout=60
    )


@pytest.mark.skipif(BASH is None, reason="bash not available")
class TestPumpHelper:
    def test_the_syntax_is_valid(self):
        result = subprocess.run([BASH, "-n", str(HELPER)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_a_command_runs_control_pump(self, install):
        result = _run(install, "ON")
        assert result.returncode == 0, result.stderr
        assert "control_pump.py ON" in result.stdout

    def test_another_user_goes_through_sudo_as_pi(self, install):
        result = _run(install, "ALE", user="root")
        assert result.returncode == 0, result.stderr
        assert "via sudo -u pi" in result.stdout
        assert "control_pump.py ALE" in result.stdout

    @pytest.mark.parametrize("busy", [{"executor": "activating"}, {"restore": "activating"}])
    def test_refused_while_the_executor_or_the_boot_restore_runs(self, install, busy):
        result = _run(install, "EVU", **busy)
        assert result.returncode == 1
        assert "ran" not in result.stdout

    @pytest.mark.parametrize("state", ["active", "inactive", "failed"])
    def test_runs_when_neither_is_running(self, install, state):
        result = _run(install, "EVU", executor=state, restore=state)
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("minute", ["14", "29", "44", "59"])
    def test_refused_in_the_minute_before_a_quarter_hour(self, install, minute):
        result = _run(install, "ON", minute=minute)
        assert result.returncode == 1
        assert "ran" not in result.stdout

    @pytest.mark.parametrize(
        "args", [("status",), ("--dry-run", "status"), ("ON", "--dry-run"), ("-h",), ()]
    )
    def test_what_writes_no_relay_always_runs(self, install, args):
        result = _run(install, *args, executor="activating", minute="59")
        assert result.returncode == 0, result.stderr
        assert "control_pump.py" in result.stdout


def test_the_production_deploy_links_the_helper():
    text = PRODUCTION_DEPLOY.read_text(encoding="utf-8")
    assert 'ln -sfn "$DEPLOY_DIR/pump.sh" /usr/local/bin/redhouse-pump' in text
