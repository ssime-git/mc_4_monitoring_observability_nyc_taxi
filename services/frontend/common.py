"""Shared helpers for the demo pages: service clients, live queries, charts and widgets."""
import json
import os
import time
from urllib.parse import urlencode

import altair as alt
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

API = os.getenv('PREDICTION_API_URL', 'http://prediction_api:8000')
PROM = os.getenv('PROMETHEUS_API_URL', 'http://prometheus:9090')
ALERTMANAGER = os.getenv('ALERTMANAGER_API_URL', 'http://alertmanager:9093')
WEBHOOK = os.getenv('ALERT_WEBHOOK_URL', 'http://alert_webhook:8020')
SIM = os.getenv('SIMULATOR_API_URL', 'http://load_simulator:8010')
PREFECT = os.getenv('PREFECT_API_URL', 'http://prefect_server:4200/api')
CONTROLLER = os.getenv('CONTROLLER_API_URL', 'http://controller:8030')
LOKI = os.getenv('LOKI_API_URL', 'http://loki:3100')
TEMPO = os.getenv('TEMPO_API_URL', 'http://tempo:3200')
GRAFANA = os.getenv('GRAFANA_UI_URL', 'http://localhost:3000').rstrip('/')

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


def chart(frame, title, unit='', threshold=None, threshold_label='alert threshold', height=230):
    """Line chart with an optional red threshold rule: the audience sees the metric cross the alert line."""
    if frame.empty:
        st.caption(f'{title}: waiting for data…')
        return
    base = alt.Chart(frame).mark_line(strokeWidth=2).encode(
        x=alt.X('time:T', title=None),
        y=alt.Y('value:Q', title=unit or None, scale=alt.Scale(zero=True)),
        color=alt.Color('series:N', title=None, legend=alt.Legend(orient='bottom')),
        tooltip=['series', alt.Tooltip('value:Q', format=',.3f'), 'time:T'])
    layers = [base]
    if threshold is not None:
        rule = alt.Chart(pd.DataFrame({'y': [threshold], 'label': [threshold_label]})).mark_rule(
            color='#d62728', strokeDash=[6, 4], strokeWidth=2).encode(y='y:Q')
        text = alt.Chart(pd.DataFrame({'y': [threshold], 'label': [f'{threshold_label} ({threshold:g})']})).mark_text(
            align='left', dx=4, dy=-6, color='#d62728').encode(y='y:Q', text='label:N', x=alt.value(4))
        layers += [rule, text]
    st.markdown(f'**{title}**')
    st.altair_chart(alt.layer(*layers).properties(height=height), width='stretch')


def metric(col, label, query, unit='', fmt='{:,.1f}', help=None, scale=1.0, empty='–'):
    value = safe(lambda: prom(query))
    col.metric(label, empty if value is None else fmt.format(value * scale) + unit, help=help)


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


def alert_chips(names):
    states = safe(alert_states, {})
    cols = st.columns(len(names))
    for col, name in zip(cols, names):
        state = states.get(name, 'inactive')
        icon = {'firing': '🔴', 'pending': '🟡', 'inactive': '🟢'}[state]
        col.markdown(f'{icon} **{name}**  \n{state}')


def notification_inbox(limit=6, names=None):
    """What the on-call person would receive (the demo webhook stands in for Slack/PagerDuty)."""
    items = safe(lambda: call('GET', f'{WEBHOOK}/alerts'), [])
    if names:
        items = [i for i in items if i['alertname'] in names]
    if not items:
        st.caption('📭 No notification received yet.')
        return
    for item in items[:limit]:
        icon = '🔥' if item['status'] == 'firing' else '✅'
        st.markdown(f"{icon} `{item['received_at'][11:19]}` **{item['alertname']}** — {item['status']}  \n"
                    f"<small>{item['summary']}. **Action:** {item['action']}</small>", unsafe_allow_html=True)


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


def logs_panel(limit=8, only_slow=False, levels=None):
    frame = safe(lambda: recent_logs(100 if (only_slow or levels) else limit), pd.DataFrame())
    if frame.empty:
        st.caption('No log line yet.')
        return
    if only_slow:
        frame = frame[frame['duration_ms'].fillna(0) > 500]
    if levels:
        frame = frame[frame['level'].isin(levels)]
    st.dataframe(frame.head(limit), hide_index=True, width='stretch')


def slowest_traces(limit=5, minutes=10):
    end = int(time.time())
    data = call('GET', f'{TEMPO}/api/search', params={
        'q': '{ name = "fare_prediction" }', 'limit': 200, 'start': end - minutes * 60, 'end': end})['traces']
    rows = [{'trace_id': t['traceID'], 'duration_ms': t.get('durationMs', 0),
             'started': pd.to_datetime(int(t['startTimeUnixNano']), unit='ns', utc=True).strftime('%H:%M:%S')} for t in data]
    return pd.DataFrame(rows).sort_values('duration_ms', ascending=False).head(limit) if rows else pd.DataFrame()


def traces_panel(limit=5):
    frame = safe(slowest_traces, pd.DataFrame())
    if frame.empty:
        st.caption('No trace yet.')
    else:
        st.dataframe(frame, hide_index=True, width='stretch')


def grafana_panel(dashboard_uid, slug, panel_id, height=260, title=None):
    """A single live Grafana panel embedded in the page (browser-side iframe)."""
    query = urlencode({'orgId': 1, 'panelId': panel_id, 'refresh': '5s', 'from': 'now-15m', 'to': 'now'})
    st.iframe(f'{GRAFANA}/d-solo/{dashboard_uid}/{slug}?{query}', height=height)


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
        done.append(f"{'✅' if safe(fn) is not None else '⚠️'} {label}")
    return done


def start_pipeline(**parameters):
    deployment = call('GET', f'{PREFECT}/deployments/name/nyc-taxi-demo/demo')
    run = call('POST', f"{PREFECT}/deployments/{deployment['id']}/create_flow_run", json={'parameters': parameters})
    return run['id']


def pipeline_state(run_id):
    run = call('GET', f'{PREFECT}/flow_runs/{run_id}')
    return run['name'], run['state_type']


# ---- page furniture -------------------------------------------------------------------------------
def scenario_header(number, icon, title, story, watch):
    st.title(f'{icon} {title}')
    if number:
        st.caption(f'SCENARIO {number} · follow the four steps: story → trigger → monitoring → observability')
    st.markdown(f'> {story}')
    st.info(f'**What to watch:** {watch}')


def section(label):
    st.markdown(f'#### {label}')


def pillars(monitoring, observability):
    """Two-column explanation: metrics tell you THAT something is wrong; logs/traces tell you WHY."""
    a, b = st.columns(2)
    a.markdown('##### 📈 Monitoring — *is something wrong?*')
    a.markdown(monitoring)
    b.markdown('##### 🔎 Observability — *why is it wrong?*')
    b.markdown(observability)
