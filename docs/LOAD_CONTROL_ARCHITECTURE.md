# Load Control Architecture Design

**Date:** 2025-10-19
**Status:** Design Phase
**Version:** 1.0

---

## Executive Summary

Design for a multi-load home energy management system that optimizes electricity costs while maintaining comfort. The system manages geothermal heating, EV charging, garage heating, and AC control with peak power management and multi-day forecasting.

---

## System Constraints

### Electrical Infrastructure
- **Main Fuse:** 50A × 3 phases = ~34.5kW total (230V × 50A × 3 × √3)
- **Peak Power Charge:** Monthly fee based on 3rd highest hourly consumption
  - Must track current month's peak
  - Decision: exceed peak only if cost savings > monthly peak fee increase

### Loads and Capabilities

| Load | Power | Control | Priority | Notes |
|------|-------|---------|----------|-------|
| AC Unit | 400W | Emergency shutoff | 1 (Highest) | Always on except EVUOFF |
| Water Heating | 3kW | Monitor temp sensors | 2 (Critical) | 20min bursts, not during EVUOFF |
| House Heating (Pump) | 3kW | ON/OFF | 3 (High) | COP 4:1, provides 12kW heating |
| EV Charger (VW ID.7) | 5kW/11kW | 8A/16A + Start/Stop | 4 (Dynamic) | Moves to #2 when departure scheduled |
| Garage Heater | 0/1/2kW | 3-level | 5 (Low) | Two Shelly relays (186, 185) |
| Direct Heating | 3/6/9kW | Selectable | 6 (Emergency) | Emergency only, poor efficiency |

### Control Capabilities
- **AC Unit:** Emergency shutoff relay (Shelly), normally ON, off during EVUOFF
- **Water Heating:** Monitored via temp sensors (Kayttovesi ylh, Kayttovesi alh), automatic 20min cycles
- **Geothermal Pump:** ON/OFF via I2C (mlp_control.sh), minimum cycle time ~15 min
- **EV Charger (VW ID.7):** tillsteinbach/CarConnectivity API, 8A/16A switching, read SoC and target
- **Garage Heater:** Two independent Shelly relays (192.168.1.186, 192.168.1.185), 1kW each
- **Direct Heating:** Selectable 3kW/6kW/9kW levels (emergency use only)

---

## Architecture Overview

### Three-Tier Planning System

```
┌─────────────────────────────────────────────────────────────┐
│ TIER 1: Multi-Day Strategic Planner (Daily at 16:05)       │
│ - Forecast spot prices 2-7 days ahead                       │
│ - Plan heat storage strategy                                │
│ - Optimize peak power management                            │
│ - Generate baseline load allocation                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ TIER 2: Daily Tactical Optimizer (Daily at 16:05)          │
│ - Use confirmed weather & price forecasts                   │
│ - Calculate heating hours from temperature                  │
│ - Allocate EV charging windows                              │
│ - Optimize for cost vs. comfort setting                     │
│ - Generate 24h schedule with 15-min resolution              │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ TIER 3: Real-Time Executor (Every 15 minutes)              │
│ - Monitor actual temperature & humidity                     │
│ - Check EV charge level & target                            │
│ - Adjust schedule based on real-time conditions            │
│ - Enforce power budget limits                               │
│ - Execute load commands                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Schedule Format Design

### Universal Schedule Format (JSON)

```json
{
  "version": "2.0",
  "generated_at": "2025-10-19T16:05:00+02:00",
  "valid_from": "2025-10-20T00:00:00+02:00",
  "valid_until": "2025-10-21T00:00:00+02:00",
  "optimization_mode": "balanced",
  "comfort_weight": 0.7,
  "cost_weight": 0.3,
  "peak_power_limit": 25000,
  "current_monthly_peak": 22500,
  "metadata": {
    "avg_temperature": -5.2,
    "total_heating_hours": 10.5,
    "ev_charging_windows": 2,
    "estimated_cost": 12.45
  },
  "schedule": [
    {
      "start_time": "2025-10-20T00:00:00+02:00",
      "end_time": "2025-10-20T00:15:00+02:00",
      "epoch_start": 1729378800,
      "epoch_end": 1729379700,
      "loads": {
        "geothermal_pump": {
          "command": "OFF",
          "reason": "high_price",
          "power_w": 0
        },
        "direct_heating": {
          "command": "OFF",
          "level": 0,
          "power_w": 0
        },
        "ev_charger": {
          "command": "CHARGE",
          "amperage": 16,
          "power_w": 11000,
          "target_soc": 80,
          "priority": "normal"
        },
        "garage_heater": {
          "command": "OFF",
          "level": 0,
          "power_w": 0
        },
        "ac_unit": {
          "command": "ALLOW",
          "emergency_shutoff": false
        }
      },
      "total_power_w": 11000,
      "spot_price": 0.085,
      "solar_prediction_w": 0,
      "net_grid_power_w": 11000,
      "cost_estimate": 0.935,
      "executed": false,
      "executed_at": null,
      "adjustments": []
    }
  ]
}
```

### Key Design Features

1. **Time Resolution:** 15-minute intervals (96 slots per day)
2. **Multi-Load Support:** Each interval specifies all loads
3. **Power Tracking:** Track total power per interval
4. **Execution Tracking:** Mark intervals as executed
5. **Adjustment History:** Log real-time adjustments
6. **Versioned Format:** Allow future schema evolution

---

## Load Control Logic

### 1. Geothermal Heating Control

**Heating Curve (configurable):**
```yaml
heating:
  curve:
    -20: 12.0  # hours per day at -20°C
    0: 8.0     # hours per day at 0°C
    16: 4.0    # hours per day at 16°C

  # Real-time adjustments
  temperature_adjustment:
    per_degree_error: 0.25  # add 15min per degree below target

  humidity_adjustment:
    high_threshold: 65      # % RH
    adjustment_factor: 0.1  # add 10% heating time if humid

  constraints:
    min_cycle_time_minutes: 15
    max_continuous_hours: 6
    evuoff_max_hours: 4
    evuoff_threshold_price: 0.20  # EUR/kWh
