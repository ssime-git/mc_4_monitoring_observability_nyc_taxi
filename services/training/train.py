"""
Training Pipeline — FastAPI service with 24h scheduled retraining and manual trigger.

Runs as a long-lived service that:
- Automatically retrains every 24 hours
- Exposes POST /trigger-training for manual retraining
- Compares new model against current champion on RMSE
- Promotes to 'champion' alias in MLflow if better
- Saves reference data for Evidently drift detection
- Calls Evidently service for model performance reports
"""
import pandas as pd
import requests
import os
import time
import threading
import logging
import json
from datetime import datetime
from contextlib import asynccontextmanager

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

# ── Structured Logging ──
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "training_pipeline",
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("training_pipeline")

# ── Configuration ──
DATA_API_URL = os.getenv("DATA_API_URL", "http://data_api:8001")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow_server:5000")
EVIDENTLY_URL = os.getenv("EVIDENTLY_URL", "http://evidently_service:8002")
REFERENCE_DATA_PATH = os.getenv("REFERENCE_DATA_PATH", "/app/reference_data/reference.parquet")
N_ESTIMATORS = int(os.getenv("N_ESTIMATORS", 100))
MAX_DEPTH = int(os.getenv("MAX_DEPTH", 5))
RETRAIN_INTERVAL_SECONDS = int(os.getenv("RETRAIN_INTERVAL_SECONDS", 86400))  # 24 hours
MODEL_NAME = "NYC_Taxi_Fare_Model"

# ── Training State ──
_training_lock = threading.Lock()
_training_status = {
    "state": "idle",  # idle, training, completed, error
    "last_run_time": None,
    "last_run_duration_seconds": None,
    "last_model_version": None,
    "last_rmse": None,
    "last_r2": None,
    "was_promoted": None,
    "total_runs": 0,
    "error_message": None,
}


