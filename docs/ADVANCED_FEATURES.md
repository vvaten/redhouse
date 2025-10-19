# Advanced Load Control Features

**Addendum to:** [LOAD_CONTROL_ARCHITECTURE.md](LOAD_CONTROL_ARCHITECTURE.md)
**Date:** 2025-10-19
**Status:** Design Phase

---

## Overview

This document describes advanced features for the load control system that go beyond basic scheduling and optimization. These features leverage multiple sensor inputs, real-time adjustments, and predictive models to maximize comfort while minimizing costs.

---

## 1. Per-Room Temperature & Humidity Monitoring

### Motivation
Instead of a single house-wide temperature setpoint, monitor individual rooms to optimize comfort and respond to specific room conditions (e.g., bathroom humidity after shower).

### Sensor Network

**Temperature Sensors (1-wire DS18B20):**
- Living room
- Kitchen
- Bedrooms (multiple)
- Bathrooms (multiple)
- Chimney (Savupiippu) - for wood oven monitoring
- Water heating (Kayttovesi ylh, Kayttovesi alh)
- Outdoor temperature

**Humidity Sensors (Shelly H&T):**
- Bathrooms (one per bathroom)
- Other rooms as available

### Control Logic

```python
class RoomMonitor:
    """Monitor individual room conditions."""

    def __init__(self, room_config):
        self.rooms = {}
        for room_name, config in room_config.items():
            self.rooms[room_name] = {
                'temp_sensor_id': config['temp_sensor'],
                'humidity_sensor_id': config.get('humidity_sensor'),
                'target_temp': config['target_temp'],
                'priority': config['priority'],
                'type': config['type']  # living, bedroom, bathroom, etc.
            }

    def get_heating_adjustment(self) -> float:
        """Calculate heating adjustment based on all room temps."""

        # Weighted average based on room priorities
        total_temp_error = 0
        total_weight = 0

        for room_name, room in self.rooms.items():
            current_temp = read_sensor(room['temp_sensor_id'])
            target_temp = room['target_temp']
            priority = room['priority']

            temp_error = target_temp - current_temp
            weighted_error = temp_error * priority

            total_temp_error += weighted_error
            total_weight += priority

        avg_temp_error = total_temp_error / total_weight

        # Convert to heating hours adjustment
        # +1°C error = +0.25 hours extra heating per day
        return avg_temp_error * 0.25
```

### Bathroom Humidity Spike Detection

**Use Case:** Someone just showered, bathroom floor heating should continue to dry the room.

```python
class BathroomHumidityController:
    """Detect and respond to bathroom humidity spikes."""

    def __init__(self, humidity_threshold=65, spike_threshold=15):
        self.humidity_threshold = humidity_threshold  # Absolute high
        self.spike_threshold = spike_threshold        # Sudden increase
        self.previous_readings = {}
        self.spike_detected_at = {}

    def check_humidity_spike(self, bathroom_name, current_humidity):
        """Detect sudden humidity increase (shower event)."""

        if bathroom_name not in self.previous_readings:
            self.previous_readings[bathroom_name] = current_humidity
            return False

        previous = self.previous_readings[bathroom_name]
        spike = current_humidity - previous

        # Detect spike: sudden increase of >15% RH
        if spike > self.spike_threshold:
            self.spike_detected_at[bathroom_name] = time.time()
            logger.info(f"Humidity spike detected in {bathroom_name}: "
                       f"{previous}% -> {current_humidity}%")
            return True

        self.previous_readings[bathroom_name] = current_humidity
        return False

    def should_extend_heating(self, bathroom_name, current_humidity,
                            price_mode="balanced"):
        """Decide if heating should be extended for drying."""

        # If humidity is high, keep heating
        if current_humidity > self.humidity_threshold:
            return True

        # If spike was recent (within 2 hours), keep heating
        if bathroom_name in self.spike_detected_at:
            time_since_spike = time.time() - self.spike_detected_at[bathroom_name]
            if time_since_spike < 7200:  # 2 hours
                # But only if not too expensive
                if price_mode == "cost_optimized":
                    return get_current_spot_price() < 0.15  # Threshold
                else:
                    return True  # Comfort mode, always extend

        return False
```

