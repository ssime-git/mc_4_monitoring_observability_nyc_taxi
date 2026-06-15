from fastapi import FastAPI, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
import models, database
from pydantic import BaseModel
from datetime import datetime
import time
import logging
import json
import os
import random

# ── Structured Logging (Pillar: Logs) ──
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "data_api",
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
logger = logging.getLogger("data_api")

# ── Prometheus Metrics (Pillar: Metrics) ──
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

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

# Business Metrics (Layer 5)
trips_ingested_total = Counter(
    "trips_ingested_total",
    "Total number of trips ingested into the database"
)
trips_batch_size = Histogram(
    "trips_batch_size",
    "Size of trip batches sent to the API",
    buckets=[1, 10, 50, 100, 250, 500, 1000]
)

# Data Quality Metrics (Layer 2: Data)
data_null_rate = Gauge(
    "data_null_rate",
    "Rate of null values in ingested trip fields",
    ["field"]
)
data_ingestion_errors_total = Counter(
    "data_ingestion_errors_total",
    "Total data ingestion errors"
)

# Infrastructure Gauge (Layer 1)
db_connections_active = Gauge(
    "db_connections_active",
    "Number of active database connections"
)

# New data tracking metrics
new_data_batches_total = Counter(
    "new_data_batches_total",
    "Total number of new data batches received since startup"
)

# ── OpenTelemetry Tracing (Pillar: Traces) ──
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

TEMPO_ENDPOINT = os.getenv("TEMPO_ENDPOINT", "tempo:4317")

resource = Resource.create({"service.name": "data_api"})
tracer_provider = TracerProvider(resource=resource)

try:
    otlp_exporter = OTLPSpanExporter(endpoint=TEMPO_ENDPOINT, insecure=True)
    tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
except Exception as e:
    logger.warning(f"Could not initialize OTLP exporter: {e}")

trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("data_api")

app = FastAPI(title="NYC Taxi Data API")

# Auto-instrument FastAPI with OpenTelemetry
FastAPIInstrumentor.instrument_app(app)

# ── In-memory new-data tracking ──
_new_data_available = False
_new_data_rows_since_last_training = 0
_last_data_notification_time = None

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
        name="data_api", level=logging.INFO, pathname="", lineno=0,
        msg=f"{method} {endpoint} {status} {duration:.3f}s",
        args=None, exc_info=None
    )
    for k, v in extra.items():
        setattr(log, k, v)
    logger.handle(log)

    return response


# Pydantic models for API
class TripBase(BaseModel):
    vendor_id: int = None
    tpep_pickup_datetime: datetime
    tpep_dropoff_datetime: datetime
    passenger_count: float = None
    trip_distance: float = None
    rate_code_id: float = None
    store_and_fwd_flag: str = None
    pu_location_id: int = None
    do_location_id: int = None
    payment_type: int = None
    fare_amount: float = None
    extra: float = None
    mta_tax: float = None
    tip_amount: float = None
    tolls_amount: float = None
    improvement_surcharge: float = None
    total_amount: float = None
    congestion_surcharge: float = None
    airport_fee: float = None

class TripCreate(TripBase):
    pass

class TripResponse(TripBase):
    id: int

    class Config:
        from_attributes = True

class NewDataNotification(BaseModel):
    batch_number: int = 0
    rows_ingested: int = 0

class NewDataStatus(BaseModel):
    new_data_available: bool
    rows_since_last_training: int
    last_notification_time: Optional[str] = None


@app.on_event("startup")
def on_startup():
    # Wait for DB to be ready and create tables
    import time
    max_retries = 10
    while max_retries > 0:
        try:
            models.Base.metadata.create_all(bind=database.engine)
            logger.info("Database tables created successfully.")
            break
        except Exception as e:
            logger.warning(f"Database not ready yet ({e}). Retrying in 5s...")
            time.sleep(5)
            max_retries -= 1

@app.get("/")
def read_root():
    return {"message": "NYC Taxi Data API is running"}

@app.post("/trips/", response_model=TripResponse)
def create_trip(trip: TripCreate, db: Session = Depends(database.get_db)):
    with tracer.start_as_current_span("create_single_trip"):
        db_trip = models.Trip(**trip.model_dump())
        db.add(db_trip)
        db.commit()
        db.refresh(db_trip)
        trips_ingested_total.inc()
        return db_trip

