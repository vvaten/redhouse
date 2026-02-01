# Refactoring Plan: Code Quality Violations

## Overview

Address 74 non-blocking code quality violations identified in the code review:
- 2 files exceeding 500 lines
- 4 functions with high cyclomatic complexity
- Improve maintainability and testability

**Status:** Planning Phase
**Priority:** Medium (Technical Debt)
**Estimated Effort:** 24-32 hours total

---

## Executive Summary

### Issues Summary

| Issue | Current | Target | Impact |
|-------|---------|--------|--------|
| Long files | 2 files >500 lines | Split into focused modules | Maintainability |
| High complexity | 4 functions >20 complexity | Reduce to <15 | Testability |
| Code duplication | Analytics 15min/1hour | Extract shared logic | DRY principle |

### Critical Path

1. **Phase 1** (High Priority): Refactor aggregation functions (16-20 hours)
   - Complexity 45, 39, 33 functions are testing bottlenecks
   - Similar structure enables shared refactoring approach

2. **Phase 2** (Medium Priority): Refactor pump_controller.py (14-21 hours)
   - Already has detailed plan in [REFACTORING_PUMP_CONTROLLER.md](REFACTORING_PUMP_CONTROLLER.md)
   - Hardware separation improves testability

3. **Phase 3** (Lower Priority): Split program_generator.py (4-6 hours)
   - Well-structured but exceeds line limit
   - Extract EVU optimization logic

4. **Phase 4** (Lower Priority): Simplify get_temperature (2-3 hours)
   - Extract sensor discovery and validation

**Total Estimated Time:** 36-50 hours

---

## Phase 1: Refactor Aggregation Functions (HIGH PRIORITY)

### Problem

Three aggregation functions have excessive complexity and are difficult to test:

| Function | File | Complexity | Lines | Issue |
|----------|------|------------|-------|-------|
| aggregate_5min_window | emeters_5min.py | 45 | 226 | Critical |
| aggregate_1hour_window | analytics_1hour.py | 39 | 189 | Critical |
| aggregate_15min_window | analytics_15min.py | 33 | 184 | High |

**Root Causes:**
- Monolithic functions doing data fetching, validation, calculations, and writing
- High branching complexity from error handling
- Code duplication between 15min and 1hour versions
- Difficult to test individual calculation steps

### Solution: Extract Calculation Pipeline

Refactor each aggregation function into a pipeline of smaller, testable functions:

```
aggregate_window()
  |-- fetch_data()           # Data retrieval
  |-- validate_data()        # Input validation
  |-- calculate_metrics()    # Core calculations
      |-- calculate_energy_metrics()
      |-- calculate_cost_metrics()
      |-- calculate_efficiency_metrics()
  |-- format_output()        # Result formatting
  |-- write_results()        # InfluxDB write
```

### Detailed Refactoring Strategy

#### 1. Create Shared Aggregation Base Class

**New file:** `src/aggregation/aggregation_base.py`

```python
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd

class AggregationPipeline(ABC):
    """
    Base class for data aggregation pipelines.

    Provides common structure for all aggregation intervals (5min, 15min, 1hour).
    """

    def __init__(self, influx_client, config):
        self.influx = influx_client
        self.config = config

    def aggregate_window(
        self,
        window_start: datetime,
        window_end: datetime,
        write_to_influx: bool = True
    ) -> Optional[dict]:
        """
        Execute aggregation pipeline for a time window.

        Args:
            window_start: Window start time
            window_end: Window end time
            write_to_influx: Whether to write results to InfluxDB

        Returns:
            Aggregated metrics dict or None if failed
        """
        try:
            # Step 1: Fetch data
            raw_data = self.fetch_data(window_start, window_end)
            if not raw_data:
                logger.warning(f"No data for window {window_start} - {window_end}")
                return None

            # Step 2: Validate data
            if not self.validate_data(raw_data):
                logger.error(f"Data validation failed for window {window_start} - {window_end}")
                return None

            # Step 3: Calculate metrics (implemented by subclass)
            metrics = self.calculate_metrics(raw_data, window_start, window_end)
            if not metrics:
                logger.error(f"Metric calculation failed for window {window_start} - {window_end}")
                return None

            # Step 4: Write results
            if write_to_influx:
                success = self.write_results(metrics, window_end)
                if not success:
                    logger.error(f"Failed to write results for window {window_start} - {window_end}")
                    return None

            return metrics

        except Exception as e:
            logger.error(f"Aggregation failed for window {window_start} - {window_end}: {e}")
            return None

    @abstractmethod
    def fetch_data(self, window_start: datetime, window_end: datetime) -> dict:
        """Fetch raw data for the window."""
        pass

    @abstractmethod
    def validate_data(self, raw_data: dict) -> bool:
        """Validate fetched data."""
        pass

    @abstractmethod
    def calculate_metrics(self, raw_data: dict, window_start: datetime, window_end: datetime) -> Optional[dict]:
        """Calculate aggregated metrics."""
        pass

    @abstractmethod
    def write_results(self, metrics: dict, timestamp: datetime) -> bool:
        """Write results to InfluxDB."""
        pass
```

