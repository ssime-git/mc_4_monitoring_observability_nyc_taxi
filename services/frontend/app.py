import requests
import streamlit as st
from datetime import datetime
import os

def taxi_api_call(payload):
    # Use environment variable for the prediction API URL
    api_url = os.getenv("PREDICTION_API_URL", "http://prediction_api:8000")
    url = f"{api_url}/fare_predict"
    headers = {'accept': 'application/json'}
    try:
        response = requests.post(url, json=payload, headers=headers)
        return response
    except Exception as e:
        st.error(f"Error connecting to API: {e}")
        return None

def main():
    st.set_page_config(page_title="NYC Taxi Fare Predictor", layout="wide")
    st.title("🚖 NYC Taxi Fare Prediction (MLOps Edition)")
    st.markdown("""
    This app uses a Random Forest model tracked by **MLflow**, with data stored in **PostgreSQL**.
    """)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Trip Details")
        vendor_id = st.selectbox("Vendor ID", [1, 2], index=0)
        passenger_count = st.number_input("Passenger Count", min_value=1, max_value=10, value=1)
        rate_code_id = st.number_input("Rate Code ID", min_value=1.0, max_value=6.0, value=1.0)
        
        st.subheader("Coordinates")
        pickup_longitude = st.number_input("Pickup Longitude", value=-73.985428, format="%.6f")
        pickup_latitude = st.number_input("Pickup Latitude", value=40.748817, format="%.6f")
        dropoff_longitude = st.number_input("Dropoff Longitude", value=-73.985428, format="%.6f")
        dropoff_latitude = st.number_input("Dropoff Latitude", value=40.748817, format="%.6f")

    with col2:
        st.subheader("Location IDs")
        pu_location_id = st.number_input("Pickup Location ID", min_value=1, max_value=265, value=1)
        do_location_id = st.number_input("Dropoff Location ID", min_value=1, max_value=265, value=1)

        st.subheader("Time Information")
        pickup_datetime = st.date_input("Pickup Date", datetime.today())
        pickup_time = st.time_input("Pickup Time", datetime.now().time())

    if st.button("Predict Fare", use_container_width=True):
        pickup_dt = datetime.combine(pickup_datetime, pickup_time)
        
        payload = {
            "vendor_id": vendor_id,
            "passenger_count": float(passenger_count),
            "rate_code_id": rate_code_id,
            "pickup_longitude": pickup_longitude,
            "pickup_latitude": pickup_latitude,
            "dropoff_longitude": dropoff_longitude,
            "dropoff_latitude": dropoff_latitude,
            "pu_location_id": pu_location_id,
            "do_location_id": do_location_id,
            "pickup_hour": pickup_dt.hour,
            "pickup_dayofweek": pickup_dt.weekday(),
            "pickup_month": pickup_dt.month,
            "pickup_weekday": 1 if pickup_dt.weekday() < 5 else 0
        }

        with st.spinner("Getting prediction..."):
            response = taxi_api_call(payload)

            if response and response.status_code == 200:
                prediction = response.json()
                st.balloons()
                st.success(f"### Predicted Fare Amount: ${prediction['predicted_fare']:.2f}")
            elif response:
                st.error(f"API Error: {response.status_code} - {response.text}")
            else:
                st.error("Failed to receive response from the API.")

if __name__ == "__main__":
    main()