```

**Temperature-Based Real-Time Adjustment:**
```python
def adjust_heating_schedule(schedule, current_temp, target_temp, humidity):
    """Adjust heating based on real-time sensor data."""
    temp_error = target_temp - current_temp

    # Add extra heating if too cold
    if temp_error > 1.0:
        extra_hours = temp_error * 0.25  # 15min per degree
        schedule.add_heating_hours(extra_hours, priority="immediate")

    # Reduce heating if too warm
    elif temp_error < -1.0:
        schedule.defer_heating_hours(abs(temp_error) * 0.25)

    # Humidity adjustment
    if humidity > 65:
        schedule.add_heating_hours(schedule.total_hours * 0.1)
```

### 2. EV Charging Control

**Charging Parameters:**
```python
class EVChargingGoal:
    current_soc: float          # Current state of charge (%)
    target_soc: float           # Target charge level (80% or 100%)
    target_time: datetime       # When to reach target
    battery_capacity_kwh: float # Total battery capacity

    def required_energy_kwh(self) -> float:
        return (self.target_soc - self.current_soc) * self.battery_capacity_kwh / 100

    def required_hours(self, charging_power_kw: float) -> float:
        return self.required_energy_kwh() / charging_power_kw / 0.85  # 85% efficiency
```

**Charging Strategy:**
```python
def plan_ev_charging(goal: EVChargingGoal, price_forecast, solar_forecast):
    """Allocate EV charging to cheapest hours with solar preference."""

    available_hours = hours_until(goal.target_time)
    required_hours = goal.required_hours(charging_power_kw=10)

    # Prioritize hours with solar production
    solar_hours = find_hours_with_solar(solar_forecast, available_hours)

    # Fill remaining with cheapest grid hours
    cheap_hours = find_cheapest_hours(price_forecast,
                                      count=required_hours - len(solar_hours))

    charging_schedule = solar_hours + cheap_hours

    # Decide amperage based on price
    for hour in charging_schedule:
        if hour.price < 0.05:  # Very cheap
            hour.amperage = 16  # 11kW
        else:
            hour.amperage = 8   # 5kW (extend charging time)
```

### 3. Peak Power Management

**Peak Power Decision Logic:**
```python
class PeakPowerManager:
    def __init__(self, current_monthly_peak_w: float, peak_fee_per_kw: float):
        self.current_peak = current_monthly_peak_w
        self.peak_fee = peak_fee_per_kw  # EUR/kW/month

    def should_exceed_peak(self, proposed_load_w: float,
                          spot_price: float, duration_hours: float) -> bool:
        """Decide if exceeding peak is economically justified."""

        new_peak_w = max(self.current_peak, proposed_load_w)
        peak_increase_kw = (new_peak_w - self.current_peak) / 1000

        # Cost of increasing peak for rest of month
        days_left_in_month = days_until_month_end()
        peak_cost_increase = peak_increase_kw * self.peak_fee * (days_left_in_month / 30)

        # Savings from running load now vs. later
        alternative_price = self.get_next_available_price()
        energy_kwh = proposed_load_w * duration_hours / 1000
        savings = (alternative_price - spot_price) * energy_kwh

        return savings > peak_cost_increase