#### 2. Extract Shared Calculation Functions

**New file:** `src/aggregation/metric_calculators.py`

```python
"""Shared metric calculation functions for aggregation pipelines."""

def calculate_energy_averages(df: pd.DataFrame, fields: list[str]) -> dict:
    """
    Calculate average power for energy fields.

    Args:
        df: DataFrame with energy data
        fields: List of field names to average

    Returns:
        Dict of field -> average value
    """
    averages = {}
    for field in fields:
        if field in df.columns:
            averages[f"{field}_avg"] = df[field].mean()
    return averages


def calculate_energy_sums(df: pd.DataFrame, fields: list[str], interval_seconds: int) -> dict:
    """
    Calculate energy sums (Wh) from power averages (W).

    Args:
        df: DataFrame with energy data
        fields: List of field names to sum
        interval_seconds: Time interval in seconds

    Returns:
        Dict of field -> sum value in Wh
    """
    sums = {}
    for field in fields:
        if field in df.columns:
            # Convert W to Wh: average_power * (seconds / 3600)
            sums[f"{field}_sum"] = df[field].mean() * (interval_seconds / 3600.0)
    return sums


def calculate_electricity_cost(
    energy_kwh: float,
    price_c_kwh: float
) -> float:
    """
    Calculate electricity cost in EUR.

    Args:
        energy_kwh: Energy consumed in kWh
        price_c_kwh: Price in cents per kWh

    Returns:
        Cost in EUR
    """
    if energy_kwh is None or price_c_kwh is None:
        return 0.0
    return (energy_kwh * price_c_kwh) / 100.0


def calculate_self_consumption_ratio(
    solar_yield_wh: float,
    export_wh: float
) -> float:
    """
    Calculate self-consumption ratio (% of solar used directly).

    Args:
        solar_yield_wh: Total solar production in Wh
        export_wh: Energy exported to grid in Wh

    Returns:
        Self-consumption ratio as percentage (0-100)
    """
    if solar_yield_wh is None or solar_yield_wh == 0:
        return 0.0

    if export_wh is None:
        export_wh = 0.0

    self_consumed = solar_yield_wh - export_wh
    return (self_consumed / solar_yield_wh) * 100.0


def safe_mean(df: pd.DataFrame, field: str, default: float = 0.0) -> float:
    """
    Safely calculate mean of a field.

    Args:
        df: DataFrame
        field: Field name
        default: Default value if field missing or no data

    Returns:
        Mean value or default
    """
    if field not in df.columns or df.empty:
        return default
    return float(df[field].mean())


def safe_last(df: pd.DataFrame, field: str, default: float = 0.0) -> float:
    """
    Safely get last value of a field.

    Args:
        df: DataFrame
        field: Field name
        default: Default value if field missing or no data

    Returns:
        Last value or default
    """
    if field not in df.columns or df.empty:
        return default
    return float(df[field].iloc[-1])
```

#### 3. Refactor emeters_5min.py

**Changes to:** `src/aggregation/emeters_5min.py`

Split `aggregate_5min_window` (complexity 45 -> target <15) into pipeline:

