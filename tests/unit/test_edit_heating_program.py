"""Unit tests for heating program editor."""

import sys
import unittest
from pathlib import Path

# Import functions from the editor script
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from edit_heating_program import add_entry, list_program  # noqa: E402


class TestEditHeatingProgram(unittest.TestCase):
    """Test heating program editor functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.sample_program = {
            "program_date": "2024-11-06",
            "program_version": "2.0.0",
            "generated_at": "2024-11-05T20:00:00+02:00",
            "simulation_mode": False,
            "input_parameters": {
                "avg_temperature_c": -5.2,
                "min_temperature_c": -8.5,
                "max_temperature_c": -2.1,
            },
            "planning_results": {
                "total_heating_hours_needed": 18.5,
                "total_evu_off_intervals": 2,
                "estimated_total_cost_eur": 5.23,
                "cheapest_interval_price": 2.15,
                "most_expensive_interval_price": 12.84,
            },
            "loads": {
                "geothermal_pump": {
                    "enabled": True,
                    "priority": 1,
                    "power_kw": 3.0,
                    "total_intervals_on": 2,
                    "total_hours_on": 2.0,
                    "estimated_cost_eur": 0.13,
                    "schedule": [
                        {
                            "timestamp": 1730851200,  # 2024-11-06 00:00:00
                            "utc_time": "2024-11-05T22:00:00+00:00",
                            "local_time": "2024-11-06T00:00:00+02:00",
                            "command": "ON",
                            "duration_minutes": 60,
                            "reason": "cheap_electricity",
                            "spot_price_total_c_kwh": 2.15,
                            "solar_prediction_kwh": 0.0,
                            "priority_score": 2.15,
                            "estimated_cost_eur": 0.065,
                        },
                        {
                            "timestamp": 1730854800,  # 2024-11-06 01:00:00
                            "utc_time": "2024-11-05T23:00:00+00:00",
                            "local_time": "2024-11-06T01:00:00+02:00",
                            "command": "ALE",
                            "duration_minutes": None,
                            "reason": "auto_mode",
                        },
                    ],
                }
            },
        }

    def test_add_non_overlapping_entry(self):
        """Test adding entry that doesn't overlap with existing entries."""
        program = self.sample_program.copy()
        modified = add_entry(program, "10:00", "11:00", "ON")

        schedule = modified["loads"]["geothermal_pump"]["schedule"]
        self.assertEqual(len(schedule), 3)

        # Verify entries are sorted by timestamp
        timestamps = [e["timestamp"] for e in schedule]
        self.assertEqual(timestamps, sorted(timestamps))

        # Verify new entry exists
        new_entry = [e for e in schedule if e["local_time"].startswith("2024-11-06T10:00")]
        self.assertEqual(len(new_entry), 1)
        self.assertEqual(new_entry[0]["command"], "ON")
        self.assertEqual(new_entry[0]["duration_minutes"], 60)

    def test_add_overlapping_entry_same_start(self):
        """Test adding entry with same start time as existing entry."""
        program = self.sample_program.copy()

        # Add ON at 01:00-02:00, which overlaps with ALE at 01:00
        modified = add_entry(program, "01:00", "02:00", "ON")

        schedule = modified["loads"]["geothermal_pump"]["schedule"]

        # Should NOT have duplicate timestamps
        timestamps = [e["timestamp"] for e in schedule]
        self.assertEqual(
            len(timestamps), len(set(timestamps)), "Should not have duplicate timestamps"
        )

    def test_add_overlapping_entry_mid_period(self):
        """Test adding entry that overlaps middle of existing period."""
        program = self.sample_program.copy()

        # Add EVU at 00:30-01:30, which overlaps ON (00:00-01:00) and ALE (01:00)
        # This should split/remove conflicting entries
        modified = add_entry(program, "00:30", "01:30", "EVU")

        schedule = modified["loads"]["geothermal_pump"]["schedule"]

        # Verify no overlapping periods
        for i in range(len(schedule) - 1):
            entry = schedule[i]
            next_entry = schedule[i + 1]

            if entry["duration_minutes"]:
                entry_end = entry["timestamp"] + (entry["duration_minutes"] * 60)
                self.assertLessEqual(
                    entry_end, next_entry["timestamp"], f"Entry {i} overlaps with entry {i+1}"
                )

    def test_add_entry_spanning_existing_entries(self):
        """Test adding long entry that spans multiple existing entries."""
        program = self.sample_program.copy()

        # Add EVU from 00:00 to 05:00, should replace all existing entries
        modified = add_entry(program, "00:00", "05:00", "EVU")

        schedule = modified["loads"]["geothermal_pump"]["schedule"]

        # Should have the new EVU entry, and no overlaps
        evu_entries = [
            e
            for e in schedule
            if e["command"] == "EVU" and e["local_time"].startswith("2024-11-06T00:00")
        ]
        self.assertGreater(len(evu_entries), 0, "Should have EVU entry starting at 00:00")
        self.assertEqual(evu_entries[0]["duration_minutes"], 300)

    def test_invalid_time_range(self):
        """Test adding entry with end time before start time."""
        program = self.sample_program.copy()

        with self.assertRaises(ValueError) as ctx:
            add_entry(program, "12:00", "11:00", "ON")

        self.assertIn("after start", str(ctx.exception))

    def test_invalid_time_format(self):
        """Test adding entry with invalid time format."""
        program = self.sample_program.copy()

        with self.assertRaises(ValueError):
            add_entry(program, "25:00", "26:00", "ON")

    def test_schedule_remains_sorted(self):
        """Test that schedule remains sorted after multiple additions."""
        program = self.sample_program.copy()

        # Add entries in random order
        program = add_entry(program, "20:00", "21:00", "ON")
        program = add_entry(program, "05:00", "06:00", "ON")
        program = add_entry(program, "15:00", "16:00", "EVU")
        program = add_entry(program, "10:00", "11:00", "ON")

        schedule = program["loads"]["geothermal_pump"]["schedule"]
        timestamps = [e["timestamp"] for e in schedule]

        self.assertEqual(
            timestamps, sorted(timestamps), "Schedule should remain sorted by timestamp"
        )

    def test_zero_duration_entry(self):
        """Test adding entry with same start and end time."""
        program = self.sample_program.copy()

        with self.assertRaises(ValueError) as ctx:
            add_entry(program, "12:00", "12:00", "ON")

        self.assertIn("after start", str(ctx.exception))

    def test_entry_at_midnight(self):
        """Test adding entry at midnight."""
        program = self.sample_program.copy()

        # This should replace the existing 00:00 entry
        modified = add_entry(program, "00:00", "01:00", "EVU")

        schedule = modified["loads"]["geothermal_pump"]["schedule"]
        midnight_entries = [e for e in schedule if e["timestamp"] == 1730851200]

        # Should not have duplicates at midnight
        self.assertLessEqual(len(midnight_entries), 1)

    def test_entry_late_evening(self):
        """Test adding entry late in the evening."""
        program = self.sample_program.copy()

        modified = add_entry(program, "23:00", "23:59", "ON")

        schedule = modified["loads"]["geothermal_pump"]["schedule"]
        late_entries = [e for e in schedule if e["local_time"].startswith("2024-11-06T23:00")]

        self.assertEqual(len(late_entries), 1)
        self.assertEqual(late_entries[0]["duration_minutes"], 59)

    def test_multiple_overlaps_removed(self):
        """Test that multiple overlapping entries are handled."""
        program = self.sample_program.copy()

        # First add several entries
        program = add_entry(program, "10:00", "11:00", "ON")
        program = add_entry(program, "11:00", "12:00", "ON")
        program = add_entry(program, "12:00", "13:00", "ON")

        # Now add a long EVU that should remove/adjust all three
        modified = add_entry(program, "09:30", "13:30", "EVU")

        schedule = modified["loads"]["geothermal_pump"]["schedule"]

        # Verify no overlaps exist
        for i in range(len(schedule) - 1):
            entry = schedule[i]
            next_entry = schedule[i + 1]

            if entry["duration_minutes"]:
                entry_end = entry["timestamp"] + (entry["duration_minutes"] * 60)
                self.assertLessEqual(
                    entry_end,
                    next_entry["timestamp"],
                    f"Overlap detected between entries {i} and {i+1}",
                )

    def test_list_program_does_not_crash(self):
        """Test that list_program doesn't crash with valid program."""
        # This is mainly to ensure the display function works
        try:
            list_program(self.sample_program)
        except Exception as e:
            self.fail(f"list_program raised exception: {e}")


if __name__ == "__main__":
    unittest.main()
