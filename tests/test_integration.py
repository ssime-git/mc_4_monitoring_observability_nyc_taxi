"""Integration tests: need the stack running (`make up`). Skipped automatically otherwise.

Scripted alert scenarios are slower (minutes): run them with RUN_SCENARIOS=1 or `make demo-all`.
"""
import os
import time

import pytest
import requests

PROM, API, AM, GRAFANA = 'http://localhost:9090', 'http://localhost:8000', 'http://localhost:9093', 'http://localhost:3000'
VALID = dict(vendor_id=1, passenger_count=1, rate_code_id=1, pu_location_id=48, do_location_id=79,
             pickup_hour=14, pickup_dayofweek=2, pickup_month=1, pickup_weekday=1)


def stack_up():
    try:
        return requests.get(f'{API}/health', timeout=2).status_code == 200 and requests.get(f'{PROM}/-/ready', timeout=2).ok
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(not stack_up(), reason='stack not running (make up) or model not ready')


def query(expr):
    return requests.get(f'{PROM}/api/v1/query', params={'query': expr}, timeout=5).json()['data']['result']


def eventually(fn, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(2)
    return fn()


def test_traffic_shows_up_in_prometheus_with_red_metrics():
    before = float((query('sum(http_requests_total{endpoint="/fare_predict",status="200"})') or [{'value': [0, 0]}])[0]['value'][1])
    for _ in range(30):
        assert requests.post(f'{API}/fare_predict', json=VALID, timeout=10).status_code == 200
    requests.post(f'{API}/fare_predict', json={**VALID, 'pu_location_id': 9999}, timeout=10)

    def counted():
        now = float(query('sum(http_requests_total{endpoint="/fare_predict",status="200"})')[0]['value'][1])
        return now - before >= 30
    assert eventually(counted)
    assert query('http_requests_total{status="422"}')
    assert query('histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))')
    assert query('model_version') and query('model_loaded == 1')


def test_endpoint_label_has_no_unbounded_values():
    requests.get(f'{API}/some/random/path/12345', timeout=5)
    endpoints = lambda: {r['metric']['endpoint'] for r in query('http_requests_total')}
    assert eventually(lambda: 'unmatched' in endpoints())
    assert not any('12345' in e for e in endpoints())


def test_health_checks_the_model_and_logs_carry_correlation():
    body = requests.get(f'{API}/health', timeout=5).json()
    assert body['status'] == 'ok' and body['model_version'] != 'none' and body['smoke_fare'] > 0
    response = requests.post(f'{API}/fare_predict', json=VALID, headers={'X-Request-ID': 'integration-test-1'}, timeout=10)
    assert response.headers['x-request-id'] == 'integration-test-1'


def test_alert_rules_loaded_and_alertmanager_connected():
    names = {r['name'] for g in requests.get(f'{PROM}/api/v1/rules', timeout=5).json()['data']['groups'] for r in g['rules']}
    assert {'HighLatencyP99', 'HighErrorRate', 'DataDriftDetected', 'BusinessThroughputLow', 'ServiceDown'} <= names
    assert requests.get(f'{PROM}/api/v1/alertmanagers', timeout=5).json()['data']['activeAlertmanagers']
    assert requests.get(f'{AM}/-/ready', timeout=5).ok


def test_dashboards_are_provisioned_from_files():
    for uid in ['l0-status', 'l1-executive', 'l2-system-overview', 'l3-deep-dive']:
        response = requests.get(f'{GRAFANA}/api/dashboards/uid/{uid}', timeout=5)
        assert response.status_code == 200, uid
        assert response.json()['meta']['provisioned'] is True


@pytest.mark.skipif(not os.getenv('RUN_SCENARIOS'), reason='slow: set RUN_SCENARIOS=1 (or use make demo-all)')
def test_alert_scenarios_end_to_end():
    from scripts import demo_scenarios as s
    s.normal()
    s.latency()
    s.outage()