```python
from src.aggregation.aggregation_base import AggregationPipeline
from src.aggregation.metric_calculators import (
    calculate_energy_averages,
    calculate_energy_sums,
    safe_mean,
    safe_last
)

class Emeters5MinAggregator(AggregationPipeline):
    """5-minute energy meter aggregation pipeline."""

    INTERVAL_SECONDS = 300  # 5 minutes

    def fetch_data(self, window_start: datetime, window_end: datetime) -> dict:
        """Fetch CheckWatt and Shelly EM3 data for window."""
        # Extract from current aggregate_5min_window lines 50-100
        checkwatt_df = self._fetch_checkwatt_data(window_start, window_end)
        shelly_df = self._fetch_shelly_data(window_start, window_end)

        return {
            "checkwatt": checkwatt_df,
            "shelly": shelly_df,
            "window_start": window_start,
            "window_end": window_end
        }

    def _fetch_checkwatt_data(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Fetch CheckWatt data from InfluxDB."""
        # Extract from lines 50-80
        query = f'''
            from(bucket: "{self.config.influxdb_bucket_checkwatt}")
              |> range(start: {start.isoformat()}, stop: {end.isoformat()})
              |> filter(fn: (r) => r["_measurement"] == "checkwatt")
        '''
        return self.influx.query_api.query_data_frame(query)

    def _fetch_shelly_data(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Fetch Shelly EM3 data from InfluxDB."""
        # Extract from lines 80-100
        query = f'''
            from(bucket: "{self.config.influxdb_bucket_emeters}")
              |> range(start: {start.isoformat()}, stop: {end.isoformat()})
              |> filter(fn: (r) => r["_measurement"] == "shelly_em3")
        '''
        return self.influx.query_api.query_data_frame(query)

    def validate_data(self, raw_data: dict) -> bool:
        """Validate that we have sufficient data."""
        checkwatt_df = raw_data.get("checkwatt")
        shelly_df = raw_data.get("shelly")

        # Need at least one data point from each source
        has_checkwatt = checkwatt_df is not None and not checkwatt_df.empty
        has_shelly = shelly_df is not None and not shelly_df.empty

        if not has_checkwatt:
            logger.warning("No CheckWatt data for window")
        if not has_shelly:
            logger.warning("No Shelly EM3 data for window")

        # Return True even if only one source available (graceful degradation)
        return has_checkwatt or has_shelly

    def calculate_metrics(self, raw_data: dict, window_start: datetime, window_end: datetime) -> Optional[dict]:
        """Calculate 5-minute aggregated metrics."""
        metrics = {}

        # Extract dataframes
        checkwatt_df = raw_data.get("checkwatt")
        shelly_df = raw_data.get("shelly")

        # Calculate energy metrics from CheckWatt (if available)
        if checkwatt_df is not None and not checkwatt_df.empty:
            energy_metrics = self._calculate_checkwatt_metrics(checkwatt_df)
            metrics.update(energy_metrics)

        # Calculate grid metrics from Shelly (if available)
        if shelly_df is not None and not shelly_df.empty:
            grid_metrics = self._calculate_shelly_metrics(shelly_df)
            metrics.update(grid_metrics)

        # Calculate derived metrics (consumption, costs)
        if metrics:
            derived = self._calculate_derived_metrics(metrics)
            metrics.update(derived)

        return metrics if metrics else None

    def _calculate_checkwatt_metrics(self, df: pd.DataFrame) -> dict:
        """Calculate metrics from CheckWatt data (complexity reduced to ~8)."""
        # Extract from lines 120-160
        metrics = {}

        # Energy averages (power in W)
        energy_fields = ["SolarYield", "BatteryCharge", "BatteryDischarge", "EnergyImport", "EnergyExport"]
        metrics.update(calculate_energy_averages(df, energy_fields))

        # Energy sums (Wh over 5 minutes)
        metrics.update(calculate_energy_sums(df, energy_fields, self.INTERVAL_SECONDS))

        # Battery state
        metrics["Battery_SoC"] = safe_last(df, "Battery_SoC")

        return metrics

    def _calculate_shelly_metrics(self, df: pd.DataFrame) -> dict:
        """Calculate metrics from Shelly EM3 data (complexity reduced to ~6)."""
        # Extract from lines 160-190
        metrics = {}

        # Grid power and energy
        metrics["emeter_avg"] = safe_mean(df, "net_total_power")
        metrics["emeter_diff"] = safe_mean(df, "net_total_power") * (self.INTERVAL_SECONDS / 3600.0)

        # Grid quality metrics
        metrics["grid_voltage_avg"] = safe_mean(df, "voltage_avg")
        metrics["grid_current_avg"] = safe_mean(df, "current_avg")
        metrics["grid_power_factor_avg"] = safe_mean(df, "power_factor")

        return metrics

    def _calculate_derived_metrics(self, metrics: dict) -> dict:
        """Calculate derived metrics like consumption (complexity ~5)."""
        # Extract from lines 190-220
        derived = {}

        # Total consumption = grid + solar + battery_discharge - battery_charge
        cw_emeter = metrics.get("cw_emeter_avg", 0.0)
        solar = metrics.get("solar_yield_avg", 0.0)
        bat_discharge = metrics.get("battery_discharge_avg", 0.0)
        bat_charge = metrics.get("battery_charge_avg", 0.0)

        derived["consumption_avg"] = cw_emeter + solar + bat_discharge - bat_charge
        derived["consumption_diff"] = derived["consumption_avg"] * (self.INTERVAL_SECONDS / 3600.0)

        return derived

    def write_results(self, metrics: dict, timestamp: datetime) -> bool:
        """Write aggregated metrics to InfluxDB."""
        # Extract from lines 220-260
        try:
            point = Point("energy").time(timestamp)

            for field_name, value in metrics.items():
                point = point.field(field_name, float(value))

            bucket = self.config.influxdb_bucket_emeters_5min
            self.influx.write_api.write(bucket=bucket, record=point)

            logger.debug(f"Wrote {len(metrics)} fields to {bucket}")
            return True

        except Exception as e:
            logger.error(f"Failed to write metrics: {e}")
            return False
```

**Complexity Reduction:**
- `aggregate_5min_window`: 45 -> ~10 (main pipeline logic)
- `_calculate_checkwatt_metrics`: ~8
- `_calculate_shelly_metrics`: ~6
- `_calculate_derived_metrics`: ~5
- **Total max complexity: 10** (vs original 45)

