#!/usr/bin/env python3
"""
Solar yield prediction for RedHouse heating system.

This script predicts solar panel yield based on:
- Historical solar production data (emeters bucket)
- Weather forecast radiation data (weather bucket)
- Pre-trained hourly conversion ratios

The predictions are written to the emeters bucket as 'solar_yield_avg_prediction' field.
"""

import argparse
import datetime
import glob
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.common.config import get_config
from src.common.influx_client import InfluxClient

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Snow cover factor (1.0 = no snow, 0.0 = full blockage)
# TODO: Make this configurable or auto-detected
SNOW_COVER_FACTOR = 1.0

# Model file path (relative to project root)
SOLAR_MODEL_FILE = Path(__file__).parent / "models" / "solar_yield_model.json"


def load_solar_model():
    """
    Load the solar yield prediction model.

    Returns:
        tuple: (prediction_ratio DataFrame, model_params dict)
    """
    try:
        logger.info(f"Loading solar model from {SOLAR_MODEL_FILE}")

        with open(SOLAR_MODEL_FILE, "r") as f:
            prediction_model = json.load(f)

        # Validate model structure
        if "prediction_ratio" not in prediction_model:
            raise ValueError("Model missing 'prediction_ratio' field")

        if len(prediction_model["prediction_ratio"]) != 24:
            raise ValueError("Model must have exactly 24 hourly ratios")

        prediction_ratio = pd.DataFrame(
            {
                "hour": list(range(24)),
                "radiation_shifted_to_solar_ratio": prediction_model["prediction_ratio"],
            }
        )

        logger.info(f"Loaded model version {prediction_model.get('version', 'unknown')}")
        logger.info(
            f"Model trained on data from {prediction_model.get('training_period_start')} "
            f"to {prediction_model.get('training_period_end')}"
        )

        return prediction_ratio, prediction_model

    except FileNotFoundError:
        logger.error(f"Solar model not found: {SOLAR_MODEL_FILE}")
        raise
    except (KeyError, ValueError) as e:
        logger.error(f"Invalid solar model format: {e}")
        raise


def fetch_weather_and_emeters_data(influx_client):
    """
    Fetch weather forecast and historical emeters data from InfluxDB.

    Args:
        influx_client: InfluxClient instance

    Returns:
        pandas.DataFrame: Combined weather and emeters data
    """
    config = influx_client.config

    # Fetch weather forecast (Global radiation, Air temperature)
    weather_query = f"""
    from(bucket: "{config.influxdb_bucket_weather}")
      |> range(start: -5d, stop: 2d)
      |> filter(fn: (r) => r["_measurement"] == "weather")
      |> filter(fn: (r) => r["_field"] == "Global radiation" or r["_field"] == "Air temperature")
      |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
    """

    # Fetch historical solar production
    emeters_query = f"""
    from(bucket: "{config.influxdb_bucket_emeters}")
      |> range(start: -5d, stop: 2d)
      |> filter(fn: (r) => r["_measurement"] == "energy")
      |> filter(fn: (r) => r["_field"] == "solar_yield_avg" or r["_field"] == "emeter_avg" or r["_field"] == "consumption_avg")
      |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
    """

    logger.info("Fetching weather forecast data...")
    weather_result = influx_client.query_api.query(weather_query)

    logger.info("Fetching historical emeters data...")
    emeters_result = influx_client.query_api.query(emeters_query)

    # Combine results into a single dictionary
    results = {}

    for table in weather_result:
        for record in table.records:
            t = record.get_time()
            if t not in results:
                results[t] = {}
            results[t][record.get_field()] = record.get_value()

    for table in emeters_result:
        for record in table.records:
            t = record.get_time()
            if t not in results:
                results[t] = {}
            results[t][record.get_field()] = record.get_value()

    # Convert to DataFrame
    df = pd.DataFrame(results).T.reset_index()
    df.rename(columns={"index": "timestamp"}, inplace=True)
    df["time_floor"] = df["timestamp"].dt.floor("H")
    df = df.sort_values(by="timestamp").reset_index(drop=True)

    logger.info(f"Fetched {len(df)} data points")
    return df


