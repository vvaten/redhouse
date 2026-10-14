"""No production code may switch the house ventilation until the fire window
exists: the hardware layer's circulation pump relay is the ventilation, and a
restart while the oven fire starts pulls smoke indoors (docs/AC_CONTROL_SPEC.md)."""

import re
from pathlib import Path

REPO = Path(__file__).parents[2]
# The relay methods are defined here; a definition is not a call
HARDWARE_LAYER = {"hardware_interface.py", "hardware_implementations.py"}
CALL = re.compile(r"\bcontrol_circulation_pump\s*\(|[?&]turn=(on|off)")


def _production_python_files():
    roots = [REPO / "src", REPO / "scripts", REPO / "deployment"]
    files = [p for root in roots for p in root.rglob("*.py")]
    return files + list(REPO.glob("*.py"))


def test_only_the_hardware_layer_mentions_the_relay_switch():
    offenders = []
    for path in _production_python_files():
        if path.name in HARDWARE_LAYER:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CALL.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert offenders == [], offenders


def test_the_scan_sees_the_files_it_must_guard():
    names = {p.name for p in _production_python_files()}
    assert {"pump_controller.py", "program_executor.py", "control_pump.py"} <= names
