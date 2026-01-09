#!/usr/bin/env python
"""Heating curve calculations for determining required heating hours based on temperature."""

from typing import Optional

from src.common.config import get_config
from src.common.logger import setup_logger

logger = setup_logger(__name__)


class HeatingCurve:
    """
    Calculate required heating hours per day based on outdoor temperature.

    The heating curve defines the relationship between outdoor temperature
    and the number of hours per day that heating should run. Uses linear
    interpolation between defined curve points.
    """

    # Default heating curve points (temperature: hours_per_day)
    DEFAULT_CURVE = {
        -20: 12.0,  # Very cold: heat 12 hours/day
        0: 8.0,  # Freezing: heat 8 hours/day
        16: 4.0,  # Mild: heat 4 hours/day
    }

    # Minimum heating hours to bother with
    MIN_HEATING_HOURS = 0.25

    def __init__(self, curve_points: Optional[dict[float, float]] = None):
        """
        Initialize heating curve.

        Args:
            curve_points: Dict mapping temperatures (C) to heating hours/day.
                         If None, loads from config or uses DEFAULT_CURVE.
        """
        if curve_points is None:
            # Try to load from config
            config = get_config()
            curve_points = config.get("heating_curve")

            if curve_points is None:
                logger.info("No heating curve in config, using default")
                curve_points = {float(k): v for k, v in self.DEFAULT_CURVE.items()}
            else:
                logger.info(f"Loaded heating curve from config with {len(curve_points)} points")
        else:
            logger.info("Using explicitly provided heating curve")

        self.curve_points = curve_points

        # Sort curve points by temperature
        self.temperatures = sorted(self.curve_points.keys())
        self.heating_hours = [self.curve_points[t] for t in self.temperatures]

        if len(self.temperatures) < 2:
            raise ValueError("Heating curve must have at least 2 points")

        logger.debug(f"Initialized heating curve with {len(self.temperatures)} points")

    def calculate_heating_hours(self, temperature: float) -> float:
        """
        Calculate required heating hours for a given temperature.

        Uses linear interpolation between curve points. Extrapolates
        linearly beyond the defined temperature range.

        Args:
            temperature: Outdoor temperature in Celsius

        Returns:
            Required heating hours per day (rounded to 15-min intervals)
        """
        temps = self.temperatures
        hours = self.heating_hours

        # Find the correct segment for interpolation
        if temperature <= temps[0]:
            # Extrapolate below lowest temperature (use first segment slope)
            slope = (hours[1] - hours[0]) / (temps[1] - temps[0])
            val = hours[0] + (temperature - temps[0]) * slope

        elif temperature >= temps[-1]:
            # Extrapolate above highest temperature (use last segment slope)
            slope = (hours[-1] - hours[-2]) / (temps[-1] - temps[-2])
            val = hours[-1] + (temperature - temps[-1]) * slope

        else:
            # Interpolate between two curve points
            for i in range(len(temps) - 1):
                if temps[i] <= temperature <= temps[i + 1]:
                    # Linear interpolation
                    slope = (hours[i + 1] - hours[i]) / (temps[i + 1] - temps[i])
                    val = hours[i] + (temperature - temps[i]) * slope
                    break
            else:
                # Should never reach here given the if/elif logic above
                val = hours[-1]

        # Apply minimum threshold
        if val < self.MIN_HEATING_HOURS:
            val = 0.0

        # Round to 15-minute intervals (0.25 hour increments)
        val = self.round_to_quarter_hour(val)

        logger.debug(f"Temperature {temperature:.1f}C -> {val:.2f} heating hours/day")

        return val

    @staticmethod
    def round_to_quarter_hour(hours: float) -> float:
        """
        Round hours to nearest 15-minute interval (0.25 hour).

        Args:
            hours: Heating hours (can be fractional)

        Returns:
            Hours rounded to nearest 0.25
        """
        return round(hours * 4.0) / 4.0

    def get_curve_points(self) -> dict[float, float]:
        """
        Get the current heating curve points.

        Returns:
            Dict mapping temperatures to heating hours
        """
        return self.curve_points.copy()

    def set_curve_points(self, curve_points: dict[float, float]) -> None:
        """
        Update the heating curve points.

        Args:
            curve_points: New curve points (temperature: hours)
        """
        if len(curve_points) < 2:
            raise ValueError("Heating curve must have at least 2 points")

        self.curve_points = curve_points.copy()
        self.temperatures = sorted(self.curve_points.keys())
        self.heating_hours = [self.curve_points[t] for t in self.temperatures]

        logger.info(f"Updated heating curve with {len(self.temperatures)} points")