**Integration with Schedule:**
```python
def adjust_schedule_for_humidity(schedule, bathroom_humidity_controller):
    """Real-time adjustment for bathroom humidity."""

    for bathroom_name, humidity in get_bathroom_humidity_sensors():
        if bathroom_humidity_controller.check_humidity_spike(bathroom_name, humidity):
            # Add extra heating for the next 2 hours
            schedule.add_heating_hours(
                hours=2.0,
                reason=f"bathroom_humidity_spike_{bathroom_name}",
                priority="immediate"
            )
```

---

## 2. Wood Oven (Leivinuuni) Thermal Integration

### Motivation
When the large wood oven is heated, it provides significant thermal mass that keeps the house warm for days. The geothermal heating should be reduced to avoid overheating and wasted electricity.

### Monitoring Strategy

**Chimney Temperature Sensor:**
- Sensor ID: `Savupiippu`
- Normal temp: ~20-30°C (ambient)
- Heating event: >100°C
- Post-heating: Slowly decays over 24-48 hours

### Detection Logic

```python
class WoodOvenMonitor:
    """Monitor wood oven usage via chimney temperature."""

    def __init__(self, chimney_sensor_id="Savupiippu"):
        self.sensor_id = chimney_sensor_id
        self.heating_threshold = 100  # °C
        self.last_heating_time = None
        self.heating_detected = False

    def update(self, current_temp):
        """Update state based on chimney temperature."""

        if current_temp > self.heating_threshold:
            if not self.heating_detected:
                logger.info(f"Wood oven heating detected! Chimney temp: {current_temp}°C")
                self.heating_detected = True
                self.last_heating_time = time.time()
        else:
            self.heating_detected = False

    def get_heating_reduction_factor(self) -> float:
        """Calculate how much to reduce geothermal heating."""

        if self.last_heating_time is None:
            return 0.0  # No reduction

        hours_since_heating = (time.time() - self.last_heating_time) / 3600

        # Decay curve for wood oven thermal contribution
        # Hour 0-6:   50% reduction (oven very hot)
        # Hour 6-24:  25% reduction (oven still warm)
        # Hour 24-48: 10% reduction (residual heat)
        # Hour 48+:   0% reduction (back to normal)

        if hours_since_heating < 6:
            return 0.50
        elif hours_since_heating < 24:
            return 0.25
        elif hours_since_heating < 48:
            return 0.10
        else:
            return 0.0
```

**Integration with Heating Curve:**
```python
def calculate_adjusted_heating_hours(outdoor_temp, wood_oven_monitor):
    """Calculate heating hours considering wood oven contribution."""

    # Base heating hours from temperature
    base_hours = heating_curve(outdoor_temp)

    # Reduction from wood oven
    reduction_factor = wood_oven_monitor.get_heating_reduction_factor()
    reduced_hours = base_hours * (1 - reduction_factor)

    logger.info(f"Base heating hours: {base_hours:.2f}h, "
               f"Wood oven reduction: {reduction_factor*100:.0f}%, "
               f"Adjusted: {reduced_hours:.2f}h")

    return reduced_hours
```

**Advanced Feature - Predictive Scheduling:**
```python
def plan_around_wood_oven_heating(schedule, wood_oven_calendar):
    """If wood oven heating is planned, pre-reduce geothermal heating."""

    # User can mark in calendar when they plan to heat wood oven
    for planned_heating in wood_oven_calendar:
        heating_date = planned_heating['date']

        # Reduce geothermal heating for 48h after planned wood heating
        for hours_after in range(0, 48):
            schedule_time = heating_date + timedelta(hours=hours_after)
            reduction = calculate_wood_oven_reduction(hours_after)
            schedule.reduce_heating_at(schedule_time, reduction_factor=reduction)
```

---

## 3. Solar Load Balancing (Real-Time)

### Motivation
When solar panels produce excess power, use it immediately for flexible loads (EV charging, garage heating) instead of selling at low/zero prices.

### Energy Meter Integration

**Shelly EM3 (3-phase energy meter):**
- URL: `http://192.168.1.5/status`
- Provides real-time power consumption per phase
- Tracks `total` and `total_returned` for net energy flow

