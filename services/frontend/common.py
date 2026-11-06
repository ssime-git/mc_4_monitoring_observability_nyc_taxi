"""Data access for the demo pages: service clients, live queries and controls. Rendering lives in ui.py."""
import json
import os
import re
import time
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

API = os.getenv('PREDICTION_API_URL', 'http://prediction_api:8000')
PROM = os.getenv('PROMETHEUS_API_URL', 'http://prometheus:9090')
ALERTMANAGER = os.getenv('ALERTMANAGER_API_URL', 'http://alertmanager:9093')
WEBHOOK = os.getenv('ALERT_WEBHOOK_URL', 'http://alert_webhook:8020')
SIM = os.getenv('SIMULATOR_API_URL', 'http://load_simulator:8010')
PREFECT = os.getenv('PREFECT_API_URL', 'http://prefect_server:4200/api')
CONTROLLER = os.getenv('CONTROLLER_API_URL', 'http://controller:8030')
LOKI = os.getenv('LOKI_API_URL', 'http://loki:3100')
TEMPO = os.getenv('TEMPO_API_URL', 'http://tempo:3200')
PUBLIC_HOST = os.getenv('PUBLIC_HOST', '').strip()

PAYLOAD = dict(vendor_id=1, passenger_count=1., rate_code_id=1., pu_location_id=48, do_location_id=79,
               pickup_hour=14, pickup_dayofweek=2, pickup_month=1, pickup_weekday=1)
NORMAL_TRAFFIC = dict(running=True, requests_per_second=2, error_rate=0.1, label_bias=0.0)

# PromQL used by several pages (kept in one place so every page tells the same story).
P99 = ('histogram_quantile({q}, sum(rate(http_request_duration_seconds_bucket'
       '{{job="prediction_api",endpoint="/fare_predict"}}[1m])) by (le))')
ONLINE_RMSE = ('sqrt(sum(rate(prediction_squared_error_dollars2_total[2m])) '
               '/ sum(rate(labeled_predictions_total[2m])))')


