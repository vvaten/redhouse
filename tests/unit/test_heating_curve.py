"""Unit tests for heating curve calculations."""

import unittest

from src.control.heating_curve import HeatingCurve


class TestHeatingCurve(unittest.TestCase):
    """Test cases for HeatingCurve class."""

    def setUp(self):
        """Set up test fixtures with known curve values."""
        self.curve = HeatingCurve({-20: 10.0, 0: 5.0, 16: 2.0})

    def test_default_curve_initialization(self):
        """Test that default curve is loaded correctly."""
        curve_points = self.curve.get_curve_points()

        self.assertIn(-20, curve_points)
        self.assertIn(0, curve_points)
        self.assertIn(16, curve_points)
        self.assertEqual(curve_points[-20], 10.0)
        self.assertEqual(curve_points[0], 5.0)
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
        # At -20C: should be exactly 10 hours
        hours = self.curve.calculate_heating_hours(-20)
        self.assertEqual(hours, 10.0)

        # At 0C: should be exactly 5 hours
        hours = self.curve.calculate_heating_hours(0)
        self.assertEqual(hours, 5.0)

        # At 16C: should be exactly 2 hours
        hours = self.curve.calculate_heating_hours(16)
        self.assertEqual(hours, 2.0)

    def test_interpolation_between_points(self):
        """Test linear interpolation between curve points."""
        # Between -20C and 0C (midpoint at -10C)
        # Should be (10 + 5) / 2 = 7.5 hours
        hours = self.curve.calculate_heating_hours(-10)
        self.assertEqual(hours, 7.5)

        # Between 0C and 16C (midpoint at 8C)
        # Should be (5 + 2) / 2 = 3.5 hours
        hours = self.curve.calculate_heating_hours(8)
        self.assertEqual(hours, 3.5)

    def test_interpolation_quarter_points(self):
        """Test interpolation at quarter points."""
        # At -15C (1/4 between -20 and 0)
        # Should be 10 - (1/4 * 5) = 8.75 hours
        hours = self.curve.calculate_heating_hours(-15)
        self.assertEqual(hours, 8.75)

        # At -5C (3/4 between -20 and 0)
        # Should be 10 - (3/4 * 5) = 6.25 hours
        hours = self.curve.calculate_heating_hours(-5)
        self.assertEqual(hours, 6.25)

    def test_extrapolation_below_range(self):
        """Test extrapolation for temperatures below curve range."""
        # At -30C (10 degrees below -20C)
        # Slope between -20 and 0 is (5-10)/(0-(-20)) = -5/20 = -0.25
        # So: 10 + (-10 * -0.25) = 10 + 2.5 = 12.5 hours
        hours = self.curve.calculate_heating_hours(-30)
        self.assertEqual(hours, 12.5)

    def test_extrapolation_above_range(self):
        """Test extrapolation for temperatures above curve range."""
        # At 20C (4 degrees above 16C)
        # Slope between 0 and 16 is (2-5)/(16-0) = -3/16 = -0.1875
        # So: 2 + (4 * -0.1875) = 2 - 0.75 = 1.25 hours
        hours = self.curve.calculate_heating_hours(20)
        self.assertEqual(hours, 1.25)

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
        # With curve (16C -> 2h), slope = -3/16 = -0.1875
        # At 25C: 2 - (9 * 0.1875) = 2 - 1.6875 = 0.3125 -> rounds to 0.25
        hours = self.curve.calculate_heating_hours(25)
        self.assertEqual(hours, 0.25)

        # At 27C: 2 - (11 * 0.1875) = 2 - 2.0625 = -0.0625 -> below threshold -> 0
        hours = self.curve.calculate_heating_hours(27)
        self.assertEqual(hours, 0.0)

    def test_warm_weather_no_heating(self):
        """Test that warm temperatures result in minimal heating."""
        # At 18C: 2 - (2 * 0.1875) = 2 - 0.375 = 1.625 -> rounds to 1.5
        hours = self.curve.calculate_heating_hours(18)
        self.assertEqual(hours, 1.5)

        # At 20C: 2 - (4 * 0.1875) = 2 - 0.75 = 1.25
        hours = self.curve.calculate_heating_hours(20)
        self.assertEqual(hours, 1.25)

        # At 25C: 0.3125 -> rounds to 0.25
        hours = self.curve.calculate_heating_hours(25)
        self.assertEqual(hours, 0.25)

    def test_cold_weather_max_heating(self):
        """Test that cold temperatures result in significant heating."""
        # At very cold temperatures, should heat many hours
        for temp in [-25, -20, -15, -10]:
            hours = self.curve.calculate_heating_hours(temp)
            self.assertGreaterEqual(hours, 7.0)

    def test_set_curve_points(self):
        """Test updating curve points."""
        new_curve = {-15: 11.0, 5: 7.0, 18: 3.0}
        self.curve.set_curve_points(new_curve)

        curve_points = self.curve.get_curve_points()
        self.assertEqual(curve_points, new_curve)

        # Test calculation with new curve
        hours = self.curve.calculate_heating_hours(5)
        self.assertEqual(hours, 7.0)

    def test_set_invalid_curve_raises_error(self):
        """Test that setting invalid curve raises ValueError."""
        with self.assertRaises(ValueError):
            self.curve.set_curve_points({10: 5.0})  # Only 1 point

    def test_typical_helsinki_winter_day(self):
        """Test typical Helsinki winter temperature."""
        # Typical winter day: -5C
        hours = self.curve.calculate_heating_hours(-5)
        # Should be 6.25 hours (interpolated)
        self.assertEqual(hours, 6.25)

    def test_typical_helsinki_spring_day(self):
        """Test typical Helsinki spring temperature."""
        # Typical spring day: 10C
        hours = self.curve.calculate_heating_hours(10)
        # Slope 0->16: -3/16 = -0.1875, so 5 - (10 * 0.1875) = 3.125 -> 3.0
        self.assertEqual(hours, 3.0)

    def test_typical_helsinki_autumn_day(self):
        """Test typical Helsinki autumn temperature."""
        # Typical autumn day: 5C
        hours = self.curve.calculate_heating_hours(5)
        # 5 - (5 * 0.1875) = 5 - 0.9375 = 4.0625 -> rounds to 4.0
        self.assertEqual(hours, 4.0)

    def test_negative_heating_hours_handled(self):
        """Test that negative calculated hours are handled correctly."""
        # Create a curve that might produce negative values at high temps
        curve = HeatingCurve({0: 8.0, 10: 2.0, 20: 0.0})

        # At very high temperature (extrapolating)
        hours = curve.calculate_heating_hours(30)

        # Should never be negative (minimum is 0)
        self.assertGreaterEqual(hours, 0.0)


if __name__ == "__main__":
    unittest.main()