### Load Balancing Algorithm

Based on prototype in `CarChargerSolarPVController.ipynb`:

```python
class SolarLoadBalancer:
    """Real-time load balancing to maximize solar self-consumption."""

    def __init__(self, period_length_s=3600, threshold_wh=1000):
        self.period_length = period_length_s       # 1 hour periods
        self.threshold = threshold_wh              # Min surplus to act on
        self.start_period_total_wh = None
        self.controllable_loads = {}               # Load name -> controller

    async def get_energy_meter_total(self):
        """Read current total energy from Shelly EM3."""
        url = 'http://192.168.1.5/status'
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    total = 0
                    for emeter in data["emeters"]:
                        # Net energy (consumed - returned)
                        total += emeter['total'] - emeter['total_returned']
                    return round(total, 1)
                return None

    def predict_period_end_energy(self, current_wh, last_cycle_power_w,
                                  remaining_s):
        """Predict energy balance at end of current period."""
        period_energy_wh = current_wh - self.start_period_total_wh
        predicted_end_wh = period_energy_wh + (
            last_cycle_power_w * remaining_s / 3600
        )
        return predicted_end_wh

    async def balance_load(self, load_name, load_power_w):
        """Decide whether to turn load on/off based on solar surplus."""

        current_total_wh = await self.get_energy_meter_total()
        current_time = time.time()
        period_time_s = int(current_time % self.period_length)

        # Start of new period
        if period_time_s < 5:
            self.start_period_total_wh = current_total_wh

        remaining_s = self.period_length - period_time_s

        # Current period energy balance (negative = producing surplus)
        period_energy_wh = current_total_wh - self.start_period_total_wh

        # Predict end-of-period energy
        # (simplified - should use actual last cycle power)
        predicted_end_wh = self.predict_period_end_energy(
            current_total_wh,
            last_cycle_power_w=period_energy_wh * 3600 / period_time_s,
            remaining_s=remaining_s
        )

        load_controller = self.controllable_loads[load_name]
        is_on = load_controller.is_on()

        if is_on:
            # Load is currently on
            predicted_with_load_off = predicted_end_wh - (
                load_power_w * remaining_s / 3600
            )

            if predicted_end_wh > self.threshold:
                # Will buy too much from grid, turn off
                logger.info(f"{load_name}: Turning OFF (grid consumption too high)")
                await load_controller.turn_off()

        else:
            # Load is currently off
            predicted_with_load_on = predicted_end_wh + (
                load_power_w * remaining_s / 3600
            )

            if predicted_end_wh < -self.threshold:
                # Excess solar, turn on
                logger.info(f"{load_name}: Turning ON (excess solar available)")
                await load_controller.turn_on()
```

**Prioritized Load Shedding:**
```python
class PrioritizedSolarLoadBalancer(SolarLoadBalancer):
    """Solar load balancer with priority-based load management."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_priorities = {
            'ev_charger': 1,      # Highest priority for solar
            'garage_heater': 2,
            'water_heating': 3    # Can wait for scheduled time
        }

    async def balance_all_loads(self):
        """Balance multiple loads based on solar availability."""

        total_available_solar_w = await self.estimate_available_solar()

        # Sort loads by priority
        sorted_loads = sorted(
            self.controllable_loads.items(),
            key=lambda x: self.load_priorities.get(x[0], 999)
        )

        allocated_power = 0
        for load_name, controller in sorted_loads:
            load_power = controller.get_power_w()

            if allocated_power + load_power <= total_available_solar_w:
                if not controller.is_on():
                    await controller.turn_on()
                allocated_power += load_power
            else:
                if controller.is_on():
                    await controller.turn_off()
```

**Integration with Daily Schedule:**
- Solar load balancer runs independently every 60 seconds
- Only controls loads during "solar optimization windows"
- Daily schedule defines which hours allow solar load balancing
- Outside these windows, follow scheduled plan

---

## 4. Wind Power → Electricity Price Prediction

### Motivation
Wind power forecasts extend 3 days ahead, providing early warning of price changes. High wind = low prices. Use this to plan multi-day heating strategy.