#### 4. Refactor analytics_15min.py and analytics_1hour.py

**Strategy:** Both files have nearly identical structure - extract common logic

**New file:** `src/aggregation/analytics_aggregation.py`

```python
from src.aggregation.aggregation_base import AggregationPipeline
from src.aggregation.metric_calculators import (
    calculate_energy_averages,
    calculate_electricity_cost,
    calculate_self_consumption_ratio,
    safe_mean,
    safe_last
)

class AnalyticsAggregator(AggregationPipeline):
    """
    Base analytics aggregator for 15min and 1hour intervals.

    Fetches energy data, prices, weather, temperatures and calculates
    comprehensive analytics with costs and efficiency metrics.
    """

    def __init__(self, influx_client, config, interval_minutes: int):
        super().__init__(influx_client, config)
        self.interval_minutes = interval_minutes
        self.interval_seconds = interval_minutes * 60

    def fetch_data(self, window_start: datetime, window_end: datetime) -> dict:
        """Fetch all required data sources."""
        return {
            "energy": self._fetch_energy_data(window_start, window_end),
            "prices": self._fetch_price_data(window_start, window_end),
            "weather": self._fetch_weather_data(window_start, window_end),
            "temperatures": self._fetch_temperature_data(window_start, window_end)
        }

    def _fetch_energy_data(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Fetch from emeters_5min bucket."""
        # Aggregate multiple 5-min windows
        query = self._build_energy_query(start, end)
        return self.influx.query_api.query_data_frame(query)

    # ... (extract other fetch methods, each ~10-15 lines)

    def calculate_metrics(self, raw_data: dict, window_start: datetime, window_end: datetime) -> Optional[dict]:
        """Calculate comprehensive analytics metrics."""
        metrics = {}

        # Step 1: Energy metrics
        energy_metrics = self._calculate_energy_metrics(raw_data["energy"])
        metrics.update(energy_metrics)

        # Step 2: Cost metrics
        cost_metrics = self._calculate_cost_metrics(metrics, raw_data["prices"])
        metrics.update(cost_metrics)

        # Step 3: Weather metrics
        weather_metrics = self._extract_weather_metrics(raw_data["weather"])
        metrics.update(weather_metrics)

        # Step 4: Temperature metrics
        temp_metrics = self._extract_temperature_metrics(raw_data["temperatures"])
        metrics.update(temp_metrics)

        # Step 5: Efficiency metrics
        efficiency_metrics = self._calculate_efficiency_metrics(metrics)
        metrics.update(efficiency_metrics)

        return metrics

    def _calculate_energy_metrics(self, df: pd.DataFrame) -> dict:
        """Calculate energy averages and sums (complexity ~8)."""
        # Extract shared logic from both 15min and 1hour versions
        pass

    def _calculate_cost_metrics(self, energy_metrics: dict, prices_df: pd.DataFrame) -> dict:
        """Calculate electricity costs and revenue (complexity ~7)."""
        cost_metrics = {}

        # Extract price
        price_total = safe_mean(prices_df, "price_total", 0.0)
        price_sell = safe_mean(prices_df, "price_sell", 0.0)

        # Get energy values
        emeter_sum = energy_metrics.get("emeter_sum", 0.0)
        export_sum = energy_metrics.get("energy_export_sum", 0.0)

        # Calculate costs
        cost_metrics["electricity_cost"] = calculate_electricity_cost(
            emeter_sum / 1000.0,  # Wh to kWh
            price_total
        )

        cost_metrics["solar_export_revenue"] = calculate_electricity_cost(
            export_sum / 1000.0,
            price_sell
        )

        cost_metrics["net_cost"] = cost_metrics["electricity_cost"] - cost_metrics["solar_export_revenue"]

        return cost_metrics

    def _calculate_efficiency_metrics(self, metrics: dict) -> dict:
        """Calculate efficiency ratios (complexity ~5)."""
        efficiency = {}

        solar_sum = metrics.get("solar_yield_sum", 0.0)
        export_sum = metrics.get("energy_export_sum", 0.0)

        efficiency["self_consumption_ratio"] = calculate_self_consumption_ratio(solar_sum, export_sum)

        return efficiency

    # ... (other helper methods)
```

**Then create specific implementations:**

`src/aggregation/analytics_15min.py` becomes:
```python
from src.aggregation.analytics_aggregation import AnalyticsAggregator

class Analytics15MinAggregator(AnalyticsAggregator):
    """15-minute analytics aggregator."""

    def __init__(self, influx_client, config):
        super().__init__(influx_client, config, interval_minutes=15)

    def write_results(self, metrics: dict, timestamp: datetime) -> bool:
        """Write to analytics_15min bucket."""
        bucket = self.config.influxdb_bucket_analytics_15min
        # ... write logic
```

