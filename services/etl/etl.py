import pandas as pd
import requests
import os
import time

def download_data(year, month):
    url = f"https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{year}-{month:02d}.parquet"
    filename = f"yellow_tripdata_{year}-{month:02d}.parquet"
    print(f"Downloading {url}...")
    response = requests.get(url)
    if response.status_code == 200:
        with open(filename, 'wb') as f:
            f.write(response.content)
        return filename
    else:
        print(f"Failed to download data: {response.status_code}")
        return None

def ingest_data(filename, api_url):
    import pyarrow.parquet as pq
    import numpy as np

    print(f"Reading {filename} in chunks...")
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

    total_rows_processed = 0
    # Process by row groups to save memory
    for i in range(parquet_file.num_row_groups):
        df = parquet_file.read_row_group(i).to_pandas()
        
        # Rename and clean
        df = df.rename(columns=expected_cols_map)
        for col in target_cols:
            if col not in df.columns:
                df[col] = None

        # Convert datetimes
        df["tpep_pickup_datetime"] = df["tpep_pickup_datetime"].dt.strftime('%Y-%m-%dT%H:%M:%S')
        df["tpep_dropoff_datetime"] = df["tpep_dropoff_datetime"].dt.strftime('%Y-%m-%dT%H:%M:%S')

        # Inner batching for API limits
        batch_size = 500
        for j in range(0, len(df), batch_size):
            batch_df = df.iloc[j:j+batch_size]
            batch = batch_df.to_dict(orient="records")
            # JSON-safe NaN handling
            batch = [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()} for row in batch]
            
            # Retry logic for name resolution or transient errors
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    resp = requests.post(f"{api_url}/trips/batch/", json=batch, timeout=30)
                    if resp.status_code == 201:
                        break
                    print(f"Batch failed ({resp.status_code}): {resp.text}. Retrying...")
                except Exception as e:
                    print(f"Connection error (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(2 ** attempt) # Exponential backoff
            
            total_rows_processed += len(batch_df)
            if total_rows_processed % 5000 == 0:
                print(f"Progress: {total_rows_processed} rows ingested...")
        
        # Small breath between row groups
        time.sleep(1)

if __name__ == "__main__":
    year = int(os.getenv("YEAR", 2024))
    month = int(os.getenv("MONTH", 1))
    api_url = os.getenv("DATA_API_URL", "http://data_api:8001")
    
    # Wait for Data API to be ready - check connectivity first
    print("Waiting for Data API connectivity...")
    for _ in range(30):
        try:
            requests.get(f"{api_url}/", timeout=5)
            print("Data API is up!")
            break
        except:
            time.sleep(5)
    
    filename = download_data(year, month)
    if filename:
        ingest_data(filename, api_url)
        os.remove(filename)
        print("ETL process completed.")