```

### 4. Multi-Day Strategic Planning

**Heat Storage Strategy:**
```python
def plan_multi_day_heating(price_forecast_7days, temp_forecast):
    """Pre-heat when prices are low, coast when high."""

    # Identify price patterns
    high_price_days = find_days_above_threshold(price_forecast, threshold=0.15)
    low_price_days = find_days_below_threshold(price_forecast, threshold=0.08)

    for low_day in low_price_days:
        if any(high_day within 48h of low_day):
            # Add extra heating to build thermal mass
            extra_hours = calculate_thermal_buffer(temp_forecast)
            low_day.schedule.add_heating_hours(extra_hours)

            # Reduce heating on high price day
            high_day.schedule.reduce_heating_hours(extra_hours * 0.7)
```

---

## Optimization Modes

### User-Configurable Balance

```yaml
optimization:
  mode: "balanced"  # cost_optimized | balanced | comfort_first

  cost_optimized:
    comfort_weight: 0.3
    cost_weight: 0.7
    allow_temperature_drop: 2.0  # °C

  balanced:
    comfort_weight: 0.5
    cost_weight: 0.5
    allow_temperature_drop: 1.0  # °C

  comfort_first:
    comfort_weight: 0.8
    cost_weight: 0.2
    allow_temperature_drop: 0.5  # °C
```

**Cost Function:**
```python
def optimize_schedule(price_forecast, temp_forecast, mode_config):
    """Multi-objective optimization."""

    cost_score = calculate_total_cost(schedule)
    comfort_score = calculate_comfort_penalty(schedule, target_temp)

    total_score = (
        mode_config.cost_weight * cost_score +
        mode_config.comfort_weight * comfort_score
    )

    return schedule_with_minimum(total_score)
```

---

## Data Flow

### Daily Planning Cycle

```
16:05 Daily Trigger
    ↓
1. Fetch Data
   - Weather forecast (FMI)
   - Spot prices (confirmed + predicted)
   - Solar production forecast
   - Wind power forecast (for price prediction)
   - Current EV charge level
   - Temperature sensors
   - Current monthly peak power
    ↓
2. Multi-Day Planning
   - Predict prices 2-7 days ahead from wind forecast
   - Identify optimal pre-heating opportunities
   - Plan peak power management
    ↓
3. Daily Optimization
   - Calculate heating hours from temperature
   - Allocate EV charging windows
   - Distribute garage heating
   - Apply optimization mode (cost vs comfort)
    ↓
4. Generate Schedule
   - Create 96 x 15-minute intervals
   - Assign all loads per interval
   - Validate power budget
   - Save JSON schedule
    ↓
5. Store Schedule
   - Save to: schedules/YYYY-MM-DD/load_schedule.json
   - Backup previous schedule
   - Log generation metadata
```

### Real-Time Execution Cycle (Every 15 minutes)

```
00:00, 00:15, 00:30, 00:45, ... Trigger
    ↓
1. Load Schedule
   - Read today's schedule
   - Handle day transitions
    ↓
2. Read Sensors
   - Indoor temperature
   - Indoor humidity
   - EV charge level
   - Current power consumption
    ↓
3. Adjust Schedule (if needed)
   - Temperature deviation > threshold
   - EV target time approaching
   - Power budget exceeded
    ↓
4. Execute Commands
   - Geothermal pump ON/OFF
   - EV charger start/stop/adjust
   - Garage heater adjust
   - AC unit allow/shutoff
    ↓
5. Record Execution
   - Mark interval as executed
   - Log actual power consumption
   - Update peak power tracking
   - Save adjusted schedule
```

---

## Module Design

### Proposed Module Structure

```
src/control/
├── __init__.py
├── heating_curve.py          # Temp → hours calculation
├── optimizer.py              # Multi-objective optimization
├── schedule_generator.py     # Create daily schedules
├── schedule_executor.py      # Execute schedules
├── peak_power_manager.py     # Track & manage peak power
├── ev_charging_planner.py    # EV charge planning
├── load_controller.py        # Individual load control
├── strategic_planner.py      # Multi-day planning
└── models.py                 # Data classes (Schedule, Load, etc.)

src/control/loads/
├── __init__.py
├── geothermal_pump.py        # Heat pump control
├── ev_charger.py             # EV charging control
├── garage_heater.py          # Garage heating control
├── ac_unit.py                # AC emergency shutoff
└── base.py                   # Base load controller class

