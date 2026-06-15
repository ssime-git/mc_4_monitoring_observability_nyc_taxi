import os
import json
import uuid
import logging
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Gauge, Counter

from evidently import Report
from evidently.presets import DataDriftPreset, RegressionPreset

# ── Structured Logging ──
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "evidently_service",
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("evidently_service")

# ── Prometheus Metrics ──
data_drift_detected = Gauge("data_drift_detected", "Whether data drift is detected (1) or not (0)")
data_drift_share = Gauge("data_drift_share", "Share of drifted features (0.0 to 1.0)")
model_drift_rmse_reference = Gauge("model_drift_rmse_reference", "RMSE of the model on reference data")
model_drift_rmse_current = Gauge("model_drift_rmse_current", "RMSE of the model on current/new data")
drift_detection_runs_total = Counter("drift_detection_runs_total", "Total drift detection runs", ["type"])

app = FastAPI(title="NYC Taxi Evidently Drift Detection Service")

# Create reports directory
REPORTS_DIR = "/app/reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

# Mount reports directory for static access
app.mount("/reports_static", StaticFiles(directory=REPORTS_DIR), name="reports")

# Target features for data drift detection
FEATURES = [
    "vendor_id", "rate_code_id", "pu_location_id", "do_location_id",
    "passenger_count", "pickup_hour", "pickup_dayofweek",
    "pickup_month", "pickup_weekday", "total_fare"
]

class DataDriftRequest(BaseModel):
    reference_path: str
    current_data: List[Dict[str, Any]]

class ModelDriftRequest(BaseModel):
    reference_target: List[float]
    reference_prediction: List[float]
    current_target: List[float]
    current_prediction: List[float]


def preprocess_current_data(raw_data_list: List[Dict[str, Any]]) -> pd.DataFrame:
    """Preprocess the incoming raw JSON current data list into a pandas DataFrame."""
    df = pd.DataFrame(raw_data_list)
    if df.empty:
        return df

    # Normalize column names to match reference features
    df.columns = [col.lower() for col in df.columns]

    # Convert datetimes
    if "tpep_pickup_datetime" in df.columns:
        df["tpep_pickup_datetime"] = pd.to_datetime(df["tpep_pickup_datetime"], errors="coerce")
        df["pickup_hour"] = df["tpep_pickup_datetime"].dt.hour
        df["pickup_dayofweek"] = df["tpep_pickup_datetime"].dt.dayofweek
        df["pickup_month"] = df["tpep_pickup_datetime"].dt.month
        df["pickup_weekday"] = df["pickup_dayofweek"].apply(lambda x: 1 if x < 5 else 0)

    # Compute total_fare
    if "total_fare" not in df.columns:
        fare = df.get("fare_amount", 0.0).fillna(0.0)
        extra = df.get("extra", 0.0).fillna(0.0)
        mta = df.get("mta_tax", 0.0).fillna(0.0)
        imp = df.get("improvement_surcharge", 0.0).fillna(0.0)
        tolls = df.get("tolls_amount", 0.0).fillna(0.0)
        airport = df.get("airport_fee", 0.0).fillna(0.0)
        df["total_fare"] = fare + extra + mta + imp + tolls + airport

    # Ensure missing columns are filled with default values
    for col in FEATURES:
        if col not in df.columns:
            df[col] = 0.0 if col != "passenger_count" else 1.0

    return df[FEATURES]


@app.post("/detect-data-drift")
def detect_data_drift(request: DataDriftRequest):
    """Detect data drift between a saved reference parquet and current data."""
    drift_detection_runs_total.labels(type="data").inc()
    
    if not os.path.exists(request.reference_path):
        logger.error(f"Reference file not found: {request.reference_path}")
        raise HTTPException(status_code=400, detail="Reference data file not found on volume")

    try:
        logger.info(f"Loading reference data from {request.reference_path}")
        ref_df = pd.read_parquet(request.reference_path)
        ref_df.columns = [col.lower() for col in ref_df.columns]
        ref_df = ref_df[FEATURES]

        logger.info(f"Preprocessing {len(request.current_data)} current data rows")
        curr_df = preprocess_current_data(request.current_data)

        # Run Evidently
        logger.info("Running Evidently DataDriftPreset report...")
        data_drift_report = Report(metrics=[DataDriftPreset()])
        data_drift_report.run(reference_data=ref_df, current_data=curr_df)
        
        result_dict = data_drift_report.as_dict()

        # Parse results
        drifted = False
        share = 0.0
        for m in result_dict.get("metrics", []):
            metric_res = m.get("result", {})
            if "dataset_drift" in metric_res:
                drifted = metric_res["dataset_drift"]
                share = metric_res.get("share_of_drifted_columns", 0.0)
                break

        # Update Gauges
        data_drift_detected.set(1 if drifted else 0)
        data_drift_share.set(share)

        # Save HTML report
        report_id = f"data_drift_{uuid.uuid4().hex}"
        report_path = os.path.join(REPORTS_DIR, f"{report_id}.html")
        data_drift_report.save_html(report_path)
        logger.info(f"Evidently HTML report saved to {report_path}")

        return {
            "status": "success",
            "drift_detected": bool(drifted),
            "drift_share": float(share),
            "report_id": report_id,
            "report_url": f"/reports_static/{report_id}.html"
        }

    except Exception as e:
        logger.error(f"Failed to detect data drift: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/detect-model-drift")
def detect_model_drift(request: ModelDriftRequest):
    """Detect model performance drift using targets and predictions."""
    drift_detection_runs_total.labels(type="model").inc()
    
    try:
        logger.info("Constructing reference and current DataFrames for RegressionPreset")
        ref_df = pd.DataFrame({
            "target": request.reference_target,
            "prediction": request.reference_prediction
        })
        curr_df = pd.DataFrame({
            "target": request.current_target,
            "prediction": request.current_prediction
        })

        # Run Evidently
        logger.info("Running Evidently RegressionPreset report...")
        model_drift_report = Report(metrics=[RegressionPreset()])
        model_drift_report.run(reference_data=ref_df, current_data=curr_df)

        result_dict = model_drift_report.as_dict()

        # Parse RMSE metrics
        ref_rmse = 0.0
        curr_rmse = 0.0
        for m in result_dict.get("metrics", []):
            metric_res = m.get("result", {})
            if "reference" in metric_res and "rmse" in metric_res["reference"]:
                ref_rmse = metric_res["reference"]["rmse"]
            if "current" in metric_res and "rmse" in metric_res["current"]:
                curr_rmse = metric_res["current"]["rmse"]

        # Update Gauges
        model_drift_rmse_reference.set(ref_rmse)
        model_drift_rmse_current.set(curr_rmse)

        # Save HTML report
        report_id = f"model_drift_{uuid.uuid4().hex}"
        report_path = os.path.join(REPORTS_DIR, f"{report_id}.html")
        model_drift_report.save_html(report_path)
        logger.info(f"Evidently HTML report saved to {report_path}")

        return {
            "status": "success",
            "reference_rmse": float(ref_rmse),
            "current_rmse": float(curr_rmse),
            "report_id": report_id,
            "report_url": f"/reports_static/{report_id}.html"
        }

    except Exception as e:
        logger.error(f"Failed to detect model drift: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics")
def metrics():
    """Prometheus metrics scrape endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    return {"status": "ok", "service": "evidently_service"}
