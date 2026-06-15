"""
ETL Pipeline — Continuous 10-minute micro-batch ingestion.

Downloads a month's parquet file, splits it into micro-batches,
and ingests one batch every 10 minutes. After each batch, notifies
the data_api that new data is available (for drift detection & training triggers).
When all batches are exhausted, moves to the next month.
"""
import pandas as pd
import requests
import os
import time
import logging
import json
from datetime import datetime

# ── Structured Logging ──
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "etl_pipeline",
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("etl_pipeline")

# Configuration
INGESTION_INTERVAL_SECONDS = int(os.getenv("INGESTION_INTERVAL_SECONDS", 600))  # 10 minutes
BATCH_SIZE_ROWS = int(os.getenv("BATCH_SIZE_ROWS", 5000))  # rows per micro-batch
API_BATCH_SIZE = int(os.getenv("API_BATCH_SIZE", 500))  # rows per API call


def download_data(year, month):
    """Download a month's parquet file from NYC TLC."""
    url = f"https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{year}-{month:02d}.parquet"
    filename = f"yellow_tripdata_{year}-{month:02d}.parquet"
    logger.info(f"Downloading {url}...")
    response = requests.get(url, timeout=300)
    if response.status_code == 200:
        with open(filename, 'wb') as f:
            f.write(response.content)
        logger.info(f"Downloaded {filename} ({len(response.content) / 1024 / 1024:.1f} MB)")
        return filename
    else:
        logger.error(f"Failed to download data: {response.status_code}")
        return None


def split_into_batches(filename):
    """Split a parquet file into micro-batches (list of DataFrames)."""
    import pyarrow.parquet as pq

    logger.info(f"Splitting {filename} into micro-batches of {BATCH_SIZE_ROWS} rows...")
    parquet_file = pq.ParquetFile(filename)

    expected_cols_map = {
        "VendorID": "vendor_id",
        "RatecodeID": "rate_code_id",
        "PULocationID": "pu_location_id",
        "DOLocationID": "do_location_id",
        "Airport_fee": "airport_fee"
    }

    target_cols = [
        "vendor_id", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count",
        "trip_distance", "rate_code_id", "store_and_fwd_flag", "pu_location_id",
        "do_location_id", "payment_type", "fare_amount", "extra", "mta_tax",
        "tip_amount", "tolls_amount", "improvement_surcharge", "total_amount",
        "congestion_surcharge", "airport_fee"
    ]

    # Read all row groups and concatenate
    all_dfs = []
    for i in range(parquet_file.num_row_groups):
        df = parquet_file.read_row_group(i).to_pandas()
        df = df.rename(columns=expected_cols_map)
        for col in target_cols:
            if col not in df.columns:
                df[col] = None
        df["tpep_pickup_datetime"] = df["tpep_pickup_datetime"].dt.strftime('%Y-%m-%dT%H:%M:%S')
        df["tpep_dropoff_datetime"] = df["tpep_dropoff_datetime"].dt.strftime('%Y-%m-%dT%H:%M:%S')
        all_dfs.append(df[target_cols])

    full_df = pd.concat(all_dfs, ignore_index=True)
    total_rows = len(full_df)

    # Split into micro-batches
    batches = [full_df.iloc[i:i + BATCH_SIZE_ROWS] for i in range(0, total_rows, BATCH_SIZE_ROWS)]
    logger.info(f"Created {len(batches)} micro-batches from {total_rows} total rows")
    return batches


def ingest_batch(batch_df, api_url, batch_num, total_batches):
    """Ingest a single micro-batch via the Data API."""
    import numpy as np

    logger.info(f"Ingesting batch {batch_num}/{total_batches} ({len(batch_df)} rows)...")

    for j in range(0, len(batch_df), API_BATCH_SIZE):
        sub_batch_df = batch_df.iloc[j:j + API_BATCH_SIZE]
        batch = sub_batch_df.to_dict(orient="records")
        # JSON-safe NaN handling
        batch = [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()} for row in batch]

        max_retries = 5
        for attempt in range(max_retries):
            try:
                resp = requests.post(f"{api_url}/trips/batch/", json=batch, timeout=30)
                if resp.status_code == 201:
                    break
                logger.warning(f"Batch failed ({resp.status_code}): {resp.text}. Retrying...")
            except Exception as e:
                logger.warning(f"Connection error (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(2 ** attempt)

    logger.info(f"Batch {batch_num}/{total_batches} ingested successfully")


def notify_new_data(api_url, batch_num, rows_ingested):
    """Notify data_api that new data has been ingested."""
    try:
        resp = requests.post(
            f"{api_url}/notify-new-data/",
            json={"batch_number": batch_num, "rows_ingested": rows_ingested},
            timeout=10
        )
        if resp.status_code == 200:
            logger.info("Data API notified of new data")
        else:
            logger.warning(f"Failed to notify data API: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Could not notify data API: {e}")


def wait_for_api(api_url, max_retries=30):
    """Wait for the Data API to become available."""
    logger.info("Waiting for Data API connectivity...")
    for _ in range(max_retries):
        try:
            requests.get(f"{api_url}/", timeout=5)
            logger.info("Data API is up!")
            return True
        except Exception:
            time.sleep(5)
    logger.error("Data API did not become available")
    return False


if __name__ == "__main__":
    year = int(os.getenv("YEAR", 2024))
    month = int(os.getenv("MONTH", 1))
    api_url = os.getenv("DATA_API_URL", "http://data_api:8001")

    if not wait_for_api(api_url):
        exit(1)

    while True:
        # Download the parquet for current year/month
        filename = download_data(year, month)
        if filename is None:
            logger.error(f"Could not download data for {year}-{month:02d}. Retrying in 60s...")
            time.sleep(60)
            continue

        # Split into micro-batches
        batches = split_into_batches(filename)
        os.remove(filename)

        # Ingest one batch every INGESTION_INTERVAL_SECONDS
        for i, batch_df in enumerate(batches, 1):
            ingest_batch(batch_df, api_url, i, len(batches))
            notify_new_data(api_url, i, len(batch_df))

            if i < len(batches):
                logger.info(f"Sleeping {INGESTION_INTERVAL_SECONDS}s until next batch...")
                time.sleep(INGESTION_INTERVAL_SECONDS)

        logger.info(f"All batches for {year}-{month:02d} ingested. Moving to next month.")

        # Advance to next month
        month += 1
        if month > 12:
            month = 1
            year += 1