def fetch_data():
    """Fetch all training data from the Data API."""
    logger.info(f"Fetching data from {DATA_API_URL}/trips/all/...")
    max_retries = 10
    for i in range(max_retries):
        try:
            resp = requests.get(f"{DATA_API_URL}/trips/all/", timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 0:
                    logger.info(f"Fetched {len(data)} trips")
                    return pd.DataFrame(data)
                logger.warning("No data available yet, retrying...")
            else:
                logger.warning(f"Failed to fetch data: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Error fetching data: {e}")
        time.sleep(10)
    return None


def preprocess_data(df):
    """Preprocess data for training."""
    logger.info("Preprocessing data...")
    df["tpep_pickup_datetime"] = pd.to_datetime(df["tpep_pickup_datetime"], errors="coerce")
    df["tpep_dropoff_datetime"] = pd.to_datetime(df["tpep_dropoff_datetime"], errors="coerce")

    df["pickup_hour"] = df["tpep_pickup_datetime"].dt.hour
    df["pickup_dayofweek"] = df["tpep_pickup_datetime"].dt.dayofweek
    df["pickup_month"] = df["tpep_pickup_datetime"].dt.month
    df["pickup_weekday"] = df["pickup_dayofweek"].apply(lambda x: 1 if x < 5 else 0)

    df["total_fare"] = (
        df["fare_amount"]
        + df["extra"]
        + df["mta_tax"]
        + df["improvement_surcharge"]
        + df["tolls_amount"]
        + df.get("airport_fee", 0)
    )

    features = ["vendor_id", "rate_code_id", "pu_location_id", "do_location_id",
                "passenger_count", "pickup_hour", "pickup_dayofweek",
                "pickup_month", "pickup_weekday"]

    df = df.dropna(subset=features + ["total_fare"])

    X = df[features]
    y = df["total_fare"]
    return X, y, df


def get_champion_rmse(client):
    """Get the RMSE of the current champion model, if one exists."""
    try:
        # Try to get the champion alias
        model_version = client.get_model_version_by_alias(MODEL_NAME, "champion")
        run = client.get_run(model_version.run_id)
        champion_rmse = run.data.metrics.get("rmse")
        logger.info(f"Current champion: version {model_version.version}, RMSE={champion_rmse}")
        return champion_rmse, model_version.version
    except Exception as e:
        logger.info(f"No current champion found: {e}")
        return None, None


def save_reference_data(df):
    """Save the training data as reference for drift detection."""
    try:
        os.makedirs(os.path.dirname(REFERENCE_DATA_PATH), exist_ok=True)
        df.to_parquet(REFERENCE_DATA_PATH, index=False)
        logger.info(f"Reference data saved to {REFERENCE_DATA_PATH} ({len(df)} rows)")
    except Exception as e:
        logger.warning(f"Failed to save reference data: {e}")


def notify_evidently(reference_path, current_data_sample):
    """Call Evidently service for drift detection after training."""
    try:
        resp = requests.post(
            f"{EVIDENTLY_URL}/detect-data-drift",
            json={
                "reference_path": reference_path,
                "current_data": current_data_sample,
            },
            timeout=60
        )
        if resp.status_code == 200:
            result = resp.json()
            logger.info(f"Evidently drift report: drift_detected={result.get('drift_detected')}, "
                        f"drift_share={result.get('drift_share')}")
            return result
        else:
            logger.warning(f"Evidently returned {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"Could not reach Evidently service: {e}")
    return None


def acknowledge_training():
    """Notify data_api that training has been completed."""
    try:
        requests.post(f"{DATA_API_URL}/acknowledge-training/", timeout=10)
        logger.info("Data API training acknowledgment sent")
    except Exception as e:
        logger.warning(f"Could not acknowledge training: {e}")


def run_training():
    """Execute a full training run with MLflow logging and model promotion."""
    global _training_status

    if not _training_lock.acquire(blocking=False):
        logger.warning("Training already in progress, skipping")
        return {"status": "already_running"}

    try:
        _training_status["state"] = "training"
        _training_status["error_message"] = None
        start_time = time.time()
        logger.info("=" * 60)
        logger.info("TRAINING RUN STARTED")
        logger.info("=" * 60)

        # Set up MLflow
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment("NYC_Taxi_Fare_Prediction")
        client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)

        # Fetch data
        df = fetch_data()
        if df is None:
            _training_status["state"] = "error"
            _training_status["error_message"] = "Data fetching failed"
            logger.error("Data fetching failed. Aborting training run.")
            return {"status": "error", "message": "Data fetching failed"}

        X, y, full_df = preprocess_data(df)
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        # Get current champion for comparison
        champion_rmse, champion_version = get_champion_rmse(client)

        # Train model
        with mlflow.start_run() as run:
            logger.info(f"Training with n_estimators={N_ESTIMATORS}, max_depth={MAX_DEPTH}...")
            model = RandomForestRegressor(
                n_estimators=N_ESTIMATORS,
                max_depth=MAX_DEPTH,
                random_state=42
            )
            model.fit(X_train, y_train)

            # Evaluate
            y_pred = model.predict(X_test)
            mse = mean_squared_error(y_test, y_pred)
            rmse = mse ** 0.5
            r2 = r2_score(y_test, y_pred)

            logger.info(f"Metrics: RMSE={rmse:.4f}, R2={r2:.4f}")

            # Log parameters and metrics
            mlflow.log_param("n_estimators", N_ESTIMATORS)
            mlflow.log_param("max_depth", MAX_DEPTH)
            mlflow.log_param("training_data_rows", len(X_train))
            mlflow.log_param("test_data_rows", len(X_test))
            mlflow.log_metric("rmse", rmse)
            mlflow.log_metric("r2", r2)

            # Log and register model
            result = mlflow.sklearn.log_model(
                sk_model=model,
                artifact_path="model",
                registered_model_name=MODEL_NAME
            )
            logger.info(f"Model logged and registered: run_id={run.info.run_id}")

            # Get the new model version
            time.sleep(2)  # Brief pause for MLflow to register
            versions = client.search_model_versions(f"name='{MODEL_NAME}'")
            new_version = max(versions, key=lambda v: int(v.version))
            logger.info(f"New model version: {new_version.version}")

            # ── Model Promotion Logic ──
            promoted = False
            if champion_rmse is None:
                # No champion exists — promote this one
                client.set_registered_model_alias(MODEL_NAME, "champion", new_version.version)
                promoted = True
                logger.info(f"No existing champion. Version {new_version.version} promoted to champion!")
            elif rmse < champion_rmse:
                # New model is better — promote
                client.set_registered_model_alias(MODEL_NAME, "champion", new_version.version)
                promoted = True
                improvement = ((champion_rmse - rmse) / champion_rmse) * 100
                logger.info(f"New champion! Version {new_version.version} (RMSE {rmse:.4f}) "
                            f"beats v{champion_version} (RMSE {champion_rmse:.4f}). "
                            f"Improvement: {improvement:.2f}%")
            else:
                logger.info(f"Version {new_version.version} (RMSE {rmse:.4f}) did NOT beat "
                            f"champion v{champion_version} (RMSE {champion_rmse:.4f}). No promotion.")

            # Tag the run with promotion status
            mlflow.set_tag("promoted_to_champion", str(promoted))
            if promoted:
                mlflow.set_tag("champion_version", new_version.version)

        # ── Save reference data for Evidently ──
        save_reference_data(full_df)

        # ── Notify Evidently for drift baseline ──
        try:
            sample_resp = requests.get(f"{DATA_API_URL}/trips/sample/?n=1000", timeout=30)
            if sample_resp.status_code == 200:
                notify_evidently(REFERENCE_DATA_PATH, sample_resp.json())
        except Exception as e:
            logger.warning(f"Could not run drift detection: {e}")

        # ── Acknowledge training to data_api ──
        acknowledge_training()

        # Update status
        duration = time.time() - start_time
        _training_status.update({
            "state": "completed",
            "last_run_time": datetime.utcnow().isoformat() + "Z",
            "last_run_duration_seconds": round(duration, 2),
            "last_model_version": new_version.version,
            "last_rmse": round(rmse, 4),
            "last_r2": round(r2, 4),
            "was_promoted": promoted,
            "total_runs": _training_status["total_runs"] + 1,
        })

        logger.info(f"Training run completed in {duration:.1f}s")
        logger.info("=" * 60)

        return {
            "status": "completed",
            "model_version": new_version.version,
            "rmse": round(rmse, 4),
            "r2": round(r2, 4),
            "promoted": promoted,
            "duration_seconds": round(duration, 2),
        }

    except Exception as e:
        _training_status["state"] = "error"
        _training_status["error_message"] = str(e)
        logger.error(f"Training run failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        _training_lock.release()


def scheduled_training_loop():
    """Background thread that triggers training every RETRAIN_INTERVAL_SECONDS."""
    logger.info(f"Scheduled training loop started (interval: {RETRAIN_INTERVAL_SECONDS}s / "
                f"{RETRAIN_INTERVAL_SECONDS / 3600:.1f}h)")

    # Wait for services to be ready before first training
    logger.info("Waiting for MLflow and Data API to be ready...")
    for _ in range(60):
        try:
            requests.get(f"{MLFLOW_TRACKING_URI}/health", timeout=5)
            requests.get(f"{DATA_API_URL}/", timeout=5)
            logger.info("Dependencies are ready. Starting first training run...")
            break
        except Exception:
            time.sleep(10)

    # Initial training run on startup
    run_training()

    # Then schedule recurring runs
    while True:
        logger.info(f"Next scheduled training in {RETRAIN_INTERVAL_SECONDS}s "
                    f"({RETRAIN_INTERVAL_SECONDS / 3600:.1f}h)")
        time.sleep(RETRAIN_INTERVAL_SECONDS)
        logger.info("Scheduled training triggered")
        run_training()


# ── FastAPI Application ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the scheduled training thread
    training_thread = threading.Thread(target=scheduled_training_loop, daemon=True)
    training_thread.start()
    logger.info("Training scheduler started")
    yield

app = FastAPI(title="NYC Taxi Training Pipeline", lifespan=lifespan)


class TrainingResponse(BaseModel):
    status: str
    message: Optional[str] = None
    model_version: Optional[str] = None
    rmse: Optional[float] = None
    r2: Optional[float] = None
    promoted: Optional[bool] = None
    duration_seconds: Optional[float] = None


@app.post("/trigger-training", response_model=TrainingResponse)
def trigger_training(background_tasks: BackgroundTasks):
    """Manually trigger a training run."""
    if _training_status["state"] == "training":
        return TrainingResponse(status="already_running", message="Training is already in progress")

    logger.info("Manual training triggered via API")
    background_tasks.add_task(run_training)
    return TrainingResponse(status="triggered", message="Training started in background")


@app.get("/status")
def get_status():
    """Get the current training pipeline status."""
    return {
        **_training_status,
        "retrain_interval_hours": RETRAIN_INTERVAL_SECONDS / 3600,
        "model_name": MODEL_NAME,
    }


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "training_pipeline"}
