"""Deep-dive pages: live alerts, logs/traces explorer, embedded tool UIs and Evidently reports."""
import json
import os
from urllib.parse import urlsplit

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

import common as c
from metric_guides import render_guide

URLS = {
    'Prefect': os.getenv('PREFECT_UI_URL', 'http://localhost:4200'),
    'Prometheus': os.getenv('PROMETHEUS_UI_URL', 'http://localhost:9090'),
    'Alertmanager': os.getenv('ALERTMANAGER_UI_URL', 'http://localhost:9093'),
    'MLflow': os.getenv('MLFLOW_UI_URL', 'http://localhost:5000'),
}
DASHBOARDS = {
    'L0 · Is it OK?': 'l0-status', 'L1 · Executive': 'l1-executive',
    'L2 · System (RED)': 'l2-system-overview', 'L3 · Deep dive': 'l3-deep-dive',
}


def embed(title, url):
    st.title(title)
    render_guide(title)
    parsed = urlsplit(url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        st.error('The configured URL must be an HTTP(S) address reachable from the browser.')
        return
    st.iframe(url, height=900)
    st.link_button('Open in a separate tab', url)


def grafana():
    st.title('Grafana dashboards')
    render_guide('Grafana')
    choice = st.radio('Dashboard', list(DASHBOARDS), horizontal=True)
    embed_url = f'{c.GRAFANA}/d/{DASHBOARDS[choice]}?orgId=1&kiosk'
    st.iframe(embed_url, height=900)
    st.link_button('Open in a separate tab', embed_url)


def prefect():
    embed('Prefect', URLS['Prefect'])


def prometheus():
    embed('Prometheus', URLS['Prometheus'])


def mlflow_page():
    embed('MLflow', URLS['MLflow'])


def alertmanager():
    embed('Alertmanager', URLS['Alertmanager'])


@st.fragment(run_every='3s')
def live_alerts():
    rules = c.safe(lambda: [r for g in c.call('GET', f'{c.PROM}/api/v1/rules')['data']['groups'] for r in g['rules']], [])
    if rules:
        states = c.safe(c.alert_states, {})
        table = pd.DataFrame([{
            'state': {'firing': '🔴 firing', 'pending': '🟡 pending'}.get(states.get(r['name']), '🟢 inactive'),
            'alert': r['name'], 'layer': r['labels'].get('layer'), 'severity': r['labels'].get('severity'),
            'for': f"{int(r['duration'])} s", 'action': r['annotations'].get('action')} for r in rules if r['type'] == 'alerting'])
        st.dataframe(table, hide_index=True, width='stretch')
    else:
        st.warning('Prometheus rules are not reachable.')
    st.subheader('Notifications received by the demo webhook')
    st.caption('Alertmanager → `alert_webhook` (stands in for Slack / PagerDuty). In production, change the receiver in '
               '`services/alertmanager/alertmanager.yml`.')
    c.notification_inbox(10)


def alerts():
    st.title('🚨 Alerts & notifications')
    st.markdown('Every alert has a justified threshold, a duration (`for:`), a severity, an **action** and a runbook '
                '(`docs/RUNBOOKS.md`). One alert per layer: application, platform, model, business.')
    live_alerts()
    if st.button('Clear notification inbox'):
        c.safe(lambda: c.call('DELETE', f'{c.WEBHOOK}/alerts'))
    st.link_button('Open Alertmanager', URLS['Alertmanager'])


@st.fragment(run_every='4s')
def live_explorer(level, only_slow):
    left, right = st.columns([3, 2])
    with left:
        st.subheader('Logs (Loki)')
        c.logs_panel(25, only_slow=only_slow, levels=None if level == 'ALL' else [level])
    with right:
        st.subheader('Slowest traces (Tempo)')
        c.traces_panel(10)


def logs_traces():
    st.title('🔎 Logs & traces')
    st.markdown('Structured JSON logs carry `request_id`, `trace_id` and `model_version`. '
                'Take a `request_id` from a client response header, find the log line, then the trace.')
    level = st.radio('Level', ['ALL', 'INFO', 'WARNING', 'ERROR'], horizontal=True)
    only_slow = st.toggle('Only requests slower than 500 ms')
    live_explorer(level, only_slow)
    with st.expander('Open the same data in Grafana'):
        st.iframe(f'{c.GRAFANA}/d/l2-system-overview?orgId=1&kiosk', height=700)


def reports():
    from mlflow import MlflowClient
    from mlflow.artifacts import load_text
    import mlflow
    st.title('Data quality and drift — Evidently')
    render_guide('Evidently reports')
    mlflow.set_tracking_uri(os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow_server:5000'))
    client = MlflowClient()
    try:
        experiment = client.get_experiment_by_name('NYC_Taxi_Fare_Prediction_MinIO')
        if experiment is None:
            st.info('The first pipeline run has not produced a report yet.')
            return
        runs = client.search_runs([experiment.experiment_id], max_results=30, order_by=['attributes.start_time DESC'])
        if not runs:
            st.info('No run available.')
            return
        chosen = st.selectbox('Run', runs, format_func=lambda r: f'{r.info.run_id[:12]} · {r.info.status}')
        if chosen.info.status != 'FINISHED':
            st.info('The report is available once this run has finished successfully.')
            return
        summary = json.loads(load_text(f'runs:/{chosen.info.run_id}/drift/summary.json'))
        if summary['baseline']:
            st.info('First run: the reference is created, there is nothing to compare yet.')
        else:
            st.metric('Share of drifted features', f"{summary['drift_share']:.0%}")
            report = load_text(f'runs:/{chosen.info.run_id}/drift/drift.html')
            st.download_button('Download the HTML report', report, file_name=f'evidently-{chosen.info.run_id}.html', mime='text/html')
            components.html(report, height=900, scrolling=True)
    except Exception as exc:  # the page must explain, not crash, when MLflow is unreachable
        st.error(f'Reports unavailable: {exc}')