### Data Available
- **Wind Power Forecast:** 3-day forecast from `windpowergetter.py`
- **Historical Data:** Years of wind forecasts vs actual spot prices in InfluxDB
- **Correlation:** High wind typically means lower spot prices (more renewable generation)

### Prediction Model

```python
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor

class WindPricePredictionModel:
    """Predict electricity spot prices from wind power forecasts."""

    def __init__(self, model_type='linear'):
        self.model_type = model_type
        self.model = None
        self.scaler = None

    def train(self, influx_client, lookback_days=365):
        """Train model on historical wind forecast vs spot price data."""

        # Fetch historical data
        wind_data = influx_client.query_wind_forecasts(
            start=f"-{lookback_days}d"
        )
        price_data = influx_client.query_spot_prices(
            start=f"-{lookback_days}d"
        )

        # Merge on timestamp
        df = pd.merge(wind_data, price_data, on='timestamp', how='inner')

        # Features:
        # - Wind power forecast (MW)
        # - Hour of day (cyclical)
        # - Day of week (cyclical)
        # - Month (cyclical)
        # - Previous 24h average price (trend)

        df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
        df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
        df['day_sin'] = np.sin(2 * np.pi * df['dayofweek'] / 7)
        df['day_cos'] = np.cos(2 * np.pi * df['dayofweek'] / 7)
        df['price_24h_avg'] = df['price'].rolling(window=96, min_periods=1).mean()

        features = [
            'wind_power_mw',
            'hour_sin', 'hour_cos',
            'day_sin', 'day_cos',
            'price_24h_avg'
        ]

        X = df[features].values
        y = df['price'].values

        # Train model
        if self.model_type == 'linear':
            self.model = LinearRegression()
        elif self.model_type == 'random_forest':
            self.model = RandomForestRegressor(n_estimators=100, max_depth=10)

        self.model.fit(X, y)

        # Evaluate
        train_score = self.model.score(X, y)
        logger.info(f"Model trained. R² score: {train_score:.3f}")

    def predict(self, wind_forecast_df):
        """Predict spot prices from wind forecast."""

        # Prepare features
        wind_forecast_df['hour_sin'] = np.sin(
            2 * np.pi * wind_forecast_df['hour'] / 24
        )
        wind_forecast_df['hour_cos'] = np.cos(
            2 * np.pi * wind_forecast_df['hour'] / 24
        )
        # ... add other cyclical features

        features = wind_forecast_df[feature_columns].values
        predicted_prices = self.model.predict(features)

        return predicted_prices
```

**Usage in Multi-Day Planning:**
```python
def plan_with_price_prediction(wind_forecast, confirmed_prices, model):
    """Generate 7-day heating plan using predicted prices."""

    # Days 0-2: Use confirmed spot prices
    schedule_days_0_2 = optimize_with_confirmed_prices(confirmed_prices[:48])

    # Days 3-7: Use predicted prices from wind forecast
    predicted_prices_days_3_7 = model.predict(wind_forecast[48:])

    # Identify low-price windows in days 3-7
    low_price_hours = find_hours_below_percentile(
        predicted_prices_days_3_7,
        percentile=25
    )

    # If prices are predicted to spike in days 3-5...
    if any(predicted_prices_days_3_7[24:120] > 0.20):
        # Pre-heat extra in days 0-2
        logger.info("Price spike predicted days 3-5. Pre-heating now.")
        schedule_days_0_2.add_heating_hours(2.0, reason="pre_heat_before_spike")

    return combine_schedules(schedule_days_0_2, schedule_days_3_7)
```

---

## 5. Water Heating Monitoring

### Motivation
Water heating is automatic and runs in ~20 minute bursts when water temperature drops. Monitor these cycles to avoid conflicts with other loads and respect EVUOFF mode.

### Temperature Sensors
- **Kayttovesi ylh** (Domestic water upper sensor)
- **Kayttovesi alh** (Domestic water lower sensor)

### Monitoring Logic

