# RedHouse Prediction Models

This directory contains pre-trained models used by RedHouse for forecasting.

## Solar Yield Prediction Model

**File:** `solar_yield_model.json`

**Purpose:** Converts weather forecast global radiation data into predicted solar panel yield.

**How it works:**
1. Takes hourly global radiation forecast (W/m²) from FMI weather API
2. Applies hour-specific conversion ratios (0-23)
3. Outputs predicted solar yield (W)

**Model Parameters:**
- `prediction_ratio`: Array of 24 hourly conversion factors (radiation → solar yield)
- `period_seconds`: Time resolution (900 = 15 minutes)
- `radiation_timeshift_periods`: Lag compensation for forecast accuracy (-2 = shift back 2 periods)
- `training_period_start/end`: Historical data period used for training

**Training Data:**
- Historical solar panel production from energy meters
- Historical weather forecast accuracy
- Period: May 11-23, 2023

**Usage:**
The `predict_solar_yield.py` script automatically loads this model and generates predictions for the next 2 days.

**Future Improvements:**
- Seasonal models (summer vs winter sun angle)
- Snow cover detection/adjustment
- Panel degradation factor
- Automated retraining with recent data
