from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import mlflow.sklearn
import pandas as pd
import os
import time
import asyncio
import warnings
from collections import OrderedDict
import logging
import json
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

# ── Structured Logging (Pillar: Logs) ──
# Every line is JSON with level, timestamp, request_id (correlation id) and model_version.
# Payload values (locations, passengers...) are never logged: no personal data in logs.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
state = {"model_version": "none"}


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "prediction_api",
            "request_id": request_id_var.get(),
            "model_version": state["model_version"],
        }
        for key in ("trace_id", "span_id", "status", "endpoint", "duration_ms"):
            if hasattr(record, key):
                log_record[key] = getattr(record, key)
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("prediction_api")
warnings.filterwarnings("ignore", message="X has feature names")  # one noisy warning per request otherwise

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

invalid_requests_total = Counter(
    "invalid_requests_total",
    "Requests rejected at the door by input validation (HTTP 422)",
)
chaos_latency_seconds = Gauge(
    "chaos_latency_seconds",
    "Artificial latency injected for demo scenarios (0 = none)",
)

# Model quality once ground truth arrives (delayed labels): see POST /feedback
labeled_predictions_total = Counter(
    "labeled_predictions_total", "Predictions for which the real fare is now known")
prediction_squared_error_total = Counter(
    "prediction_squared_error_dollars2_total",
    "Sum of squared errors (predicted - actual)^2 on labeled predictions; RMSE = sqrt(rate(sum)/rate(count))")
feedback_unmatched_total = Counter(
    "feedback_unmatched_total", "Feedback received for an unknown or expired request id")

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

    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request_id_var.set(request_id)
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    # Label with the route TEMPLATE, never the raw path: scanners hitting random URLs
    # must not create unbounded time series.
    route = request.scope.get("route")
    endpoint = route.path if route is not None else "unmatched"
    method = request.method
    status = str(response.status_code)

    http_requests_total.labels(method=method, endpoint=endpoint, status=status).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)
    response.headers["X-Request-ID"] = request_id

    extra = {"status": response.status_code, "endpoint": endpoint, "duration_ms": round(duration * 1000, 1)}
    ctx = trace.get_current_span().get_span_context()
    if ctx and ctx.trace_id:
        extra["trace_id"] = format(ctx.trace_id, "032x")
        extra["span_id"] = format(ctx.span_id, "016x")
    logger.info(f"{method} {endpoint} {status}", extra=extra)
    return response


@app.exception_handler(RequestValidationError)
async def reject_invalid_request(request: Request, exc: RequestValidationError):
    """Defined behaviour for invalid input: reject with 422, count it, log it (fields only, no values)."""
    invalid_requests_total.inc()
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
    logger.warning(f"Rejected invalid request: fields={fields}")
    return JSONResponse(status_code=422, content={"detail": "Invalid request", "fields": fields})


# Define a Pydantic model for request data
class RequestData(BaseModel):
    """Input contract: types AND bounds (NYC TLC zone ids are 1..265)."""
    pickup_longitude: float = Field(0.0, ge=-180, le=180)
    pickup_latitude: float = Field(0.0, ge=-90, le=90)
    dropoff_longitude: float = Field(0.0, ge=-180, le=180)
    dropoff_latitude: float = Field(0.0, ge=-90, le=90)
    passenger_count: float = Field(1.0, ge=1, le=10)
    vendor_id: int = Field(1, ge=1, le=7)
    rate_code_id: float = Field(1.0, ge=1, le=99)
    pu_location_id: int = Field(1, ge=1, le=265)
    do_location_id: int = Field(1, ge=1, le=265)
    pickup_hour: int = Field(12, ge=0, le=23)
    pickup_dayofweek: int = Field(0, ge=0, le=6)
    pickup_month: int = Field(1, ge=1, le=12)
    pickup_weekday: int = Field(1, ge=0, le=1)