@app.post("/trips/batch/", status_code=201)
def create_trips_batch(trips: List[TripCreate], db: Session = Depends(database.get_db)):
    with tracer.start_as_current_span("create_trips_batch") as span:
        span.set_attribute("batch.size", len(trips))
        db_trips = [models.Trip(**trip.model_dump()) for trip in trips]
        db.add_all(db_trips)
        db.commit()
        trips_ingested_total.inc(len(trips))
        trips_batch_size.observe(len(trips))

        # Data quality check — count nulls in key fields (Layer 2: Data)
        null_fields = ["passenger_count", "fare_amount", "trip_distance"]
        for field in null_fields:
            null_count = sum(1 for t in trips if getattr(t, field, None) is None)
            if len(trips) > 0:
                data_null_rate.labels(field=field).set(null_count / len(trips))

        logger.info(f"Batch ingested: {len(trips)} trips")
        return {"message": f"Successfully ingested {len(trips)} trips"}

@app.get("/trips/", response_model=List[TripResponse])
def read_trips(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    with tracer.start_as_current_span("read_trips") as span:
        span.set_attribute("query.skip", skip)
        span.set_attribute("query.limit", limit)
        trips = db.query(models.Trip).offset(skip).limit(limit).all()
        return trips

@app.get("/trips/all/", response_model=List[TripResponse])
def read_all_trips(db: Session = Depends(database.get_db)):
    with tracer.start_as_current_span("read_all_trips"):
        # CAUTION: This could be very slow for large datasets
        trips = db.query(models.Trip).all()
        return trips

# ── New Data Tracking Endpoints ──

@app.post("/notify-new-data/")
def notify_new_data(notification: NewDataNotification):
    """Called by ETL pipeline after ingesting a new batch."""
    global _new_data_available, _new_data_rows_since_last_training, _last_data_notification_time

    _new_data_available = True
    _new_data_rows_since_last_training += notification.rows_ingested
    _last_data_notification_time = datetime.utcnow().isoformat() + "Z"
    new_data_batches_total.inc()

    logger.info(f"New data notification: batch #{notification.batch_number}, "
                f"+{notification.rows_ingested} rows, "
                f"total pending: {_new_data_rows_since_last_training}")
    return {"status": "acknowledged"}


@app.get("/new-data-available/", response_model=NewDataStatus)
def check_new_data():
    """Check if new data has arrived since last training."""
    return NewDataStatus(
        new_data_available=_new_data_available,
        rows_since_last_training=_new_data_rows_since_last_training,
        last_notification_time=_last_data_notification_time
    )


@app.post("/acknowledge-training/")
def acknowledge_training():
    """Called by training pipeline after a successful training run."""
    global _new_data_available, _new_data_rows_since_last_training

    _new_data_available = False
    _new_data_rows_since_last_training = 0
    logger.info("Training acknowledged — new data flag cleared")
    return {"status": "acknowledged"}


@app.get("/trips/count/")
def get_trip_count(db: Session = Depends(database.get_db)):
    """Return total number of trips in the database."""
    with tracer.start_as_current_span("count_trips"):
        count = db.query(func.count(models.Trip.id)).scalar()
        return {"count": count}


@app.get("/trips/sample/")
def get_trip_sample(n: int = 1000, db: Session = Depends(database.get_db)):
    """Return a random sample of N trips for drift detection."""
    with tracer.start_as_current_span("sample_trips") as span:
        span.set_attribute("sample.size", n)
        total = db.query(func.count(models.Trip.id)).scalar()
        if total == 0:
            return []

        # Use random offset-based sampling for simplicity
        trips = db.query(models.Trip).order_by(func.random()).limit(n).all()

        # Convert to dicts
        result = []
        for t in trips:
            result.append({
                "vendor_id": t.vendor_id,
                "passenger_count": t.passenger_count,
                "trip_distance": t.trip_distance,
                "rate_code_id": t.rate_code_id,
                "pu_location_id": t.pu_location_id,
                "do_location_id": t.do_location_id,
                "payment_type": t.payment_type,
                "fare_amount": t.fare_amount,
                "extra": t.extra,
                "mta_tax": t.mta_tax,
                "tip_amount": t.tip_amount,
                "tolls_amount": t.tolls_amount,
                "improvement_surcharge": t.improvement_surcharge,
                "total_amount": t.total_amount,
                "congestion_surcharge": t.congestion_surcharge,
                "airport_fee": t.airport_fee,
                "tpep_pickup_datetime": t.tpep_pickup_datetime.isoformat() if t.tpep_pickup_datetime else None,
                "tpep_dropoff_datetime": t.tpep_dropoff_datetime.isoformat() if t.tpep_dropoff_datetime else None,
            })
        return result


# ── Prometheus metrics endpoint ──
@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
