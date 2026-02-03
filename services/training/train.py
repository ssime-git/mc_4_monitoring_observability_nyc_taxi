import pandas as pd
import requests
import os
import joblib
import mlflow
import mlflow.sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score
import time

def fetch_data(api_url):
    print(f"Fetching data from {api_url}/trips/all/...")
    # Wait for data to be available
    max_retries = 10
    for i in range(max_retries):
        try:
            resp = requests.get(f"{api_url}/trips/all/")
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 0:
                    return pd.DataFrame(data)
                print("No data available yet, retrying...")
            else:
                print(f"Failed to fetch data: {resp.status_code}")
        except Exception as e:
            print(f"Error fetching data: {e}")
        time.sleep(10)
    return None

def preprocess_data(df):
    print("Preprocessing data...")
    # Original logic from train.py
    df["tpep_pickup_datetime"] = pd.to_datetime(df["tpep_pickup_datetime"], errors="coerce")
    df["tpep_dropoff_datetime"] = pd.to_datetime(df["tpep_dropoff_datetime"], errors="coerce")
    
    df["pickup_hour"] = df["tpep_pickup_datetime"].dt.hour
    df["pickup_dayofweek"] = df["tpep_pickup_datetime"].dt.dayofweek
    df["pickup_month"] = df["tpep_pickup_datetime"].dt.month
    df["pickup_weekday"] = df["pickup_dayofweek"].apply(lambda x: 1 if x < 5 else 0)
    
    # Calculate total fare as in original (target)
    df["total_fare"] = (
        df["fare_amount"]
        + df["extra"]
        + df["mta_tax"]
        + df["improvement_surcharge"]
        + df["tolls_amount"]
        + df.get("airport_fee", 0)
    )
    
    # Select features
    # Based on original train.py logic (dropping some and keeping others)
    features = ["vendor_id", "rate_code_id", "pu_location_id", "do_location_id", 
                "passenger_count", "pickup_hour", "pickup_dayofweek", 
                "pickup_month", "pickup_weekday"]
    
    # Drop rows with NaNs in features or target
    df = df.dropna(subset=features + ["total_fare"])
    
    X = df[features]
    y = df["total_fare"]
    return X, y

def train():
    api_url = os.getenv("DATA_API_URL", "http://data_api:8001")
    mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow_server:5000")
    
    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("NYC_Taxi_Fare_Prediction")

    df = fetch_data(api_url)
    if df is None:
        print("Data fetching failed. Exiting.")
        return

    X, y = preprocess_data(df)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    n_estimators = int(os.getenv("N_ESTIMATORS", 100))
    max_depth = int(os.getenv("MAX_DEPTH", 5))

    with mlflow.start_run():
        print(f"Starting training with n_estimators={n_estimators}, max_depth={max_depth}...")
        model = RandomForestRegressor(n_estimators=n_estimators, max_depth=max_depth, random_state=42)
        model.fit(X_train, y_train)

        # Predictions and metrics
        y_pred = model.predict(X_test)
        rmse = mean_squared_error(y_test, y_pred, squared=False)
        r2 = r2_score(y_test, y_pred)

        print(f"Metrics: RMSE={rmse:.4f}, R2={r2:.4f}")

        # Log parameters and metrics
        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_param("max_depth", max_depth)
        mlflow.log_metric("rmse", rmse)
        mlflow.log_metric("r2", r2)

        # Log and Register model
        mlflow.sklearn.log_model(
            sk_model=model,
            artifact_path="model",
            registered_model_name="NYC_Taxi_Fare_Model"
        )
        print("Model logged and registered in MLflow.")

if __name__ == "__main__":
    train()