def predict_solar_yield(df, prediction_ratio, prediction_model):
    """
    Predict solar yield using weather forecast and trained model.

    Args:
        df: DataFrame with weather and emeters data
        prediction_ratio: DataFrame with hourly conversion ratios
        prediction_model: Model parameters dict

    Returns:
        pandas.DataFrame: Predictions with solar_yield_avg_prediction column
    """
    training_start = prediction_model.get("training_period_start")
    period_seconds = prediction_model.get("period_seconds", 3600)  # Default 1 hour
    radiation_timeshift_periods = prediction_model.get("radiation_timeshift_periods", -4)

    # Filter to prediction period
    df_pred = df[df.time_floor.dt.strftime("%Y-%m-%d") >= training_start].copy()

    if df_pred.empty:
        logger.warning("No data available for prediction period")
        return pd.DataFrame()

    # Group by period (default: hourly)
    df_pred["period"] = df_pred.apply(
        lambda r: datetime.datetime.utcfromtimestamp(
            int(r["timestamp"].timestamp()) - (int(r["timestamp"].timestamp()) % period_seconds)
        ),
        axis=1,
    )
    df_pred = df_pred.groupby(by="period").mean()

    # Add hour for joining with prediction ratios
    df_pred["hour"] = df_pred.index.hour

    # Shift radiation data (compensate for forecast lag)
    df_pred["radiation_shifted"] = df_pred["Global radiation"].shift(radiation_timeshift_periods)

    # Join with hourly prediction ratios
    pred = df_pred.join(prediction_ratio.set_index("hour"), on="hour", how="left")

    # Group by hour floor
    pred["hour_floor"] = pred.index.floor("H")
    pred = pred.groupby(by="hour_floor").mean()

    # Calculate prediction: radiation * ratio * snow_factor
    pred["solar_yield_avg_prediction"] = (
        pred["radiation_shifted"] * pred["radiation_shifted_to_solar_ratio"] * SNOW_COVER_FACTOR
    )

    # Calculate error on historical data (where actual solar_yield_avg exists)
    pred["prediction_error"] = pred["solar_yield_avg_prediction"] - pred["solar_yield_avg"]

    return pred


def write_predictions_to_influxdb(pred, influx_client):
    """
    Write solar yield predictions to InfluxDB emeters bucket.

    Args:
        pred: DataFrame with predictions
        influx_client: InfluxClient instance
    """
    config = influx_client.config
    points = []

    for timestamp, row in pred.iterrows():
        solar_pred = row["solar_yield_avg_prediction"]

        # Only write valid predictions (non-negative)
        if pd.notna(solar_pred) and solar_pred >= 0.0:
            # Shift timestamp to mid-hour (+30 minutes)
            ts_mid_hour = timestamp + pd.Timedelta(minutes=30)

            point = {
                "measurement": "energy",
                "tags": {},
                "fields": {"solar_yield_avg_prediction": float(solar_pred)},
                "time": ts_mid_hour,
            }
            points.append(point)

    if points:
        logger.info(f"Writing {len(points)} solar predictions to InfluxDB")
        influx_client.write_api.write(bucket=config.influxdb_bucket_emeters, record=points)
        logger.info("Solar predictions written successfully")
    else:
        logger.warning("No valid predictions to write")


def calculate_rmse(pred):
    """Calculate root mean square error for predictions with actual data."""
    errors = pred[pred["prediction_error"].notna()]["prediction_error"]
    if len(errors) > 0:
        return np.sqrt((errors * errors).sum()) / len(errors)
    return None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Predict solar yield for RedHouse")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to InfluxDB")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Starting solar yield prediction")
    logger.info("=" * 60)

    try:
        # Load configuration
        config = get_config()
        influx_client = InfluxClient(config)

        # Load pre-trained model
        prediction_ratio, prediction_model = load_solar_model()

        # Fetch data
        df = fetch_weather_and_emeters_data(influx_client)

        if df.empty:
            logger.error("No data available for prediction")
            return 1

        # Generate predictions
        pred = predict_solar_yield(df, prediction_ratio, prediction_model)

        if pred.empty:
            logger.error("Failed to generate predictions")
            return 1

        # Calculate error on historical data
        rmse = calculate_rmse(pred)
        if rmse is not None:
            logger.info(f"Prediction RMSE on historical data: {rmse:.6f}")

        # Write predictions to InfluxDB
        if args.dry_run:
            logger.info("DRY-RUN: Would write %d predictions", len(pred))
            logger.info(pred[["solar_yield_avg_prediction"]].head(10))
        else:
            write_predictions_to_influxdb(pred, influx_client)

        logger.info("Solar yield prediction completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Solar yield prediction failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
