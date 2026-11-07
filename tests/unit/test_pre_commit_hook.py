"""The tracked pre-commit hook blocks a staged key in any file, docs included."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "hooks" / "pre-commit"
BASH = shutil.which("bash")
GIT = shutil.which("git")
# Built at run time, so this file holds no key-like literal for gitleaks
FAKE_KEY = "ab" * 16
FAKE_TOKEN = "Zq9" * 20 + "=="

pytestmark = pytest.mark.skipif(BASH is None or GIT is None, reason="bash or git not available")


def _git(tmp_path, *args):
    # Run inside a hook, git exports GIT_DIR and GIT_INDEX_FILE, which
    # would point these commands at the outer repository
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        [GIT, *args], cwd=tmp_path, env=env, check=True, capture_output=True, text=True
    )


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")


def _stage(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    _git(tmp_path, "add", name)


def _run_hook(tmp_path, **extra_env):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(extra_env)
    return subprocess.run(
        [BASH, str(HOOK)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )


@pytest.mark.parametrize(
    "name, line, what",
    [
        ("windpower.py", "FINGRID_API_KEY = " + '"' + FAKE_KEY + '"', "API key"),
        ("SECURITY.md", "FINGRID_API_KEY = " + '"' + FAKE_KEY + '"  # latest', "API key"),
        ("guide.md", "FINGRID_API_KEY=" + FAKE_KEY, "API key"),
        ("fetch.py", 'headers = {"x-api-key": ' + '"' + FAKE_KEY + '"}', "API key"),
        ("my notes.md", "FINGRID_API_KEY=" + FAKE_KEY, "API key"),
        ("influx.py", "INFLUX_TOKEN = " + '"' + FAKE_TOKEN + '"', "token or password"),
    ],
)
def test_a_staged_secret_blocks_the_commit(tmp_path, name, line, what):
    _repo(tmp_path)
    _stage(tmp_path, name, line + "\n")
    result = _run_hook(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"hardcoded {what}" in result.stdout


def test_a_key_added_to_a_renamed_file_blocks_the_commit(tmp_path):
    _repo(tmp_path)
    _stage(tmp_path, "old.py", "x = 1\n" * 20)
    _git(tmp_path, "commit", "-q", "--no-verify", "-m", "base")
    _git(tmp_path, "mv", "old.py", "new.py")
    _stage(tmp_path, "new.py", "x = 1\n" * 20 + "api_key = '" + FAKE_KEY + "'\n")
    result = _run_hook(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "checks_exit, override, hook_exit",
    [(0, "", 0), (1, "", 1), (1, "1", 0)],
)
def test_failing_checks_block_unless_overridden(tmp_path, checks_exit, override, hook_exit):
    _repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    checks = f"import sys\nsys.exit({checks_exit})\n"
    (tmp_path / "scripts" / "run_all_checks.py").write_text(checks, encoding="utf-8")
    _stage(tmp_path, "clean.py", "x = 1\n")
    result = _run_hook(tmp_path, VIRTUAL_ENV="set", REDHOUSE_ALLOW_FAILING_CHECKS=override)
    assert result.returncode == hook_exit, result.stdout + result.stderr


def test_clean_lines_pass_the_security_checks(tmp_path):
    _repo(tmp_path)
    clean = [
        'api_key = os.getenv("FINGRID_API_KEY")',
        "FINGRID_API_KEY=your-fingrid-api-key-here",
        "commit = " + '"' + FAKE_KEY + '"',
        "INFLUXDB_TOKEN=test-token",
    ]
    _stage(tmp_path, "clean.py", "\n".join(clean) + "\n")
    result = _run_hook(tmp_path)
    assert "Security checks passed" in result.stdout, result.stdout + result.stderr
