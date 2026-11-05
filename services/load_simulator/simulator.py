"""
Load Simulator — Generates realistic NYC taxi prediction traffic for monitoring demos.

Sends a mix of valid requests (~90%) and deliberately malformed requests (~10%)
to produce meaningful data across all dashboard panels (rate, errors, latency).
"""
import requests
import random
import time
import os
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
            "service": "load_simulator",
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("load_simulator")

# Configuration
PREDICTION_API_URL = os.getenv("PREDICTION_API_URL", "http://prediction_api:8000")
REQUESTS_PER_SECOND = float(os.getenv("REQUESTS_PER_SECOND", "2"))
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.10"))  # 10% malformed requests

# Realistic NYC location IDs (Manhattan, airports, etc.)
POPULAR_PICKUP_LOCATIONS = [
    48, 79, 100, 107, 113, 114, 125, 137, 140, 141,
    142, 143, 144, 148, 151, 158, 161, 162, 163, 164,
    170, 186, 202, 209, 211, 224, 229, 230, 231, 232,
    233, 234, 236, 237, 238, 239, 246, 249, 261, 262,
    263,  # JFK
    132,  # LGA
]

POPULAR_DROPOFF_LOCATIONS = POPULAR_PICKUP_LOCATIONS + [1, 7, 13, 24, 33, 42, 56, 65, 74, 87]


def generate_valid_request():
    """Generate a realistic taxi fare prediction request."""
    hour = random.choices(
        range(24),
        weights=[2,1,1,1,1,2,4,8,10,9,7,6,6,7,8,9,10,10,9,8,7,5,4,3],
        k=1
    )[0]
    day_of_week = random.randint(0, 6)
    month = random.randint(1, 12)

    return {
        "vendor_id": random.choice([1, 2]),
        "passenger_count": float(random.choices([1,2,3,4,5,6], weights=[60,15,10,8,5,2], k=1)[0]),
        "rate_code_id": float(random.choices([1,2,3,4,5], weights=[85,5,3,2,5], k=1)[0]),
        "pickup_longitude": round(random.uniform(-74.05, -73.75), 6),
        "pickup_latitude": round(random.uniform(40.63, 40.85), 6),
        "dropoff_longitude": round(random.uniform(-74.05, -73.75), 6),
        "dropoff_latitude": round(random.uniform(40.63, 40.85), 6),
        "pu_location_id": random.choice(POPULAR_PICKUP_LOCATIONS),
        "do_location_id": random.choice(POPULAR_DROPOFF_LOCATIONS),
        "pickup_hour": hour,
        "pickup_dayofweek": day_of_week,
        "pickup_month": month,
        "pickup_weekday": 1 if day_of_week < 5 else 0,
    }


def generate_malformed_request():
    """Generate a deliberately malformed request to trigger errors."""
    error_type = random.choice(["missing_field", "bad_type"])

    if error_type == "missing_field":
        req = generate_valid_request()
        # Remove a required field
        field_to_remove = random.choice(["vendor_id", "pu_location_id", "pickup_hour"])
        del req[field_to_remove]
        return req
    elif error_type == "bad_type":
        req = generate_valid_request()
        req["vendor_id"] = "not_a_number"
        return req
    else:  # extreme_values
        req = generate_valid_request()
        req["passenger_count"] = 999.0
        req["pickup_hour"] = 99
        return req


def send_request(payload):
    """Send a prediction request and return success/failure."""
    try:
        response = requests.post(
            f"{PREDICTION_API_URL}/fare_predict",
            json=payload,
            timeout=10
        )
        return response.status_code, response.elapsed.total_seconds()
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None, 0


# Control plane used by the authenticated Streamlit portal (internal Docker network).
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel, Field

lock = threading.Lock()
stop = threading.Event()
state = dict(running=True, requests_per_second=REQUESTS_PER_SECOND, error_rate=ERROR_RATE,
             sent=0, succeeded=0, rejected=0, failed=0, last_status=None)


class Settings(BaseModel):
    running: bool
    requests_per_second: float = Field(ge=0.2, le=20)
    error_rate: float = Field(ge=0, le=1)


def traffic_loop():
    while not stop.is_set():
        with lock:
            settings = state.copy()
        if not settings['running']:
            stop.wait(0.2)
            continue
        payload = generate_malformed_request() if random.random() < settings['error_rate'] else generate_valid_request()
        status, latency = send_request(payload)
        with lock:
            state['sent'] += 1
            state['last_status'] = status
            key = 'succeeded' if status and 200 <= status < 300 else ('rejected' if status == 422 else 'failed')
            state[key] += 1
        stop.wait(random.uniform(.8, 1.2) / settings['requests_per_second'])


@asynccontextmanager
async def lifespan(app):
    stop.clear()
    thread = threading.Thread(target=traffic_loop, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=12)


app = FastAPI(lifespan=lifespan)


@app.get('/status')
def status():
    with lock:
        return state.copy()


@app.put('/settings')
def settings(value: Settings):
    with lock:
        state.update(value.model_dump())
        return state.copy()