`src/aggregation/analytics_1hour.py` becomes:
```python
from src.aggregation.analytics_aggregation import AnalyticsAggregator

class Analytics1HourAggregator(AnalyticsAggregator):
    """1-hour analytics aggregator."""

    def __init__(self, influx_client, config):
        super().__init__(influx_client, config, interval_minutes=60)

    def write_results(self, metrics: dict, timestamp: datetime) -> bool:
        """Write to analytics_1hour bucket."""
        bucket = self.config.influxdb_bucket_analytics_1hour
        # ... write logic
```

**Complexity Reduction:**
- `aggregate_15min_window`: 33 -> ~8 (main pipeline)
- `aggregate_1hour_window`: 39 -> ~8 (main pipeline)
- Shared calculation functions: <10 each
- **Eliminated code duplication** between 15min and 1hour

### Testing Benefits

**Before refactoring:**
```python
def test_aggregate_5min_window():
    # Must mock entire 226-line function
    # Can't test individual calculations
    # High complexity = many edge cases
```

**After refactoring:**
```python
def test_calculate_checkwatt_metrics():
    # Test just CheckWatt calculation (8 complexity)
    df = create_test_checkwatt_data()
    metrics = aggregator._calculate_checkwatt_metrics(df)
    assert metrics["solar_yield_avg"] == 1500.0

def test_calculate_electricity_cost():
    # Test pure calculation function
    cost = calculate_electricity_cost(energy_kwh=10.5, price_c_kwh=15.2)
    assert cost == 1.596

def test_self_consumption_ratio():
    # Test efficiency calculation
    ratio = calculate_self_consumption_ratio(solar_yield_wh=5000, export_wh=1000)
    assert ratio == 80.0
```

### Migration Path

**Phase 1.1: Create Base Classes (4 hours)**
- [ ] Create `aggregation_base.py` with `AggregationPipeline`
- [ ] Create `metric_calculators.py` with shared calculation functions
- [ ] Add unit tests for calculation functions
- [ ] No changes to existing aggregation scripts yet

**Phase 1.2: Refactor emeters_5min.py (6 hours)**
- [ ] Create `Emeters5MinAggregator` class using new base
- [ ] Extract fetch methods
- [ ] Extract calculation methods
- [ ] Update systemd service to use new class
- [ ] Verify aggregation still works correctly
- [ ] Add comprehensive unit tests

**Phase 1.3: Refactor Analytics (6-8 hours)**
- [ ] Create `AnalyticsAggregator` base class
- [ ] Refactor `analytics_15min.py` to use base
- [ ] Refactor `analytics_1hour.py` to use base
- [ ] Update systemd services
- [ ] Add unit tests for shared logic
- [ ] Verify both aggregations work

**Phase 1.4: Cleanup (2 hours)**
- [ ] Remove old commented code
- [ ] Update documentation
- [ ] Run full test suite
- [ ] Measure complexity improvements

**Deliverable:** Aggregation functions with complexity <15, fully tested, DRY code

---

## Phase 2: Refactor pump_controller.py (DOCUMENTED)

### Status

A comprehensive refactoring plan already exists in [REFACTORING_PUMP_CONTROLLER.md](REFACTORING_PUMP_CONTROLLER.md).

**Summary:**
- Separate hardware access from business logic
- Use dependency injection for hardware interfaces
- Improve testability from 55% to 90%+ coverage
- Estimated effort: 14-21 hours

**Action:** Follow existing plan in REFACTORING_PUMP_CONTROLLER.md

**Note:** pump_controller.py is currently 524 lines (target <500), but the hardware separation refactoring should reduce it to ~400 lines by extracting hardware implementations to separate files.

---

## Phase 3: Split program_generator.py (LOWER PRIORITY)

### Problem

`program_generator.py` is 675 lines (target: <500 lines)

**However:** File is well-structured with:
- Clear class organization
- Single responsibility per method
- Good documentation
- Complexity values are acceptable (<15 for most methods)

**Root cause:** Single-class module with comprehensive functionality

### Solution: Extract EVU Optimization Logic

The file has natural split points:

**Current structure:**
```
program_generator.py (675 lines)
  - HeatingProgramGenerator class
    - generate_daily_program() - orchestration
    - _generate_evu_off_periods() - EVU logic
    - _optimize_evu_off_groups() - EVU optimization
    - _generate_load_schedules() - schedule building
    - _generate_geothermal_pump_schedule() - pump schedule
    - _calculate_planning_results() - summary
    - save_program_json() - persistence
    - save_program_influxdb() - persistence
```

**Proposed split:**

