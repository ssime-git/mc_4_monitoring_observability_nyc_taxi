"""Deep-dive pages: live alerts, logs/traces explorer, embedded tool UIs and Evidently reports."""
import json
import os
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import common as c
import ui
from metric_guides import render_guide

URLS = {
    'Prefect': os.getenv('PREFECT_UI_URL', 'http://localhost:4200'),
    'Prometheus': os.getenv('PROMETHEUS_UI_URL', 'http://localhost:9090'),
    'Alertmanager': os.getenv('ALERTMANAGER_UI_URL', 'http://localhost:9093'),
    'MLflow': os.getenv('MLFLOW_UI_URL', 'http://localhost:5000'),
}
DASHBOARDS = {'L0 Is it OK?': 'l0-status', 'L1 Executive': 'l1-executive',
              'L2 System (RED)': 'l2-system-overview', 'L3 Deep dive': 'l3-deep-dive'}


def embed(title, url):
    ui.page_header('Deep dive', title)
    render_guide(title)
    parsed = urlsplit(url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        st.error('The configured URL must be an HTTP(S) address reachable from the browser.')
        return
    st.iframe(url, height=900)
    st.link_button('Open in a new tab', url)


def grafana():
    ui.page_header('Observe', 'Grafana dashboards')
    render_guide('Grafana')
    choice = st.segmented_control('Dashboard', list(DASHBOARDS), default='L0 Is it OK?', label_visibility='collapsed') or 'L0 Is it OK?'
    url = f'{c.GRAFANA}/d/{DASHBOARDS[choice]}?orgId=1&kiosk&theme={ui.color_scheme()}'
    st.iframe(url, height=900)
    st.link_button('Open in a new tab', url)


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
    if not rules:
        ui.waiting('Prometheus rules are not reachable.')
    else:
        states = c.safe(c.alert_states, {})
        rows = pd.DataFrame([{'state': states.get(r['name'], 'inactive'), 'alert': r['name'], 'layer': r['labels'].get('layer'),
                              'severity': r['labels'].get('severity'), 'for': f"{int(r['duration'])} s",
                              'action': r['annotations'].get('action', '').replace('{{ $labels.job }}', '<service>')} for r in rules if r['type'] == 'alerting'])

        def state_cell(state):
            dot = {'firing': 'dot crit live', 'pending': 'dot warn'}.get(state, 'dot')
            return f'<span style="display:inline-flex;align-items:center;gap:8px"><i class="{dot}"></i>{state}</span>'
        ui.table(rows, mono=('state', 'alert', 'layer', 'severity', 'for'), render={'state': state_cell})
    ui.heading('Notifications', 'Alertmanager posts to `alert_webhook`, standing in for Slack or PagerDuty. '
               'In production, change the receiver in `services/alertmanager/alertmanager.yml`.')
    ui.inbox(c.notifications(), 10)


def alerts():
    ui.page_header('Observe', 'Alerts and notifications',
                   'Every alert has a justified threshold, a `for:` duration, a severity, an action and a runbook in `docs/RUNBOOKS.md`. '
                   'Each layer is covered: application, platform, model, business.')
    live_alerts()
    with st.container(horizontal=True):
        if st.button('Clear notifications'):
            c.safe(lambda: c.call('DELETE', f'{c.WEBHOOK}/alerts'))
        st.link_button('Open Alertmanager', URLS['Alertmanager'])


@st.fragment(run_every='4s')
def live_explorer(level, only_slow):
    left, right = st.columns([3, 2])
    with left:
        ui.heading('Logs', step='Loki')
        ui.logs(c.filtered_logs(25, only_slow=only_slow, levels=None if level == 'All' else [level]))
    with right:
        ui.heading('Slowest traces', step='Tempo')
        ui.traces(c.safe(lambda: c.slowest_traces(10), pd.DataFrame()))


def logs_traces():
    ui.page_header('Observe', 'Logs and traces',
                   'Structured logs carry `request_id`, `trace_id` and `model_version`. Take a `request_id` from a response header, '
                   'find the log line, then the trace.')
    with st.container(horizontal=True, vertical_alignment='center'):
        level = st.segmented_control('Level', ['All', 'INFO', 'WARNING', 'ERROR'], default='All', label_visibility='collapsed') or 'All'
        only_slow = st.toggle('Slower than 500 ms')
    live_explorer(level, only_slow)
    with st.expander('Same data in Grafana'):
        st.iframe(f'{c.GRAFANA}/d/l2-system-overview?orgId=1&kiosk&theme={ui.color_scheme()}', height=700)


def reports():
    from mlflow import MlflowClient
    from mlflow.artifacts import load_text
    import mlflow
    ui.page_header('Deep dive', 'Evidently reports')
    render_guide('Evidently reports')
    mlflow.set_tracking_uri(os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow_server:5000'))
    client = MlflowClient()
    try:
        experiment = client.get_experiment_by_name('NYC_Taxi_Fare_Prediction_MinIO')
        if experiment is None:
            ui.waiting('The first pipeline run has not produced a report yet.')
            return
        runs = client.search_runs([experiment.experiment_id], max_results=30, order_by=['attributes.start_time DESC'])
        if not runs:
            ui.waiting('No run available.')
            return
        chosen = st.selectbox('Run', runs, format_func=lambda r: f'{r.info.run_id[:12]} · {r.info.status}')
        if chosen.info.status != 'FINISHED':
            ui.waiting('The report is available once this run has finished successfully.')
            return
        summary = json.loads(load_text(f'runs:/{chosen.info.run_id}/drift/summary.json'))
        if summary['baseline']:
            ui.waiting('First run: the reference is created, there is nothing to compare yet.')
        else:
            ui.stats([ui.stat('Share of drifted features', f"{summary['drift_share']:.0%}")])
            report = load_text(f'runs:/{chosen.info.run_id}/drift/drift.html')
            st.download_button('Download the HTML report', report, file_name=f'evidently-{chosen.info.run_id}.html', mime='text/html')
            components.html(report, height=900, scrolling=True)
    except Exception as exc:  # the page must explain, not crash, when MLflow is unreachable
        st.error(f'Reports unavailable: {exc}')
