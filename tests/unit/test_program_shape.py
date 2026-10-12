"""A program that parses but has the wrong structure must count as unusable."""

import copy

import pytest

from src.control.program_shape import program_is_well_formed

GOOD = {
    "version": "2.0.0",
    "loads": {
        "geothermal_pump": {
            "schedule": [{"timestamp": 1000, "local_time": "00:00", "command": "ON"}]
        },
        "garage_heater": {
            "schedule": [{"timestamp": 2000, "local_time": "01:00", "command": "OFF"}]
        },
    },
}


def _broken(change):
    program = copy.deepcopy(GOOD)
    change(program)
    return program


def test_a_generated_program_is_well_formed():
    assert program_is_well_formed(GOOD) is True


@pytest.mark.parametrize(
    "program",
    [
        None,
        [],
        5,
        {"loads": {}},
        {"version": "2.0.0"},
        {"version": "2.0.0", "loads": None},
        _broken(lambda p: p["loads"].update(geothermal_pump={})),
        _broken(lambda p: p["loads"].update(geothermal_pump={"schedule": None})),
        _broken(lambda p: p["loads"]["geothermal_pump"]["schedule"].append(None)),
        _broken(lambda p: p["loads"]["geothermal_pump"]["schedule"].append({"command": "ON"})),
        _broken(lambda p: p["loads"]["geothermal_pump"]["schedule"][0].update(timestamp="1")),
        _broken(lambda p: p["loads"]["geothermal_pump"]["schedule"][0].update(timestamp=True)),
        _broken(lambda p: p["loads"]["geothermal_pump"]["schedule"][0].update(command="OFF")),
        _broken(lambda p: p["loads"]["garage_heater"]["schedule"][0].pop("command")),
        _broken(lambda p: p["loads"]["geothermal_pump"]["schedule"][0].pop("local_time")),
        _broken(lambda p: p["loads"]["geothermal_pump"]["schedule"][0].update(local_time=0)),
    ],
)
def test_a_broken_program_is_not(program):
    assert program_is_well_formed(program) is False
