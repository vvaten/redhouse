"""Unit tests for heating curve calculations."""

import unittest

from src.control.heating_curve import HeatingCurve


class TestHeatingCurve(unittest.TestCase):
    """Test cases for HeatingCurve class."""

    def setUp(self):
        """Set up test fixtures with known curve values."""
        self.curve = HeatingCurve({-20: 10.0, 0: 6.0, 16: 2.0})

    def test_default_curve_initialization(self):
        """Test that default curve is loaded correctly."""
        curve_points = self.curve.get_curve_points()

        self.assertIn(-20, curve_points)
        self.assertIn(0, curve_points)
        self.assertIn(16, curve_points)
        self.assertEqual(curve_points[-20], 10.0)
        self.assertEqual(curve_points[0], 6.0)
        self.assertEqual(curve_points[16], 2.0)

    def test_custom_curve_initialization(self):
        """Test initialization with custom curve points."""
        custom_curve = {-10: 10.0, 5: 6.0, 20: 2.0}
        curve = HeatingCurve(custom_curve)

        curve_points = curve.get_curve_points()
        self.assertEqual(curve_points, custom_curve)

    def test_curve_with_too_few_points_raises_error(self):
        """Test that curve with less than 2 points raises ValueError."""
        with self.assertRaises(ValueError):
            HeatingCurve({10: 5.0})

    def test_exact_curve_point_temperature(self):
        """Test calculation at exact curve point temperatures."""
        hours = self.curve.calculate_heating_hours(-20)
        self.assertEqual(hours, 10.0)

        hours = self.curve.calculate_heating_hours(0)
        self.assertEqual(hours, 6.0)

        hours = self.curve.calculate_heating_hours(16)
        self.assertEqual(hours, 2.0)

    def test_interpolation_between_points(self):
        """Test linear interpolation between curve points."""
        # Between -20C and 0C (midpoint at -10C)
        # Slope = -4/20 = -0.2, so 10 + (10 * -0.2) = 8.0
        hours = self.curve.calculate_heating_hours(-10)
        self.assertEqual(hours, 8.0)

        # Between 0C and 16C (midpoint at 8C)
        # Slope = -4/16 = -0.25, so 6 + (8 * -0.25) = 4.0
        hours = self.curve.calculate_heating_hours(8)
        self.assertEqual(hours, 4.0)

    def test_interpolation_quarter_points(self):
        """Test interpolation at quarter points."""
        # At -15C: 10 + (5 * -0.2) = 9.0
        hours = self.curve.calculate_heating_hours(-15)
        self.assertEqual(hours, 9.0)

        # At -5C: 10 + (15 * -0.2) = 7.0
        hours = self.curve.calculate_heating_hours(-5)
        self.assertEqual(hours, 7.0)

    def test_extrapolation_below_range(self):
        """Test extrapolation for temperatures below curve range."""
        # At -30C (10 degrees below -20C)
        # Slope = -0.2, so 10 + (-10 * -0.2) = 12.0
        hours = self.curve.calculate_heating_hours(-30)
        self.assertEqual(hours, 12.0)

    def test_extrapolation_above_range(self):
        """Test extrapolation for temperatures above curve range."""
        # At 20C (4 degrees above 16C)
        # Slope = -0.25, so 2 + (4 * -0.25) = 1.0
        hours = self.curve.calculate_heating_hours(20)
        self.assertEqual(hours, 1.0)

    def test_rounding_to_quarter_hour(self):
        """Test rounding to 15-minute (0.25 hour) intervals."""
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.0), 5.0)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.1), 5.0)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.12), 5.0)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.13), 5.25)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.25), 5.25)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.37), 5.25)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.38), 5.5)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.5), 5.5)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.62), 5.5)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.63), 5.75)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.75), 5.75)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.87), 5.75)
        self.assertEqual(HeatingCurve.round_to_quarter_hour(5.88), 6.0)

    def test_minimum_heating_threshold(self):
        """Test that very small heating hours are rounded to zero."""
        # Slope above 16C = -0.25
        # At 24C: 2 - (8 * 0.25) = 0.0 -> below threshold -> 0
        hours = self.curve.calculate_heating_hours(24)
        self.assertEqual(hours, 0.0)

        # At 23C: 2 - (7 * 0.25) = 0.25 -> exactly at threshold
        hours = self.curve.calculate_heating_hours(23)
        self.assertEqual(hours, 0.25)

    def test_warm_weather_no_heating(self):
        """Test that warm temperatures result in minimal heating."""
        # At 18C: 2 - (2 * 0.25) = 1.5
        hours = self.curve.calculate_heating_hours(18)
        self.assertEqual(hours, 1.5)

        # At 20C: 2 - (4 * 0.25) = 1.0
        hours = self.curve.calculate_heating_hours(20)
        self.assertEqual(hours, 1.0)

    def test_cold_weather_max_heating(self):
        """Test that cold temperatures result in significant heating."""
        for temp in [-25, -20, -15, -10]:
            hours = self.curve.calculate_heating_hours(temp)
            self.assertGreaterEqual(hours, 8.0)

    def test_set_curve_points(self):
        """Test updating curve points."""
        new_curve = {-15: 11.0, 5: 7.0, 18: 3.0}
        self.curve.set_curve_points(new_curve)

        curve_points = self.curve.get_curve_points()
        self.assertEqual(curve_points, new_curve)

        hours = self.curve.calculate_heating_hours(5)
        self.assertEqual(hours, 7.0)

    def test_set_invalid_curve_raises_error(self):
        """Test that setting invalid curve raises ValueError."""
        with self.assertRaises(ValueError):
            self.curve.set_curve_points({10: 5.0})  # Only 1 point

    def test_typical_helsinki_winter_day(self):
        """Test typical Helsinki winter temperature."""
        # At -5C: 10 + (15 * -0.2) = 7.0
        hours = self.curve.calculate_heating_hours(-5)
        self.assertEqual(hours, 7.0)

    def test_typical_helsinki_spring_day(self):
        """Test typical Helsinki spring temperature."""
        # At 10C: 6 + (10 * -0.25) = 3.5
        hours = self.curve.calculate_heating_hours(10)
        self.assertEqual(hours, 3.5)

    def test_typical_helsinki_autumn_day(self):
        """Test typical Helsinki autumn temperature."""
        # At 5C: 6 + (5 * -0.25) = 4.75
        hours = self.curve.calculate_heating_hours(5)
        self.assertEqual(hours, 4.75)

    def test_negative_heating_hours_handled(self):
        """Test that negative calculated hours are handled correctly."""
        curve = HeatingCurve({0: 8.0, 10: 2.0, 20: 0.0})

        hours = curve.calculate_heating_hours(30)
        self.assertGreaterEqual(hours, 0.0)


if __name__ == "__main__":
    unittest.main()