class Feedback(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    actual_fare: float = Field(gt=0, lt=5000)


recent_predictions: "OrderedDict[str, float]" = OrderedDict()  # bounded memory: last 10k predictions
MAX_PENDING = 10000


class ChaosSettings(BaseModel):
    latency_ms: int = Field(0, ge=0, le=10000)


CHAOS_ENABLED = os.getenv("CHAOS_ENABLED", "false").lower() == "true"
chaos = {"latency_ms": 0}

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
            state["model_version"] = str(new_version)
            
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
        time.sleep(5 if model is None else 60)

@app.on_event("startup")
async def startup_event():
    import threading
    threading.Thread(target=model_polling_loop, daemon=True).start()

@app.post("/fare_predict")
async def predict(data: RequestData):
    global model

    if chaos["latency_ms"]:
        await asyncio.sleep(chaos["latency_ms"] / 1000)  # demo scenario: degraded dependency
    with tracer.start_as_current_span("fare_prediction") as span:
        if model is None:
            # Never load a model inside a request: it blocks the event loop for seconds and shows up as a
            # fake latency incident. The polling thread retries every 5 s until a champion exists.
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
            recent_predictions[request_id_var.get()] = fare
            while len(recent_predictions) > MAX_PENDING:
                recent_predictions.popitem(last=False)

            span.set_attribute("prediction.fare", fare)
            span.set_attribute("prediction.inference_ms", inference_duration * 1000)

            return {"predicted_fare": fare}
        except Exception as e:
            prediction_errors_total.labels(error_type="inference_error").inc()
            logger.error(f"Prediction error: {type(e).__name__}")
            raise HTTPException(status_code=500, detail=str(e))

SMOKE_ROW = pd.DataFrame([{"vendor_id": 1, "rate_code_id": 1.0, "pu_location_id": 48, "do_location_id": 79,
                            "passenger_count": 1.0, "pickup_hour": 14, "pickup_dayofweek": 2,
                            "pickup_month": 1, "pickup_weekday": 1}])


@app.get("/health")
def health():
    """Readiness: 200 only if a model is loaded AND answers a real prediction."""
    if model is None:
        return JSONResponse(status_code=503, content={"status": "unavailable", "model_loaded": False,
                                                       "reason": "no champion model loaded"})
    try:
        fare = float(model.predict(SMOKE_ROW)[0])
    except Exception as exc:
        logger.error(f"Health smoke prediction failed: {type(exc).__name__}")
        return JSONResponse(status_code=503, content={"status": "unavailable", "model_loaded": True,
                                                       "reason": "model cannot predict"})
    return {"status": "ok", "model_loaded": True, "model_version": state["model_version"], "smoke_fare": round(fare, 2)}


@app.post("/feedback")
def feedback(item: Feedback):
    """Delayed ground truth: the real fare of an earlier prediction (matched by X-Request-ID)."""
    predicted = recent_predictions.pop(item.request_id, None)
    if predicted is None:
        feedback_unmatched_total.inc()
        raise HTTPException(status_code=404, detail="Unknown or expired request id")
    labeled_predictions_total.inc()
    prediction_squared_error_total.inc((predicted - item.actual_fare) ** 2)
    return {"error": round(predicted - item.actual_fare, 2)}


# ── Demo fault injection (disabled unless CHAOS_ENABLED=true) ──
@app.put("/admin/chaos")
def set_chaos(settings: ChaosSettings):
    if not CHAOS_ENABLED:
        raise HTTPException(status_code=403, detail="Fault injection disabled (set CHAOS_ENABLED=true)")
    chaos["latency_ms"] = settings.latency_ms
    chaos_latency_seconds.set(settings.latency_ms / 1000)
    logger.warning(f"Chaos latency set to {settings.latency_ms} ms")
    return chaos


@app.get("/admin/chaos")
def get_chaos():
    return {"enabled": CHAOS_ENABLED, **chaos}


# ── Prometheus metrics endpoint ──
@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
