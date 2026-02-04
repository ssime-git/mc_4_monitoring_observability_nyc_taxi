from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import mlflow.sklearn
import pandas as pd
import os
import time

app = FastAPI(title="NYC Taxi Prediction API")

# Define a Pydantic model for request data
class RequestData(BaseModel):
    pickup_longitude: float = 0.0 # Coordinate placeholders if needed
    pickup_latitude: float = 0.0
    dropoff_longitude: float = 0.0
    dropoff_latitude: float = 0.0
    passenger_count: float = 1.0
    vendor_id: int = 1
    rate_code_id: float = 1.0
    pu_location_id: int = 1
    do_location_id: int = 1
    pickup_hour: int = 12
    pickup_dayofweek: int = 0
    pickup_month: int = 1
    pickup_weekday: int = 1

model = None
model_name = "NYC_Taxi_Fare_Model"
mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow_server:5000")

def load_latest_model():
    global model
    mlflow.set_tracking_uri(mlflow_uri)
    print(f"Loading latest model '{model_name}' from {mlflow_uri}...")
    
    # Retry logic as the training might still be running or mlflow server starting
    for i in range(20):
        try:
            # We can use 'models:/NYC_Taxi_Fare_Model/latest' or specific aliases if set
            # For simplicity, let's try to get the latest version
            model = mlflow.sklearn.load_model(f"models:/{model_name}/latest")
            print("Model loaded successfully.")
            return True
        except Exception as e:
            print(f"Could not load model (attempt {i+1}): {e}")
            time.sleep(15)
    return False

@app.on_event("startup")
async def startup_event():
    # Attempt to load model at startup
    # Note: In a real prod env, we might want to fail startup if model not found
    # But here we wait for the training pipeline to finish.
    import threading
    threading.Thread(target=load_latest_model).start()

@app.post("/fare_predict")
async def predict(data: RequestData):
    global model
    if model is None:
        if not load_latest_model():
            raise HTTPException(status_code=503, detail="Model not yet available")
    
    # Create input DataFrame
    input_data = data.model_dump()
    input_df = pd.DataFrame([input_data])
    
    # Feature Engineering (Must match train.py)
    # We don't have datetime objects here, we have integers from the request
    # The request model already asks for pickup_hour, pickup_dayofweek, pickup_month, pickup_weekday directly
    # So we just need to ensure the columns match perfectly.
    
    features = ["vendor_id", "rate_code_id", "pu_location_id", "do_location_id", 
                "passenger_count", "pickup_hour", "pickup_dayofweek", 
                "pickup_month", "pickup_weekday"]
    
    # Select only the features expected by the model
    try:
        input_df = input_df[features]
    except KeyError as e:
         raise HTTPException(status_code=400, detail=f"Missing feature: {e}")

    # Make prediction
    try:
        prediction = model.predict(input_df)
        return {"predicted_fare": float(prediction[0])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": model is not None}
