from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
import mlflow.sklearn
import pandas as pd
import os
import time
import logging
import json
from datetime import datetime

# ── Structured Logging (Pillar: Logs) ──
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "prediction_api",
        }
        if hasattr(record, "trace_id"):
            log_record["trace_id"] = record.trace_id
        if hasattr(record, "span_id"):
            log_record["span_id"] = record.span_id
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("prediction_api")

# ── Prometheus Metrics (Pillar: Metrics) ──
from prometheus_client import Counter, Histogram, Gauge, Summary, generate_latest, CONTENT_TYPE_LATEST

# RED Method — Rate
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

# RED Method — Duration
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# ML Model Metrics (Layer 4: Model)
prediction_requests_total = Counter(
    "prediction_requests_total",
    "Total prediction requests served"
)
prediction_errors_total = Counter(
    "prediction_errors_total",
    "Total prediction errors",
    ["error_type"]
)
prediction_value = Histogram(
    "prediction_value_dollars",
    "Distribution of predicted fare values",
    buckets=[5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200]
)
prediction_latency_seconds = Histogram(
    "prediction_latency_seconds",
    "Time to generate a single prediction (model inference only)",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
model_load_duration_seconds = Gauge(
    "model_load_duration_seconds",
    "Time taken to load the ML model from MLflow"
)
model_loaded = Gauge(
    "model_loaded",
    "Whether the ML model is currently loaded (1=yes, 0=no)"
)

# Business Metrics (Layer 5: Business)
total_predicted_revenue = Counter(
    "total_predicted_revenue_dollars",
    "Running sum of all predicted fares (proxy for business throughput)"
)
model_version_gauge = Gauge(
    "model_version",
    "Currently loaded model version",
    ["version"]
)

# ── OpenTelemetry Tracing (Pillar: Traces) ──
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

TEMPO_ENDPOINT = os.getenv("TEMPO_ENDPOINT", "tempo:4317")

resource = Resource.create({"service.name": "prediction_api"})
tracer_provider = TracerProvider(resource=resource)

try:
    otlp_exporter = OTLPSpanExporter(endpoint=TEMPO_ENDPOINT, insecure=True)
    tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
except Exception as e:
    logger.warning(f"Could not initialize OTLP exporter: {e}")

trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("prediction_api")

app = FastAPI(title="NYC Taxi Prediction API")

# Auto-instrument FastAPI with OpenTelemetry
FastAPIInstrumentor.instrument_app(app)

# ── Middleware for RED metrics ──
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    endpoint = request.url.path
    method = request.method
    status = str(response.status_code)

    http_requests_total.labels(method=method, endpoint=endpoint, status=status).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)

    # Log each request with trace context
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    extra = {}
    if ctx and ctx.trace_id:
        extra["trace_id"] = format(ctx.trace_id, "032x")
        extra["span_id"] = format(ctx.span_id, "016x")

    log = logging.LogRecord(
        name="prediction_api", level=logging.INFO, pathname="", lineno=0,
        msg=f"{method} {endpoint} {status} {duration:.3f}s",
        args=None, exc_info=None
    )
    for k, v in extra.items():
        setattr(log, k, v)
    logger.handle(log)

    return response


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
current_model_version = None

def load_champion_model():
    global model, current_model_version
    mlflow.set_tracking_uri(mlflow_uri)
    from mlflow.tracking import MlflowClient
    client = MlflowClient(tracking_uri=mlflow_uri)
    
    try:
        # Check if the champion alias exists
        model_version = client.get_model_version_by_alias(model_name, "champion")
        new_version = model_version.version
        
        if new_version != current_model_version:
            logger.info(f"New champion model version detected: {new_version}. Loading...")
            start_time = time.time()
            loaded_model = mlflow.sklearn.load_model(f"models:/{model_name}@champion")
            model = loaded_model
            current_model_version = new_version
            
            # Update metrics
            load_duration = time.time() - start_time
            model_load_duration_seconds.set(load_duration)
            model_loaded.set(1)
            model_version_gauge.clear()
            model_version_gauge.labels(version=new_version).set(1)
            
            logger.info(f"Champion model version {new_version} loaded successfully in {load_duration:.2f}s")
        return True
    except Exception as e:
        logger.warning(f"Champion not available yet: {e}")
        return False

def model_polling_loop():
    logger.info("Starting champion model polling loop...")
    while True:
        try:
            load_champion_model()
        except Exception as e:
            logger.error(f"Error in model polling loop: {e}")
        time.sleep(60)

@app.on_event("startup")
async def startup_event():
    import threading
    threading.Thread(target=model_polling_loop, daemon=True).start()

@app.post("/fare_predict")
async def predict(data: RequestData):
    global model

    with tracer.start_as_current_span("fare_prediction") as span:
        if model is None:
            if not load_champion_model():
                prediction_errors_total.labels(error_type="model_unavailable").inc()
                raise HTTPException(status_code=503, detail="Model not yet available")

        # Create input DataFrame
        input_data = data.model_dump()
        input_df = pd.DataFrame([input_data])

        span.set_attribute("input.vendor_id", data.vendor_id)
        span.set_attribute("input.pu_location_id", data.pu_location_id)
        span.set_attribute("input.do_location_id", data.do_location_id)

        features = ["vendor_id", "rate_code_id", "pu_location_id", "do_location_id",
                    "passenger_count", "pickup_hour", "pickup_dayofweek",
                    "pickup_month", "pickup_weekday"]

        # Select only the features expected by the model
        try:
            input_df = input_df[features]
        except KeyError as e:
            prediction_errors_total.labels(error_type="missing_feature").inc()
            raise HTTPException(status_code=400, detail=f"Missing feature: {e}")

        # Make prediction
        try:
            inference_start = time.time()
            prediction = model.predict(input_df)
            inference_duration = time.time() - inference_start

            fare = float(prediction[0])

            # Record model & business metrics
            prediction_requests_total.inc()
            prediction_value.observe(fare)
            prediction_latency_seconds.observe(inference_duration)
            total_predicted_revenue.inc(fare)

            span.set_attribute("prediction.fare", fare)
            span.set_attribute("prediction.inference_ms", inference_duration * 1000)

            return {"predicted_fare": fare}
        except Exception as e:
            prediction_errors_total.labels(error_type="inference_error").inc()
            logger.error(f"Prediction error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": model is not None}

# ── Prometheus metrics endpoint ──
@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
