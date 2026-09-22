"""Guards the CI workflow against unpinned tooling.

The lint job ran `pip install black ruff` with no versions, so it
drifted to black 26.5.1 while requirements.txt pinned 24.10.0. Twelve
consecutive builds failed on two files nobody had edited, and the local
gate reported clean because it used the pinned version.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"
REQUIREMENTS = REPO / "requirements.txt"

LINTERS = ["black", "ruff"]


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def requirements() -> str:
    return REQUIREMENTS.read_text(encoding="utf-8")


class TestLintersArePinned:
    def test_workflow_exists(self, workflow):
        assert "black" in workflow

    @pytest.mark.parametrize("tool", LINTERS)
    def test_never_installed_bare(self, workflow, tool):
        """A bare name lets an upstream release break the build."""
        for line in workflow.splitlines():
            stripped = line.strip()
            if not stripped.startswith("pip install"):
                continue
            words = stripped.split()
            assert tool not in words, f"{tool} installed unpinned: {stripped}"

    @pytest.mark.parametrize("tool", LINTERS)
    def test_version_comes_from_requirements(self, workflow, tool):
        """One source of truth, so CI and the local gate cannot diverge."""
        assert f"'^{tool}==' requirements.txt" in workflow

    @pytest.mark.parametrize("tool", LINTERS)
    def test_requirements_actually_pins_it(self, requirements, tool):
        """The workflow greps for this line, so it must exist."""
        assert re.search(rf"^{tool}==\S+", requirements, re.M), f"{tool} not pinned"


class TestTestJobUsesRequirements:
    def test_installs_from_requirements(self, workflow):
        assert "pip install -r requirements.txt" in workflow