**File 1:** `src/control/program_generator.py` (350 lines)
```python
class HeatingProgramGenerator:
    """Main program generation orchestration."""

    def __init__(self, config):
        self.evu_optimizer = EvuOptimizer(config)
        self.schedule_builder = ScheduleBuilder(config)

    def generate_daily_program(self, date_offset, simulation_mode, base_date):
        # Orchestration logic
        evu_periods = self.evu_optimizer.generate_evu_off_periods(...)
        schedules = self.schedule_builder.generate_load_schedules(...)
```

**File 2:** `src/control/evu_optimizer.py` (150 lines)
```python
class EvuOptimizer:
    """
    Optimize EVU-OFF periods to block expensive direct heating.

    Responsible for:
    - Identifying expensive hours above threshold
    - Grouping consecutive hours with max continuous length limit
    - Merging adjacent groups when possible
    """

    EVU_OFF_THRESHOLD_PRICE = 15.0
    EVU_OFF_MAX_CONTINUOUS_HOURS = 4

    def generate_evu_off_periods(self, df, priorities_df, hours_to_heat, date_offset):
        # Extract from program_generator.py lines 197-258

    def _optimize_evu_off_groups(self, expensive_hours_df, max_continuous_hours):
        # Extract from program_generator.py lines 260-335
```

**File 3:** `src/control/schedule_builder.py` (200 lines)
```python
class ScheduleBuilder:
    """
    Build heating schedules for all loads.

    Responsible for:
    - Generating geothermal pump schedules
    - Placeholder for garage heater
    - Placeholder for EV charger
    - Adding ALE (auto mode) transitions
    """

    def generate_load_schedules(self, selected_hours, evu_off_periods, day_priorities, program_date):
        # Extract from program_generator.py lines 337-377

    def _generate_geothermal_pump_schedule(self, selected_hours, evu_off_periods, day_priorities, program_date):
        # Extract from program_generator.py lines 379-506
```

### Benefits

- **Maintainability:** Each file has single responsibility
- **Testability:** Can test EVU optimization independently
- **Extensibility:** Easy to add new load types to ScheduleBuilder
- **Line count:** All files <400 lines

### Migration Path

**Phase 3.1: Extract EvuOptimizer (2 hours)**
- [ ] Create `src/control/evu_optimizer.py`
- [ ] Move EVU logic from program_generator.py
- [ ] Update program_generator.py to use EvuOptimizer
- [ ] Add unit tests for EVU optimization

**Phase 3.2: Extract ScheduleBuilder (2 hours)**
- [ ] Create `src/control/schedule_builder.py`
- [ ] Move schedule generation logic
- [ ] Update program_generator.py to use ScheduleBuilder
- [ ] Add unit tests for schedule building

**Phase 3.3: Cleanup (1 hour)**
- [ ] Update imports
- [ ] Update documentation
- [ ] Run tests
- [ ] Verify program generation still works

**Deliverable:** program_generator.py reduced to ~350 lines

---

## Phase 4: Simplify get_temperature() (LOWER PRIORITY)

### Problem

`get_temperature()` in `src/data_collection/temperature.py` has complexity 21 (target: <15)

**Function does:**
- Discover 1-wire temperature sensors (DS18B20)
- Read sensor values
- Validate ranges (-55C to 125C)
- Detect suspicious values (85C, 0C error codes)
- Handle sensor name mapping
- Write to InfluxDB
- Error handling and logging

**Current structure:** 104 lines, single function

### Solution: Extract Sensor Operations

Split into focused functions:

