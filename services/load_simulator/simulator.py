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
    error_type = random.choice(["missing_field", "bad_type", "extreme_values"])

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


def main():
    logger.info(f"Starting load simulator: {REQUESTS_PER_SECOND} req/s, {ERROR_RATE*100:.0f}% error rate")
    logger.info(f"Target: {PREDICTION_API_URL}/fare_predict")

    # Wait for prediction API to be ready
    logger.info("Waiting for Prediction API to be ready...")
    for _ in range(60):
        try:
            resp = requests.get(f"{PREDICTION_API_URL}/health", timeout=5)
            health = resp.json()
            if health.get("model_loaded"):
                logger.info("Prediction API is ready with model loaded!")
                break
            else:
                logger.info("API is up but model not loaded yet...")
        except:
            pass
        time.sleep(10)

    delay = 1.0 / REQUESTS_PER_SECOND
    request_count = 0
    success_count = 0
    error_count = 0

    while True:
        try:
            # Decide: valid or malformed request
            if random.random() < ERROR_RATE:
                payload = generate_malformed_request()
                is_error_request = True
            else:
                payload = generate_valid_request()
                is_error_request = False

            status, latency = send_request(payload)
            request_count += 1

            if status and 200 <= status < 300:
                success_count += 1
            else:
                error_count += 1

            # Log periodic summary
            if request_count % 50 == 0:
                logger.info(
                    f"Stats: {request_count} total, {success_count} success, "
                    f"{error_count} errors ({error_count/max(request_count,1)*100:.1f}%)"
                )

            # Add jitter for more realistic traffic patterns
            jitter = random.uniform(0.5, 1.5)
            time.sleep(delay * jitter)

        except KeyboardInterrupt:
            logger.info("Shutting down load simulator")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