src/forecasting/
├── __init__.py
├── price_predictor.py        # Predict prices from wind forecast
├── thermal_model.py          # House thermal behavior model
└── solar_predictor.py        # Solar production (if not using existing)
```

---

## Implementation Phases

### Phase 4.1: Core Architecture (Week 1)
- [ ] Define data models (Schedule, Load, LoadCommand)
- [ ] Implement schedule format v2.0
- [ ] Create base load controller class
- [ ] Extract heating curve logic
- [ ] Unit tests for core models

### Phase 4.2: Single Load (Current Functionality) (Week 1-2)
- [ ] Implement geothermal pump controller
- [ ] Migrate existing heating optimization
- [ ] Generate schedules in new format
- [ ] Execute schedules with new executor
- [ ] Maintain backward compatibility
- [ ] Integration tests with dry-run mode

### Phase 4.3: Multi-Load Support (Week 2-3)
- [ ] Implement EV charging planner
- [ ] Implement garage heater controller
- [ ] Power budget enforcement
- [ ] Peak power management
- [ ] Multi-load schedule generation
- [ ] Unit tests for each load type

### Phase 4.4: Advanced Features (Week 3-4)
- [ ] Multi-day strategic planning
- [ ] Price prediction from wind forecast
- [ ] Real-time schedule adjustments
- [ ] Optimization mode selection
- [ ] Thermal model for pre-heating
- [ ] Comprehensive integration tests

### Phase 4.5: Testing & Validation (Week 4)
- [ ] Simulation with historical data
- [ ] Cost optimization validation
- [ ] Comfort level validation
- [ ] Peak power limit validation
- [ ] Edge case testing
- [ ] Documentation

---

## Configuration Schema

### config.yaml Extension

```yaml
# Existing config...

# Load Control Configuration
load_control:
  # Optimization
  optimization_mode: "balanced"  # cost_optimized | balanced | comfort_first

  # Peak Power Management
  peak_power:
    limit_w: 25000
    monthly_fee_per_kw: 5.50  # EUR/kW/month
    track_current_peak: true

  # Geothermal Heating
  geothermal:
    power_w: 3000
    cop: 4.0
    min_cycle_minutes: 15
    max_continuous_hours: 6
    controller_type: "i2c"  # i2c | shelly | modbus

  # Direct Heating (Emergency)
  direct_heating:
    enabled: false
    levels: [3000, 6000, 9000]
    auto_enable_temp: -15  # Enable if temp drops below

  # EV Charging
  ev_charger:
    enabled: true
    max_power_w: 11000
    min_power_w: 5000
    amperage_levels: [8, 16]
    battery_capacity_kwh: 64
    default_target_soc: 80
    efficiency: 0.85
    controller_type: "api"  # api | modbus | ocpp
    api_url: "http://192.168.1.xx/api"

  # Garage Heating
  garage:
    enabled: true
    relay_1_power_w: 1000
    relay_2_power_w: 1000
    relay_1_url: "http://192.168.1.xx"
    relay_2_url: "http://192.168.1.yy"

  # AC Unit
  ac_unit:
    emergency_shutoff_relay_url: "http://192.168.1.zz"
    auto_shutoff_in_evuoff: true

  # EVUOFF Mode
  evuoff:
    enabled: true
    threshold_price: 0.20  # EUR/kWh
    max_continuous_hours: 4
    disable_loads: ["geothermal", "ac_unit"]

  # Real-time Adjustments
  realtime:
    temperature_sensor_id: "28-000000000000"
    humidity_sensor_id: "shelly-ht-000000"
    target_temperature: 21.0
    temperature_tolerance: 1.0
    adjustment_interval_minutes: 15
```

---

## Success Criteria

- [ ] **Backward Compatible:** Existing heating control continues to work
- [ ] **Multi-Load:** Can control 4+ loads simultaneously
- [ ] **Power Budget:** Never exceeds 34.5kW total
- [ ] **Peak Management:** Optimizes monthly peak power charge
- [ ] **EV Integration:** Charges to target by deadline at minimum cost
- [ ] **Real-Time Adaptive:** Adjusts for temperature/humidity deviations
- [ ] **Multi-Day Planning:** Pre-heats before high price periods
- [ ] **Cost vs Comfort:** User-selectable optimization balance
- [ ] **Testable:** 50+ unit tests, integration tests with simulated loads
- [ ] **Documented:** Clear architecture and API documentation

---

## Open Questions / Future Considerations

1. **Price Prediction Model:**
   - How accurate should wind → price correlation be?
   - Use ML model or simple correlation?
   - Fallback if wind forecast unavailable?

2. **EV API Integration:**
   - Which EV charger model/API?
   - Tesla Wall Connector? Easee? Go-e?
   - Fallback to manual control?

3. **Thermal Model:**
   - How complex should house thermal model be?
   - Simple time constant or multi-zone?
   - Machine learning from historical data?

4. **Load Priorities:**
   - Should priorities be dynamic based on conditions?
   - Emergency overrides (freeze protection)?
   - User manual overrides via Grafana?

5. **Safety Interlocks:**
   - Over-temperature protection
   - Under-voltage detection
   - Communication failure handling
   - Manual override mechanism

---

**Next Steps:**
1. Review this design with user
2. Refine based on feedback
3. Start implementation with Phase 4.1
4. Iterate incrementally with testing at each phase