```python
def get_temperature(sensors_to_read: Optional[list] = None) -> bool:
    """
    Read and record temperatures from DS18B20 sensors.

    Main orchestration function with reduced complexity (~8).
    """
    try:
        # Step 1: Discover sensors
        available_sensors = discover_temperature_sensors()
        if not available_sensors:
            logger.warning("No temperature sensors found")
            return False

        # Step 2: Select sensors to read
        sensors = select_sensors_to_read(available_sensors, sensors_to_read)

        # Step 3: Read and validate sensors
        readings = read_and_validate_sensors(sensors)
        if not readings:
            logger.warning("No valid temperature readings")
            return False

        # Step 4: Write to InfluxDB
        success = write_temperatures_to_influx(readings)
        return success

    except Exception as e:
        logger.error(f"Temperature reading failed: {e}")
        return False


def discover_temperature_sensors() -> list[str]:
    """
    Discover available DS18B20 temperature sensors.

    Returns:
        List of sensor IDs (e.g., ['28-XXXX', '28-YYYY'])
    """
    # Extract from lines 52-65
    result = subprocess.run(
        ["ls", "/sys/bus/w1/devices"],
        capture_output=True,
        text=True,
        stderr=subprocess.DEVNULL
    ).stdout

    sensors = [s for s in result.strip().split() if s.startswith("28-")]
    logger.info(f"Discovered {len(sensors)} temperature sensors")
    return sensors


def select_sensors_to_read(
    available_sensors: list[str],
    requested_sensors: Optional[list[str]]
) -> list[str]:
    """
    Select which sensors to read.

    Args:
        available_sensors: List of discovered sensor IDs
        requested_sensors: Optional list of specific sensors to read

    Returns:
        List of sensor IDs to read
    """
    if requested_sensors:
        # Filter to requested sensors that are available
        sensors = [s for s in requested_sensors if s in available_sensors]
        if not sensors:
            logger.warning(f"None of the requested sensors are available")
        return sensors
    return available_sensors


def read_and_validate_sensors(sensors: list[str]) -> list[dict]:
    """
    Read temperature values from sensors and validate.

    Args:
        sensors: List of sensor IDs to read

    Returns:
        List of valid readings: [{"sensor_id": str, "temperature": float, "name": str}, ...]
    """
    readings = []

    for sensor_id in sensors:
        temperature = read_sensor_value(sensor_id)
        if temperature is None:
            continue

        if not validate_temperature_value(temperature, sensor_id):
            continue

        sensor_name = get_sensor_name(sensor_id)
        readings.append({
            "sensor_id": sensor_id,
            "temperature": temperature,
            "name": sensor_name
        })

    return readings


def read_sensor_value(sensor_id: str) -> Optional[float]:
    """
    Read raw temperature value from sensor file.

    Args:
        sensor_id: Sensor ID (e.g., '28-XXXX')

    Returns:
        Temperature in Celsius or None if read failed
    """
    # Extract from lines 70-85
    try:
        with open(f"/sys/bus/w1/devices/{sensor_id}/w1_slave") as f:
            lines = f.readlines()

        if len(lines) < 2:
            logger.warning(f"Invalid sensor data from {sensor_id}")
            return None

        # Parse temperature value
        if "t=" not in lines[1]:
            logger.warning(f"No temperature data in {sensor_id}")
            return None

        temp_str = lines[1].split("t=")[1].strip()
        temperature = int(temp_str) / 1000.0
        return temperature

    except Exception as e:
        logger.error(f"Failed to read sensor {sensor_id}: {e}")
        return None


def validate_temperature_value(temperature: float, sensor_id: str) -> bool:
    """
    Validate temperature reading is within acceptable range.

    DS18B20 valid range: -55C to +125C
    Suspicious values: 85C (sensor error), 0C (disconnected)

    Args:
        temperature: Temperature value to validate
        sensor_id: Sensor ID for logging

    Returns:
        True if value is valid
    """
    # Extract from lines 90-105
    # Range check
    if not (-55 <= temperature <= 125):
        logger.warning(f"Sensor {sensor_id} reading {temperature:.2f}C out of valid range")
        return False

    # Suspicious value check (common error codes)
    if temperature == 85.0 or temperature == 0.0:
        logger.warning(f"Sensor {sensor_id} suspicious reading: {temperature:.2f}C (likely error)")
        return False

    return True


def get_sensor_name(sensor_id: str) -> str:
    """
    Get human-readable name for sensor.

    Args:
        sensor_id: Sensor ID

    Returns:
        Sensor name from config or sensor ID
    """
    config = get_config()
    sensor_mapping = config.get("sensor_mapping", {})
    return sensor_mapping.get(sensor_id, sensor_id)


def write_temperatures_to_influx(readings: list[dict]) -> bool:
    """
    Write temperature readings to InfluxDB.

    Args:
        readings: List of validated readings

    Returns:
        True if write successful
    """
    # Extract from lines 110-130
    influx = InfluxClient(get_config())

    try:
        for reading in readings:
            influx.write_point(
                measurement="temperature",
                fields={"value": reading["temperature"]},
                tags={"sensor": reading["name"]},
            )

        logger.info(f"Wrote {len(readings)} temperature readings to InfluxDB")
        return True

    except Exception as e:
        logger.error(f"Failed to write temperatures: {e}")
        return False
```

### Complexity Reduction

- `get_temperature()`: 21 -> ~8 (orchestration only)
- `discover_temperature_sensors()`: ~3
- `select_sensors_to_read()`: ~4
- `read_and_validate_sensors()`: ~6 (iteration logic)
- `read_sensor_value()`: ~5
- `validate_temperature_value()`: ~4
- `get_sensor_name()`: ~2
- `write_temperatures_to_influx()`: ~4
- **Max complexity: 8** (vs original 21)

### Testing Benefits

**Before:**
```python
def test_get_temperature():
    # Must mock file system, sensor discovery, validation, InfluxDB
    # Can't test validation independently
    # 104 lines of logic to cover
```

**After:**
```python
def test_discover_temperature_sensors():
    # Test just sensor discovery
    with mock.patch('subprocess.run') as mock_run:
        mock_run.return_value.stdout = "28-000001 28-000002\n"
        sensors = discover_temperature_sensors()
        assert len(sensors) == 2

def test_validate_temperature_value():
    # Test validation rules independently
    assert validate_temperature_value(20.5, "test") is True
    assert validate_temperature_value(85.0, "test") is False  # Error code
    assert validate_temperature_value(-60.0, "test") is False  # Out of range
    assert validate_temperature_value(130.0, "test") is False  # Out of range

def test_read_sensor_value():
    # Test raw reading with mocked file
    pass
```

