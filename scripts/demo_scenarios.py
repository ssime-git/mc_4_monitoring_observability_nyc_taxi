#!/usr/bin/env python3
"""Scripted demo scenarios. Each one injects a fault, waits for the alert, prints the measured delay, cleans up.

Usage: python scripts/demo_scenarios.py {normal|latency|drift|outage|performance|business|all}
Requires the stack to be running (`make up`). The `outage` scenario needs the docker CLI.
"""
import json
import subprocess
import sys
import time

import requests

PROM, AM, HOOK = 'http://localhost:9090', 'http://localhost:9093', 'http://localhost:8020'
API, PREFECT = 'http://localhost:8000', 'http://localhost:4200/api'


def firing():
    alerts = requests.get(f'{PROM}/api/v1/alerts', timeout=5).json()['data']['alerts']
    return {a['labels']['alertname'] for a in alerts if a['state'] == 'firing'}


def pending_or_firing(name):
    alerts = requests.get(f'{PROM}/api/v1/alerts', timeout=5).json()['data']['alerts']
    return {a['state'] for a in alerts if a['labels']['alertname'] == name}


def in_alertmanager(name):
    return any(a['labels']['alertname'] == name and a['status']['state'] == 'active'
               for a in requests.get(f'{AM}/api/v2/alerts', timeout=5).json())


def webhook_seen(name, status='firing'):
    return any(n['alertname'] == name and n['status'] == status
               for n in requests.get(f'{HOOK}/alerts', timeout=5).json())


def sim(settings):
    code = ('import requests,sys,json;'
            f"r=requests.put('http://load_simulator:8010/settings',json={settings!r},timeout=5);print(r.status_code)")
    out = subprocess.run(['docker', 'compose', 'exec', '-T', 'frontend', 'python', '-c', code], capture_output=True, text=True)
    assert out.stdout.strip() == '200', out.stdout + out.stderr


NORMAL = dict(running=True, requests_per_second=2, error_rate=0.1, label_bias=0.0)


def chaos(ms):
    requests.put(f'{API}/admin/chaos', json={'latency_ms': ms}, timeout=5).raise_for_status()


def run_flow(params):
    deployment = requests.get(f'{PREFECT}/deployments/name/nyc-taxi-demo/demo', timeout=10).json()
    run = requests.post(f"{PREFECT}/deployments/{deployment['id']}/create_flow_run", json={'parameters': params}, timeout=10).json()
    while True:
        state = requests.get(f"{PREFECT}/flow_runs/{run['id']}", timeout=10).json()['state_type']
        if state in ('COMPLETED', 'FAILED', 'CRASHED', 'CANCELLED'):
            return state
        time.sleep(5)


def wait(predicate, timeout, label):
    start = time.time()
    while time.time() - start < timeout:
        try:
            if predicate():
                return round(time.time() - start, 1)
        except requests.RequestException:
            pass
        time.sleep(2)
    raise AssertionError(f'timeout ({timeout}s) waiting for {label}')


def settle():
    """Return to a clean baseline before a scenario."""
    sim(NORMAL)
    chaos(0)
    wait(lambda: not firing(), 300, 'all alerts to clear before starting')
    time.sleep(70)  # let the 1-minute rate windows forget the previous scenario
    assert not firing(), f'alerts re-fired during settle: {firing()}'
    requests.delete(f'{HOOK}/alerts', timeout=5)


def expect_alert(name, inject, undo, timeout=240):
    settle()
    print(f'\n▶ {name}: injecting fault')
    t0 = time.time()
    inject()
    result = {}
    result['prometheus_firing_s'] = wait(lambda: name in firing(), timeout, f'{name} firing in Prometheus')
    result['alertmanager_s'] = round(wait(lambda: in_alertmanager(name), 60, f'{name} in Alertmanager') + result['prometheus_firing_s'], 1)
    result['webhook_notification_s'] = round(wait(lambda: webhook_seen(name), 90, f'{name} webhook notification') + result['alertmanager_s'], 1)
    print(f'  ✔ {name} -> {json.dumps(result)} (seconds since injection; total {time.time() - t0:.0f}s)')
    undo()
    result['resolved_s'] = round(wait(lambda: name not in firing(), 400, f'{name} to resolve'), 1)
    print(f'  ✔ resolved {result["resolved_s"]}s after the fix')
    return result


def normal():
    settle()
    print('\n▶ normal traffic: watching 90 s, expecting zero firing alert')
    deadline = time.time() + 90
    while time.time() < deadline:
        assert not firing(), f'unexpected alerts under normal traffic: {firing()}'
        time.sleep(5)
    print('  ✔ no alert fired under normal traffic (2 req/s, 10% invalid inputs => 422, not errors)')


def latency():
    return expect_alert('HighLatencyP99', lambda: chaos(1500), lambda: chaos(0))


def drift():
    def inject():
        state = run_flow({'simulate_drift': True})
        assert state == 'COMPLETED', f'flow ended {state}'
    return expect_alert('DataDriftDetected', inject, lambda: run_flow({}))


def outage():
    return expect_alert('ServiceDown',
                        lambda: subprocess.run(['docker', 'compose', 'stop', 'prediction_api'], check=True, capture_output=True),
                        lambda: subprocess.run(['docker', 'compose', 'start', 'prediction_api'], check=True, capture_output=True))


def performance():
    return expect_alert('OnlineModelErrorHigh', lambda: sim({**NORMAL, 'label_bias': 1.0}), lambda: sim(NORMAL), timeout=300)


def business():
    return expect_alert('BusinessThroughputLow', lambda: sim({**NORMAL, 'running': False}), lambda: sim(NORMAL))


SCENARIOS = dict(normal=normal, latency=latency, drift=drift, outage=outage, performance=performance, business=business)

if __name__ == '__main__':
    choice = sys.argv[1] if len(sys.argv) > 1 else 'all'
    for name in (SCENARIOS if choice == 'all' else [choice]):
        SCENARIOS[name]()
    print('\nAll requested scenarios passed.')
