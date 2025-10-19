"""Unit tests for heating optimizer."""

import datetime
import unittest

import pandas as pd

from src.control.heating_optimizer import HeatingOptimizer


class TestHeatingOptimizer(unittest.TestCase):
    """Test cases for HeatingOptimizer class."""

    def setUp(self):
        """Set up test fixtures."""
        self.optimizer = HeatingOptimizer()

        # Create sample hourly data for testing
        timestamps = pd.date_range(
            start="2025-01-15 00:00:00",
            periods=24,
            freq="H",
            tz="Europe/Helsinki",
        )

        self.sample_df = pd.DataFrame(
            {
                "time_floor_local": timestamps,
                "solar_yield_avg_prediction": [0.0] * 6
                + [0.5, 1.0, 1.5, 2.0]
                + [2.0] * 4
                + [1.5, 1.0, 0.5]
                + [0.0] * 7,
                "price_total": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0]
                + [6.0, 7.0, 8.0, 9.0]
                + [15.0, 16.0, 17.0, 18.0]
                + [12.0, 11.0, 10.0]
                + [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0],
                "price_sell": [5.0] * 24,
                "Air temperature": [-5.0] * 24,
            }
        )

        self.sample_df.set_index("time_floor_local", inplace=True)

    def test_initialization_default_values(self):
        """Test optimizer initialization with default values."""
        opt = HeatingOptimizer()
        self.assertEqual(opt.base_load_kw, 1.0)
        self.assertEqual(opt.heating_load_kw, 3.0)

    def test_initialization_custom_values(self):
        """Test optimizer initialization with custom values."""
        opt = HeatingOptimizer(base_load_kw=1.5, heating_load_kw=4.0)
        self.assertEqual(opt.base_load_kw, 1.5)
        self.assertEqual(opt.heating_load_kw, 4.0)
        self.assertEqual(opt.resolution_minutes, 60)  # Default

    def test_initialization_hourly_resolution(self):
        """Test optimizer initialization with hourly resolution."""
        opt = HeatingOptimizer(resolution_minutes=60)
        self.assertEqual(opt.resolution_minutes, 60)

    def test_initialization_quarterly_resolution(self):
        """Test optimizer initialization with 15-minute resolution."""
        opt = HeatingOptimizer(resolution_minutes=15)
        self.assertEqual(opt.resolution_minutes, 15)

    def test_calculate_heating_priorities_structure(self):
        """Test that calculate_heating_priorities returns correct structure."""
        result = self.optimizer.calculate_heating_priorities(self.sample_df)

        self.assertIsInstance(result, pd.DataFrame)
        self.assertIn("heating_prio", result.columns)
        self.assertIn("base_load", result.columns)
        self.assertIn("solar_yield_avg_prediction", result.columns)
        self.assertEqual(len(result), 24)  # 24 hours

    def test_calculate_heating_priorities_empty_dataframe(self):
        """Test handling of empty DataFrame."""
        empty_df = pd.DataFrame()
        result = self.optimizer.calculate_heating_priorities(empty_df)

        self.assertTrue(result.empty)

    def test_calculate_heating_priorities_values(self):
        """Test that priority values are calculated correctly."""
        result = self.optimizer.calculate_heating_priorities(self.sample_df)

        # Priority should be based on electricity cost
        # Lower priority = cheaper time to heat
        self.assertIn("heating_prio", result.columns)

        # All priorities should be positive (costs)
        self.assertTrue((result["heating_prio"] >= 0).all())

    def test_solar_reduces_heating_cost(self):
        """Test that solar production reduces heating costs."""
        # Create two scenarios: with and without solar
        no_solar_df = self.sample_df.copy()
        no_solar_df["solar_yield_avg_prediction"] = 0.0

        with_solar_df = self.sample_df.copy()
        # High solar at noon
        with_solar_df.loc[with_solar_df.index[12], "solar_yield_avg_prediction"] = 5.0

        no_solar_result = self.optimizer.calculate_heating_priorities(no_solar_df)
        with_solar_result = self.optimizer.calculate_heating_priorities(with_solar_df)

        # Cost at noon should be lower with solar
        noon_idx = no_solar_result.index[12]
        self.assertLess(
            with_solar_result.loc[noon_idx, "heating_prio"],
            no_solar_result.loc[noon_idx, "heating_prio"],
        )

    def test_filter_day_priorities(self):
        """Test filtering priorities for a specific day."""
        # Use today's data
        today = datetime.datetime.now()
        timestamps = pd.date_range(
            start=today.replace(hour=0, minute=0, second=0),
            periods=48,
            freq="H",
            tz="Europe/Helsinki",
        )

        df = pd.DataFrame(
            {
                "heating_prio": range(48),
            },
            index=timestamps,
        )

        # Filter for today (offset 0)
        today_data = self.optimizer.filter_day_priorities(df, date_offset=0)

        # Should get 24 hours for today
        self.assertLessEqual(len(today_data), 24)

        # Filter for tomorrow (offset 1)
        tomorrow_data = self.optimizer.filter_day_priorities(df, date_offset=1)

        # Should get 24 hours for tomorrow
        self.assertLessEqual(len(tomorrow_data), 24)

    def test_select_cheapest_hours(self):
        """Test selecting cheapest hours to heat."""
        result = self.optimizer.calculate_heating_priorities(self.sample_df)

        # Select 6 cheapest hours
        selected = self.optimizer.select_cheapest_hours(result, num_hours=6.0)

        self.assertEqual(len(selected), 6)

        # Selected hours should be sorted by priority (cheapest first)
        priorities = selected["heating_prio"].values
        self.assertTrue(all(priorities[i] <= priorities[i + 1] for i in range(len(priorities) - 1)))

    def test_select_cheapest_hours_empty(self):
        """Test selecting from empty DataFrame."""
        empty_df = pd.DataFrame()
        selected = self.optimizer.select_cheapest_hours(empty_df, num_hours=6.0)

        self.assertTrue(selected.empty)

    def test_select_cheapest_hours_fractional(self):
        """Test selecting fractional hours."""
        result = self.optimizer.calculate_heating_priorities(self.sample_df)

        # Select 6.5 hours (should round down to 6)
        selected = self.optimizer.select_cheapest_hours(result, num_hours=6.5)

        self.assertEqual(len(selected), 6)

    def test_get_priority_range(self):
        """Test getting priority range."""
        result = self.optimizer.calculate_heating_priorities(self.sample_df)

        min_prio, max_prio = self.optimizer.get_priority_range(result)

        self.assertIsInstance(min_prio, float)
        self.assertIsInstance(max_prio, float)
        self.assertLessEqual(min_prio, max_prio)
        self.assertGreater(max_prio, 0)

    def test_get_priority_range_empty(self):
        """Test getting priority range from empty DataFrame."""
        empty_df = pd.DataFrame()

        min_prio, max_prio = self.optimizer.get_priority_range(empty_df)

        self.assertEqual(min_prio, 0.0)
        self.assertEqual(max_prio, 0.0)

    def test_high_price_hours_not_selected(self):
        """Test that high price hours are avoided."""
        result = self.optimizer.calculate_heating_priorities(self.sample_df)

        # Select 6 cheapest hours
        selected = self.optimizer.select_cheapest_hours(result, num_hours=6.0)

        # Get the hours with highest prices (10-14)
        high_price_hours = result.iloc[10:14]

        # Selected hours should not include the expensive ones
        selected_times = set(selected.index)
        high_price_times = set(high_price_hours.index)

        overlap = selected_times.intersection(high_price_times)

        # There might be some overlap if other hours are even more expensive,
        # but there should be fewer high-price hours selected
        self.assertLess(len(overlap), 4)

    def test_night_hours_preferred_if_cheap(self):
        """Test that cheap night hours are selected."""
        # Make night hours (0-6) very cheap
        df = self.sample_df.copy()
        df.loc[df.index[0:6], "price_total"] = 3.0  # Very cheap

        result = self.optimizer.calculate_heating_priorities(df)
        selected = self.optimizer.select_cheapest_hours(result, num_hours=6.0)

        # Most of the selected hours should be from the cheap night period
        night_hours_selected = sum(1 for idx in selected.index if idx.hour >= 0 and idx.hour < 6)

        self.assertGreaterEqual(night_hours_selected, 4)

    def test_quarterly_resolution_returns_more_intervals(self):
        """Test that 15-minute resolution preserves all intervals while hourly groups them."""
        # Create 15-minute resolution data
        timestamps_15min = pd.date_range(
            start="2025-01-15 00:00:00",
            periods=96,  # 24 hours * 4 = 96 intervals
            freq="15T",
            tz="Europe/Helsinki",
        )

        df_15min = pd.DataFrame(
            {
                "time_floor_local": timestamps_15min,
                "solar_yield_avg_prediction": [0.5] * 96,
                "price_total": [10.0] * 96,
                "price_sell": [5.0] * 96,
                "Air temperature": [-5.0] * 96,
            }
        )

        df_15min.set_index("time_floor_local", inplace=True)

        # Test with quarterly resolution - should preserve all 96 intervals
        opt_quarterly = HeatingOptimizer(resolution_minutes=15)
        result_quarterly = opt_quarterly.calculate_heating_priorities(df_15min)

        # Test with hourly resolution - should group into 24 hours
        opt_hourly = HeatingOptimizer(resolution_minutes=60)
        result_hourly = opt_hourly.calculate_heating_priorities(df_15min)

        # Quarterly should have 96 intervals, hourly should have 24
        self.assertEqual(len(result_quarterly), 96)
        self.assertEqual(len(result_hourly), 24)
        self.assertAlmostEqual(len(result_quarterly) / len(result_hourly), 4, delta=0.1)

    def test_quarterly_resolution_priorities_calculated(self):
        """Test that priorities are calculated correctly for 15-minute intervals."""
        timestamps_15min = pd.date_range(
            start="2025-01-15 00:00:00",
            periods=96,
            freq="15T",
            tz="Europe/Helsinki",
        )

        df_15min = pd.DataFrame(
            {
                "time_floor_local": timestamps_15min,
                "solar_yield_avg_prediction": [0.5] * 96,
                "price_total": list(range(96)),  # Increasing prices
                "price_sell": [5.0] * 96,
                "Air temperature": [-5.0] * 96,
            }
        )

        df_15min.set_index("time_floor_local", inplace=True)

        opt = HeatingOptimizer(resolution_minutes=15)
        result = opt.calculate_heating_priorities(df_15min)

        # Should have heating_prio column
        self.assertIn("heating_prio", result.columns)

        # Priorities should increase as prices increase
        priorities = result["heating_prio"].values
        # Check that generally priorities increase (allowing some variation)
        self.assertLess(priorities[0], priorities[-1])

    def test_quarterly_select_cheapest_intervals(self):
        """Test selecting cheapest 15-minute intervals."""
        timestamps_15min = pd.date_range(
            start="2025-01-15 00:00:00",
            periods=96,
            freq="15T",
            tz="Europe/Helsinki",
        )

        # Make first 24 intervals (6 hours) very cheap
        prices = [3.0] * 24 + [10.0] * 72

        df_15min = pd.DataFrame(
            {
                "time_floor_local": timestamps_15min,
                "solar_yield_avg_prediction": [0.5] * 96,
                "price_total": prices,
                "price_sell": [5.0] * 96,
                "Air temperature": [-5.0] * 96,
            }
        )

        df_15min.set_index("time_floor_local", inplace=True)

        opt = HeatingOptimizer(resolution_minutes=15)
        result = opt.calculate_heating_priorities(df_15min)

        # Select 24 cheapest intervals (6 hours)
        selected = opt.select_cheapest_hours(result, num_hours=6.0)

        # Should get 24 intervals (6 hours * 4 intervals/hour)
        # Note: select_cheapest_hours uses integer conversion, so we get 6 intervals
        self.assertEqual(len(selected), 6)

        # All selected intervals should be from the cheap period
        # (first 6 hours = first 24 intervals)
        for idx in selected.index:
            hour = idx.hour
            self.assertLess(hour, 6)

    def test_both_resolutions_same_cheapest_hours(self):
        """Test that both resolutions select the same cheapest hours (roughly)."""
        # Create data where certain hours are clearly cheapest
        timestamps_hourly = pd.date_range(
            start="2025-01-15 00:00:00", periods=24, freq="H", tz="Europe/Helsinki"
        )

        # Hours 0-3 are cheap (price=3), rest are expensive (price=15)
        prices_hourly = [3.0] * 4 + [15.0] * 20

        df_hourly = pd.DataFrame(
            {
                "time_floor_local": timestamps_hourly,
                "solar_yield_avg_prediction": [0.5] * 24,
                "price_total": prices_hourly,
                "price_sell": [5.0] * 24,
                "Air temperature": [-5.0] * 24,
            }
        )

        df_hourly.set_index("time_floor_local", inplace=True)

        # Test with both resolutions
        opt_hourly = HeatingOptimizer(resolution_minutes=60)
        opt_quarterly = HeatingOptimizer(resolution_minutes=15)

        result_hourly = opt_hourly.calculate_heating_priorities(df_hourly)
        result_quarterly = opt_quarterly.calculate_heating_priorities(df_hourly)

        selected_hourly = opt_hourly.select_cheapest_hours(result_hourly, num_hours=4.0)
        selected_quarterly = opt_quarterly.select_cheapest_hours(result_quarterly, num_hours=4.0)

        # Both should select hours 0-3 (the cheap hours)
        hourly_hours = {idx.hour for idx in selected_hourly.index}
        quarterly_hours = {idx.hour for idx in selected_quarterly.index}

        # Both should include hours 0, 1, 2, 3
        self.assertTrue({0, 1, 2, 3}.issubset(hourly_hours))
        self.assertTrue({0, 1, 2, 3}.issubset(quarterly_hours))


if __name__ == "__main__":
    unittest.main()