### Migration Path

**Phase 4.1: Extract Functions (2 hours)**
- [ ] Create new functions: `discover_temperature_sensors()`, `select_sensors_to_read()`, etc.
- [ ] Keep original `get_temperature()` for now (deprecate later)
- [ ] Add unit tests for each new function

**Phase 4.2: Refactor Main Function (1 hour)**
- [ ] Update `get_temperature()` to use new functions
- [ ] Verify temperature collection still works
- [ ] Run integration tests

**Phase 4.3: Cleanup (30 minutes)**
- [ ] Remove old code
- [ ] Update documentation
- [ ] Run coverage report

**Deliverable:** `get_temperature()` with complexity ~8, fully tested

---

## Implementation Order

### Recommended Sequence

1. **Start with Phase 1** (Aggregation refactoring): Highest complexity, biggest testing benefit
2. **Then Phase 2** (pump_controller.py): Already planned, critical for test coverage
3. **Then Phase 4** (get_temperature): Quick win, improves data collection reliability
4. **Finally Phase 3** (program_generator.py): Lower priority, file is well-structured

### Alternative Sequence (if time-constrained)

If limited time, prioritize:

1. **Phase 1.2**: Refactor emeters_5min.py (complexity 45 is critical)
2. **Phase 4**: Simplify get_temperature() (quick, high-value)
3. **Defer**: Phases 1.3, 2, and 3 to future sprints

---

## Success Criteria

### Code Quality Metrics

| Metric | Before | After | Status |
|--------|--------|-------|--------|
| Files >500 lines | 2 | 0 | Target |
| Functions >20 complexity | 4 | 0 | Target |
| Max function complexity | 45 | <15 | Target |
| Code duplication | High (15min/1hour) | Low | Target |

### Testing Improvements

| Area | Before | After | Status |
|------|--------|-------|--------|
| Aggregation tests | Difficult, monolithic | Easy, unit-testable | Target |
| Pump controller coverage | 55% | 90%+ | Target |
| Temperature collection tests | Limited | Comprehensive | Target |

### Maintainability

- All functions <100 lines
- All complexity <15
- No code duplication in analytics
- Hardware separated from business logic
- Each file has single responsibility

---

## Effort Estimation

| Phase | Description | Estimated Hours |
|-------|-------------|----------------|
| 1.1 | Create aggregation base classes | 4 |
| 1.2 | Refactor emeters_5min.py | 6 |
| 1.3 | Refactor analytics (15min + 1hour) | 6-8 |
| 1.4 | Cleanup and testing | 2 |
| **Phase 1 Total** | **Aggregation refactoring** | **18-20** |
| | | |
| 2 | pump_controller.py refactoring | 14-21 |
| | *(See REFACTORING_PUMP_CONTROLLER.md)* | |
| | | |
| 3.1 | Extract EvuOptimizer | 2 |
| 3.2 | Extract ScheduleBuilder | 2 |
| 3.3 | Cleanup | 1 |
| **Phase 3 Total** | **program_generator.py split** | **5** |
| | | |
| 4.1 | Extract temperature functions | 2 |
| 4.2 | Refactor main function | 1 |
| 4.3 | Cleanup | 0.5 |
| **Phase 4 Total** | **get_temperature simplification** | **3.5** |
| | | |
| **Grand Total** | **All phases** | **40-49 hours** |

---

## Risk Assessment

### Low Risk

- **Aggregation refactoring**: No external dependencies, easy to test incrementally
- **get_temperature simplification**: Single-file change, easy to validate

### Medium Risk

- **pump_controller.py**: Hardware interface changes require careful testing
- **program_generator.py split**: Must ensure program generation logic unchanged

### Mitigation Strategies

1. **Incremental changes**: Refactor one function at a time, validate with tests
2. **Backward compatibility**: Keep old code during transition, deprecate later
3. **Test coverage**: Add unit tests before refactoring (capture current behavior)
4. **Staging environment**: Test all changes in staging before production
5. **Rollback plan**: Keep git commits small, easy to revert

---

## Related Documents

- [CODE_REVIEW_REPORT.md](CODE_REVIEW_REPORT.md) - Detailed code review findings
- [REFACTORING_PUMP_CONTROLLER.md](REFACTORING_PUMP_CONTROLLER.md) - Pump controller hardware separation plan
- [AGGREGATION_PIPELINE_DESIGN.md](AGGREGATION_PIPELINE_DESIGN.md) - Aggregation architecture
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Development practices

---

**Document Status:** Planning Phase - Ready for Implementation
**Last Updated:** 2026-02-01
**Author:** Code Quality Improvement Initiative
