# 🚖 NYC Taxi Fare Prediction (MLOps Edition)

This project is a comprehensive MLOps pipeline for predicting NYC taxi fares. It goes beyond a simple model to include data ingestion, storage, experiment tracking, and automated deployment.

---

## 🏗️ Architecture

The system is composed of several microservices orchestrated with Docker:

- **PostgreSQL**: Stores raw trip data.
- **MLflow**: Manages experiment runs and model versions.
- **Data API (FastAPI)**: Interface for database operations.
- **ETL Pipeline**: Automatically fetches data from NYC TLC.
- **Training Pipeline**: Trains models and registers them in MLflow.
- **Prediction API (FastAPI)**: Serves predictions using the latest model from MLflow.
- **Frontend (Streamlit)**: User-friendly web interface.

---

## 🚀 Quick Start

Ensure you have **Docker** and **Docker Compose** installed.

1.  **Clone and Start**:
    ```bash
    git clone https://github.com/mjdocevedo/nyc_taxi_simple.git
    cd nyc_taxi_simple
    docker-compose up --build
    ```

2.  **Monitor Progress**:
    - **Streamlit**: [http://localhost:8501](http://localhost:8501)
    - **MLflow Tracking**: [http://localhost:5000](http://localhost:5000)
    - **Data API Docs**: [http://localhost:8001/docs](http://localhost:8001/docs)

---

## 📂 Project Structure

```text
.
├── docker-compose.yml
└── services/
    ├── data_api/          # Database interface
    ├── etl/               # NYC TLC ingestion logic
    ├── training/          # MLflow integrated training
    ├── prediction_api/    # Model serving
    ├── frontend/          # Streamlit UI
    └── mlflow/            # MLflow configuration
```

## 🛠️ Customization

- **Change Data Range**: Edit `YEAR` and `MONTH` in `docker-compose.yml` under the `etl_pipeline` service.
- **Model Parameters**: Edit `N_ESTIMATORS` and `MAX_DEPTH` in `docker-compose.yml` under the `training_pipeline` service.

---

## 🚦 API Usage (Prediction)

**Endpoint**: `POST /fare_predict`

**Request Body**:
```json
{
  "vendor_id": 1,
  "passenger_count": 1.0,
  "rate_code_id": 1.0,
  "pickup_longitude": -73.985428,
  "pickup_latitude": 40.748817,
  "dropoff_longitude": -73.985428,
  "dropoff_latitude": 40.748817,
  "pu_location_id": 1,
  "do_location_id": 1,
  "pickup_hour": 14,
  "pickup_dayofweek": 2,
  "pickup_month": 1,
  "pickup_weekday": 1
}
```
