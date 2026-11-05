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