```python
class WaterHeatingMonitor:
    """Monitor water heating cycles."""

    def __init__(self,
                 upper_sensor_id="Kayttovesi ylh",
                 lower_sensor_id="Kayttovesi alh"):
        self.upper_sensor = upper_sensor_id
        self.lower_sensor = lower_sensor_id

        # Temperature thresholds
        self.target_temp = 60  # °C
        self.heating_trigger = 50  # Start heating below this
        self.heating_duration_s = 1200  # ~20 minutes

        self.is_heating = False
        self.heating_started_at = None

    def update(self):
        """Check water temperatures and detect heating cycle."""

        upper_temp = read_sensor(self.upper_sensor)
        lower_temp = read_sensor(self.lower_sensor)

        avg_temp = (upper_temp + lower_temp) / 2

        # Detect heating start (temperature rising rapidly)
        if not self.is_heating:
            temp_rising = upper_temp > lower_temp + 5  # Upper hotter = heating
            if temp_rising or avg_temp < self.heating_trigger:
                self.is_heating = True
                self.heating_started_at = time.time()
                logger.info(f"Water heating started (avg temp: {avg_temp:.1f}°C)")

        # Detect heating end
        else:
            time_heating = time.time() - self.heating_started_at
            if avg_temp >= self.target_temp or time_heating > self.heating_duration_s:
                self.is_heating = False
                logger.info(f"Water heating completed (avg temp: {avg_temp:.1f}°C)")

    def is_water_heating_active(self) -> bool:
        """Check if water heating is currently running."""
        return self.is_heating

    def get_water_heating_power(self) -> float:
        """Return power consumption if heating active."""
        return 3000 if self.is_heating else 0
```

**Integration with Power Budget:**
```python
def calculate_available_power_budget(water_monitor, ac_on, evuoff_mode):
    """Calculate how much power is available for controllable loads."""

    total_capacity = 34500  # 50A × 3-phase = 34.5kW

    # Reserved loads
    base_load = 1000  # Always consuming something
    ac_power = 400 if ac_on else 0
    water_power = water_monitor.get_water_heating_power()

    reserved_power = base_load + ac_power + water_power
    available = total_capacity - reserved_power

    logger.debug(f"Power budget: {available}W available "
                f"(water: {water_power}W, AC: {ac_power}W)")

    return available
```

---

## 6. Integration - Putting It All Together

### Real-Time Execution Loop (Every 15 minutes)

```python
async def execute_schedule_with_advanced_features():
    """Execute schedule with all advanced features enabled."""

    # Initialize monitors
    room_monitor = RoomMonitor(room_config)
    bathroom_controller = BathroomHumidityController()
    wood_oven = WoodOvenMonitor()
    water_monitor = WaterHeatingMonitor()
    solar_balancer = SolarLoadBalancer()

    # Load today's schedule
    schedule = load_schedule(today())

    # Read all sensors
    outdoor_temp = read_sensor('outdoor')
    room_temps = room_monitor.get_all_temperatures()
    bathroom_humidity = get_bathroom_humidity()
    chimney_temp = read_sensor('Savupiippu')
    water_temps = water_monitor.update()

    # Update monitors
    wood_oven.update(chimney_temp)

    # Calculate adjustments
    room_temp_adjustment = room_monitor.get_heating_adjustment()
    wood_oven_reduction = wood_oven.get_heating_reduction_factor()

    # Adjust schedule
    schedule.adjust_heating_hours(room_temp_adjustment, reason="room_temps")
    schedule.reduce_heating_hours(
        schedule.base_hours * wood_oven_reduction,
        reason="wood_oven"
    )

    # Bathroom humidity spike detection
    for bathroom, humidity in bathroom_humidity.items():
        if bathroom_controller.should_extend_heating(bathroom, humidity):
            schedule.add_heating_hours(1.0, reason=f"bathroom_{bathroom}_humidity")

    # Check available power budget
    available_power = calculate_available_power_budget(
        water_monitor,
        ac_on=True,
        evuoff_mode=check_evuoff_mode()
    )

    # Execute scheduled commands
    await execute_schedule_interval(schedule, available_power)

    # Solar load balancing (if enabled)
    if is_solar_optimization_window():
        await solar_balancer.balance_all_loads()
```

### Daily Planning with Price Prediction

