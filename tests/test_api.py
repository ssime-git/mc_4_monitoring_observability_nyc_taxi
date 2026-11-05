import json
import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services.prediction_api import app as api

VALID = dict(vendor_id=1, passenger_count=1, rate_code_id=1, pu_location_id=48, do_location_id=79,
             pickup_hour=14, pickup_dayofweek=2, pickup_month=1, pickup_weekday=1)


class DummyModel:
    def predict(self, frame):
        return np.array([12.5])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "model", DummyModel())
    monkeypatch.setitem(api.state, "model_version", "7")
    return TestClient(api.app)


def test_health_is_503_without_model_and_200_with_one(monkeypatch):
    monkeypatch.setattr(api, "model", None)
    assert TestClient(api.app).get("/health").status_code == 503
    monkeypatch.setattr(api, "model", DummyModel())
    body = TestClient(api.app).get("/health").json()
    assert body["status"] == "ok" and body["smoke_fare"] == 12.5


def test_health_fails_when_model_cannot_predict(monkeypatch):
    class Broken:
        def predict(self, frame):
            raise RuntimeError("corrupted")
    monkeypatch.setattr(api, "model", Broken())
    assert TestClient(api.app).get("/health").status_code == 503


@pytest.mark.parametrize("field,value", [("pu_location_id", 9999), ("pickup_hour", 99),
                                          ("passenger_count", -1), ("vendor_id", "abc")])
def test_invalid_input_is_rejected_with_422(client, field, value):
    response = client.post("/fare_predict", json={**VALID, field: value})
    assert response.status_code == 422
    assert field in response.json()["fields"]


def test_valid_request_returns_prediction_and_request_id(client):
    response = client.post("/fare_predict", json=VALID, headers={"X-Request-ID": "abc-123"})
    assert response.json() == {"predicted_fare": 12.5}
    assert response.headers["x-request-id"] == "abc-123"


def test_endpoint_label_cardinality_is_bounded(client):
    for i in range(20):
        client.get(f"/scan/{i}")
    endpoints = {s.labels["endpoint"] for m in api.http_requests_total.collect() for s in m.samples}
    assert "unmatched" in endpoints
    assert not any(e.startswith("/scan") for e in endpoints)


def test_logs_are_json_with_correlation_and_model_version_and_no_payload(client, capsys):
    stream = []
    class Capture(logging.Handler):
        def emit(self, record):
            stream.append(api.JSONFormatter().format(record))
    handler = Capture()
    api.logger.addHandler(handler)
    try:
        client.post("/fare_predict", json=VALID, headers={"X-Request-ID": "req-42"})
    finally:
        api.logger.removeHandler(handler)
    line = json.loads(stream[-1])
    assert {"timestamp", "level", "request_id", "model_version"} <= line.keys()
    assert line["request_id"] == "req-42" and line["model_version"] == "7"
    assert "pu_location_id" not in stream[-1]


def test_chaos_is_forbidden_unless_enabled(client, monkeypatch):
    monkeypatch.setattr(api, "CHAOS_ENABLED", False)
    assert client.put("/admin/chaos", json={"latency_ms": 500}).status_code == 403
    monkeypatch.setattr(api, "CHAOS_ENABLED", True)
    assert client.put("/admin/chaos", json={"latency_ms": 0}).status_code == 200


def test_feedback_updates_online_rmse_counters(client):
    r = client.post("/fare_predict", json=VALID, headers={"X-Request-ID": "fb-1"})
    assert r.status_code == 200
    before = api.prediction_squared_error_total._value.get()
    assert client.post("/feedback", json={"request_id": "fb-1", "actual_fare": 14.5}).json() == {"error": -2.0}
    assert api.prediction_squared_error_total._value.get() - before == 4.0
    assert client.post("/feedback", json={"request_id": "fb-1", "actual_fare": 14.5}).status_code == 404
    assert client.post("/feedback", json={"request_id": "x", "actual_fare": -3}).status_code == 422


def test_api_bounds_match_the_pipeline_schema():
    """One data contract: what training accepts, serving accepts (vendor 1-7, rate code 1-99, zones 1-265)."""
    from services.pipeline import taxi_validation
    for column, field in [('vendor_id', 'vendor_id'), ('rate_code_id', 'rate_code_id'),
                          ('pu_location_id', 'pu_location_id'), ('passenger_count', 'passenger_count')]:
        check = taxi_validation.SCHEMA.columns[column].checks[0].statistics
        model_field = api.RequestData.model_fields[field]
        bounds = {type(m).__name__: m for m in model_field.metadata}
        assert bounds['Ge'].ge == check['min_value'] and bounds['Le'].le == check['max_value'], column


def test_request_without_model_returns_503_without_loading_in_the_request_path(monkeypatch):
    monkeypatch.setattr(api, "model", None)
    called = []
    monkeypatch.setattr(api, "load_champion_model", lambda: called.append(1) or True)
    response = TestClient(api.app).post("/fare_predict", json=VALID)
    assert response.status_code == 503 and not called