def call(method, url, **kwargs):
    response = requests.request(method, url, timeout=kwargs.pop('timeout', 10), **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def safe(fn, default=None):
    """Run a lookup; the demo must keep rendering when one backend is down (that is often the point)."""
    try:
        return fn()
    except (requests.RequestException, KeyError, ValueError, IndexError):
        return default


def prom(query):
    data = call('GET', f'{PROM}/api/v1/query', params={'query': query})['data']
    result = data['result']
    if not result:
        return None
    number = float(result[1] if data['resultType'] == 'scalar' else result[0]['value'][1])
    return number if number == number else None


def prom_range(queries, minutes=10, step=5):
    """queries: {label: promql}. Returns a long DataFrame (time, series, value)."""
    end = time.time()
    rows = []
    for label, query in queries.items():
        for series in call('GET', f'{PROM}/api/v1/query_range',
                           params={'query': query, 'start': end - minutes * 60, 'end': end, 'step': step})['data']['result']:
            name = label if len(queries) > 1 or not series['metric'] else label.format(**series['metric'])
            for t, v in series['values']:
                if v not in ('NaN', '+Inf', '-Inf'):
                    rows.append({'time': pd.to_datetime(t, unit='s', utc=True), 'series': name, 'value': float(v)})
    return pd.DataFrame(rows, columns=['time', 'series', 'value'])


def value(query, fmt='{:,.1f}', scale=1.0, empty='–'):
    """Latest value of a query, formatted for a tile."""
    number = safe(lambda: prom(query))
    return (empty, None) if number is None else (fmt.format(number * scale), number * scale)


# ---- alerts -----------------------------------------------------------------------------------
def alert_states():
    """{alertname: 'pending' | 'firing'} for what Prometheus currently evaluates as active."""
    alerts = call('GET', f'{PROM}/api/v1/alerts')['data']['alerts']
    states = {}
    for a in alerts:
        name = a['labels']['alertname']
        if states.get(name) != 'firing':
            states[name] = a['state']
    return states


def notifications(names=None):
    """What the on-call person would receive (the demo webhook stands in for Slack/PagerDuty)."""
    items = safe(lambda: call('GET', f'{WEBHOOK}/alerts'), [])
    return [i for i in items if i['alertname'] in names] if names else items


# ---- observability: logs and traces ---------------------------------------------------------------
def recent_logs(limit=8, contains='request_id', minutes=10):
    end = int(time.time() * 1e9)
    data = call('GET', f'{LOKI}/loki/api/v1/query_range', params={
        'query': f'{{service="prediction_api"}} |= "{contains}"', 'limit': 100, 'direction': 'backward',
        'start': end - minutes * 60 * int(1e9), 'end': end})['data']['result']
    rows = []
    for stream in data:
        for ts, line in stream['values']:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            rows.append({'time': entry['timestamp'][11:23], 'level': entry['level'], 'message': entry['message'],
                         'duration_ms': entry.get('duration_ms'), 'model_version': entry.get('model_version'),
                         'request_id': entry['request_id'], 'trace_id': entry.get('trace_id')})
    return pd.DataFrame(rows).sort_values('time', ascending=False).head(limit) if rows else pd.DataFrame()


def filtered_logs(limit=8, only_slow=False, levels=None, contains=None):
    frame = safe(lambda: recent_logs(100 if (only_slow or levels or contains) else limit), pd.DataFrame())
    if frame.empty:
        return frame
    if only_slow:
        frame = frame[frame['duration_ms'].fillna(0) > 500]
    if levels:
        frame = frame[frame['level'].isin(levels)]
    if contains:
        frame = frame[frame['message'].str.contains(contains)]
    return frame.head(limit)


def slowest_traces(limit=5, minutes=10):
    """Slow traces first; Tempo caps a search, so asking for all traces and sorting would miss them."""
    end = int(time.time())

    def search(query, window):
        return call('GET', f'{TEMPO}/api/search', params={'q': query, 'limit': 100, 'start': end - window, 'end': end})['traces']
    found = search('{ name = "fare_prediction" && duration > 500ms }', minutes * 60) or search('{ name = "fare_prediction" }', 120)
    rows = [{'trace_id': t['traceID'], 'duration_ms': t.get('durationMs', 0),
             'started': pd.to_datetime(int(t['startTimeUnixNano']), unit='ns', utc=True).strftime('%H:%M:%S')} for t in found]
    return pd.DataFrame(rows).sort_values('duration_ms', ascending=False).head(limit) if rows else pd.DataFrame()


def public_url(port):
    """Address of a tool for the viewer's browser: same host as the portal, so localhost and a VM IP both work."""
    host = PUBLIC_HOST or re.sub(r':\d+$', '', st.context.headers.get('Host') or '') or 'localhost'
    return f'http://{host}:{port}'


def grafana_url():
    return public_url(3000)


def grafana_panel_url(dashboard_uid, slug, panel_id, theme):
    query = urlencode({'orgId': 1, 'panelId': panel_id, 'refresh': '5s', 'from': 'now-15m', 'to': 'now', 'theme': theme})
    return f'{grafana_url()}/d-solo/{dashboard_uid}/{slug}?{query}'


# ---- controls -------------------------------------------------------------------------------------
def simulator(**settings):
    body = {**NORMAL_TRAFFIC, **settings}
    return call('PUT', f'{SIM}/settings', json=body)


def set_latency(ms):
    return call('PUT', f'{API}/admin/chaos', json={'latency_ms': int(ms)})


def reset_everything():
    """Back to a healthy baseline: normal traffic, no injected latency, API running."""
    done = []
    for label, fn in [('API started', lambda: call('POST', f'{CONTROLLER}/services/prediction_api/start')),
                      ('latency removed', lambda: set_latency(0)),
                      ('traffic back to normal', lambda: simulator())]:
        done.append((label, safe(fn) is not None))
    return done


def start_pipeline(**parameters):
    deployment = call('GET', f'{PREFECT}/deployments/name/nyc-taxi-demo/demo')
    run = call('POST', f"{PREFECT}/deployments/{deployment['id']}/create_flow_run", json={'parameters': parameters})
    return run['id']


def pipeline_state(run_id):
    run = call('GET', f'{PREFECT}/flow_runs/{run_id}')
    return run['name'], run['state_type']


# ---- data quarantine (read-only queries on the table written by the pipeline) -------------------------
def quarantine(limit=20):
    """(rows_by_reason, sample_rows) from PostgreSQL `quarantine_trips`; empty frames if nothing was quarantined yet."""
    import psycopg2
    conn = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=5)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('quarantine_trips')")
            if cur.fetchone()[0] is None:
                return pd.DataFrame(), pd.DataFrame()
        by_reason = pd.read_sql("SELECT reason, count(*) AS rows FROM quarantine_trips GROUP BY reason ORDER BY rows DESC", conn)
        sample = pd.read_sql(f"SELECT reason, pu_location_id, do_location_id, passenger_count, total_fare, pickup_hour "
                             f"FROM quarantine_trips ORDER BY random() LIMIT {int(limit)}", conn)
        return by_reason, sample
    finally:
        conn.close()
