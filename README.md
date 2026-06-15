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

---

## 📊 Observability & Monitoring

This project includes a complete production-grade observability and monitoring stack that demonstrates the core principles of modern system reliability, distributed tracing, and machine learning monitoring.

### 📌 Quick Links
- **Streamlit Frontend**: [http://localhost:8501](http://localhost:8501)
- **MLflow Tracking**: [http://localhost:5000](http://localhost:5000)
- **Grafana Dashboards**: [http://localhost:3000](http://localhost:3000) (User/Password: `admin` / `admin`)
- **Prometheus UI**: [http://localhost:9090](http://localhost:9090)
- **cAdvisor Metrics**: [http://localhost:8080](http://localhost:8080)
- **Data API Docs**: [http://localhost:8001/docs](http://localhost:8001/docs)

---

### 🏛️ The Three Pillars of Observability
1. **Metrics**: Scraped by **Prometheus** from `/metrics` endpoints instrumented with `prometheus_client` in our FastAPI services, plus container stats from **cAdvisor**.
2. **Logs**: Aggregated by **Loki** from container runtimes via **Promtail**. Application logs are structured in JSON format containing embedded trace IDs.
3. **Traces**: Exported by **OpenTelemetry** in the FastAPI services using OTLP protocol to **Tempo**, enabling detailed latency analysis of the end-to-end request lifecycle.

---

### 🎛️ The Three-Dashboards Pattern
Dashboards are auto-provisioned in Grafana:

1.  **L1 - Executive Dashboard** (Business & KPI View)
    - Total predictions served, average predicted fare, and service availability.
    - Cumulative revenue trends and pickup/dropoff value distribution.
    - Maps to **Layer 5 (Business)** of the monitoring architecture.

2.  **L2 - System Overview Dashboard** (RED & USE Methods)
    - **RED Method**: Request **R**ate, **E**rrors, and **D**uration (latency p50/p90/p99) for Data and Prediction API.
    - **USE Method**: Container **U**tilization, **S**aturation, and **E**rrors (CPU & Memory) via **cAdvisor**.
    - Maps to **Layer 3 (Application)** and **Layer 1 (Infrastructure)**.

3.  **L3 - Deep Dive Dashboard** (Debugging & Model Metrics)
    - Detailed ML model statistics (prediction latency, error types, model load times).
    - Data quality checks (null values rate on ingested fields, batch sizes).
    - Active alerts panel.
    - Maps to **Layer 4 (ML Model)** and **Layer 2 (Data Quality)**.
 
---

### 🚨 Alerting Strategy (Symptom-Based Alerting)
Prometheus is configured with proactive alerts targeting system symptoms rather than underlying causes:
- **HighErrorRate**: Fires when HTTP 5xx errors exceed 10% for 1 minute (Severity: `page`).
- **HighLatencyP99**: Fires when p99 request latency is > 2 seconds for 5 minutes (Severity: `ticket`).
- **LowPredictionThroughput**: Alerts if the prediction API is not receiving traffic (Severity: `ticket`).
- **ModelNotLoaded**: Alerts immediately if the prediction API fails to load the ML model (Severity: `page`).
- **HighContainerCPU & Memory**: Infrastructure alerts for utilization anomalies (Severity: `ticket`).

---

### 🔄 Load Simulator
To make the dashboards dynamic and interactive during live demonstrations, the **Load Simulator** service continuously generates mock taxi request traffic. It sends ~90% valid requests and ~10% deliberately malformed requests (e.g., missing fields, invalid data types) to demonstrate error metrics and trigger alert conditions.