```python
def generate_daily_schedule_with_prediction():
    """Generate tomorrow's schedule using wind-based price prediction."""

    # Fetch forecasts
    weather_forecast = fetch_weather_forecast()
    wind_forecast = fetch_wind_forecast()  # 3 days
    confirmed_prices = fetch_spot_prices()  # 2 days confirmed

    # Predict prices for days 3-7
    price_model = load_price_prediction_model()
    predicted_prices = price_model.predict(wind_forecast)

    # Calculate base heating needs
    outdoor_temp = weather_forecast['temperature'].mean()
    wood_oven = WoodOvenMonitor()
    base_heating_hours = calculate_adjusted_heating_hours(
        outdoor_temp,
        wood_oven
    )

    # Multi-day strategic planning
    if should_pre_heat(predicted_prices):
        base_heating_hours *= 1.2  # Add 20% extra heating today
        logger.info("Pre-heating before predicted price spike")

    # Generate optimized schedule
    schedule = optimize_schedule(
        heating_hours=base_heating_hours,
        prices=confirmed_prices,
        solar_forecast=weather_forecast['solar'],
        ev_charging_goal=get_ev_charging_goal(),
        optimization_mode=get_user_preference()
    )

    return schedule
```

---

## Configuration Schema Extensions

```yaml
# Advanced Features Configuration

advanced_features:
  enabled: true

  # Per-Room Monitoring
  room_monitoring:
    enabled: true
    rooms:
      living_room:
        temp_sensor: "28-000000000001"
        target_temp: 21.0
        priority: 3
        type: living

      bathroom_1:
        temp_sensor: "28-000000000002"
        humidity_sensor: "shelly-ht-000001"
        target_temp: 22.0
        priority: 2
        type: bathroom
        humidity_spike_threshold: 15
        humidity_high_threshold: 65

      # ... more rooms

  # Wood Oven Integration
  wood_oven:
    enabled: true
    chimney_sensor: "Savupiippu"
    heating_threshold_temp: 100  # °C
    reduction_schedule:
      0-6h: 0.50    # 50% reduction first 6 hours
      6-24h: 0.25   # 25% reduction 6-24 hours
      24-48h: 0.10  # 10% reduction 24-48 hours

  # Solar Load Balancing
  solar_balancing:
    enabled: true
    energy_meter_url: "http://192.168.1.5/status"
    period_length_s: 3600
    threshold_wh: 1000
    update_interval_s: 60
    optimization_windows:
      - start: "09:00"
        end: "16:00"  # Daytime solar hours

  # Wind → Price Prediction
  price_prediction:
    enabled: true
    model_type: "random_forest"  # linear | random_forest
    retrain_interval_days: 30
    lookback_days: 365
    confidence_threshold: 0.7

  # Water Heating Monitoring
  water_heating:
    enabled: true
    upper_sensor: "Kayttovesi ylh"
    lower_sensor: "Kayttovesi alh"
    target_temp: 60
    heating_trigger_temp: 50
    typical_duration_s: 1200
```

---

## Testing Strategy

### Unit Tests
- Test each monitor independently with mocked sensors
- Test adjustment calculations
- Test prediction model with synthetic data

### Integration Tests
- Test complete execution loop with fixture data
- Simulate wood oven heating event
- Simulate bathroom humidity spike
- Test solar load balancing with recorded meter data

### Simulation Tests
- Run against full year of historical data
- Measure:
  - Cost savings vs baseline
  - Comfort level (temperature deviations)
  - Solar self-consumption rate
  - Peak power management effectiveness

---

## Implementation Priority

### Phase 4.1 (Must Have - Week 1)
1. ✅ Water heating monitoring (safety critical)
2. ✅ Basic room temperature averaging
3. ✅ Wood oven detection

### Phase 4.2 (Should Have - Week 2)
4. ✅ Bathroom humidity spike detection
5. ✅ Per-room weighted temperature control
6. ✅ Wood oven heating reduction schedule

### Phase 4.3 (Nice to Have - Week 3)
7. ✅ Solar load balancing (real-time)
8. ✅ Wind → price prediction model (basic)

### Phase 4.4 (Future Enhancement - Week 4+)
9. ⏳ Advanced price prediction (ML models)
10. ⏳ Wood oven calendar integration
11. ⏳ Multi-room zoned heating optimization

---

**Status:** Ready for implementation
**Next Step:** Begin Phase 4.1 core architecture implementation
