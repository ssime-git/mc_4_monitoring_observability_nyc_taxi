"""NYC Taxi portal: login, then a guided story (one page per use case) and deep-dive tools."""
import hmac
import os
from datetime import datetime

import requests
import streamlit as st

import common as c
import story
import tools
import ui


def prediction_page():
    ui.page_header('Deep dive', 'Manual prediction',
                   'Any trip you enter goes through the same API, metrics and logs as the simulated traffic.')
    col1, col2 = st.columns(2)
    with col1:
        ui.heading('Trip details')
        vendor_id = st.selectbox('Vendor ID', [1, 2], index=0)
        passenger_count = st.number_input('Passenger count', min_value=1, max_value=10, value=1)
        rate_code_id = st.number_input('Rate code ID', min_value=1.0, max_value=6.0, value=1.0)
    with col2:
        ui.heading('Zones and time')
        pu_location_id = st.number_input('Pickup zone ID', min_value=1, max_value=265, value=48)
        do_location_id = st.number_input('Dropoff zone ID', min_value=1, max_value=265, value=79)
        pickup_datetime = st.date_input('Pickup date', datetime.today())
        pickup_time = st.time_input('Pickup time', datetime.now().time())
    if st.button('Predict fare', width='stretch', type='primary'):
        pickup_dt = datetime.combine(pickup_datetime, pickup_time)
        payload = {'vendor_id': vendor_id, 'passenger_count': float(passenger_count), 'rate_code_id': rate_code_id,
                   'pu_location_id': pu_location_id, 'do_location_id': do_location_id, 'pickup_hour': pickup_dt.hour,
                   'pickup_dayofweek': pickup_dt.weekday(), 'pickup_month': pickup_dt.month,
                   'pickup_weekday': 1 if pickup_dt.weekday() < 5 else 0}
        try:
            response = requests.post(f'{c.API}/fare_predict', json=payload, timeout=30)
        except requests.RequestException as exc:
            st.error(f'Cannot reach the API: {exc}')
            return
        if response.status_code == 200:
            ui.stats([ui.stat('Predicted fare', f"${response.json()['predicted_fare']:.2f}", hint=response.headers.get('x-request-id'))])
        else:
            st.error(f'API error {response.status_code}: {response.text}')


def require_login():
    if st.session_state.get('authenticated', False):
        return
    expected_user = os.getenv('PORTAL_USERNAME', 'demo')
    expected_password = os.getenv('PORTAL_PASSWORD', '')
    with st.container(key='login'):
        ui.page_header(None, 'NYC Taxi portal', 'Sign in to run the demo.')
        if not expected_password:
            st.error('Set PORTAL_PASSWORD to enable the portal.')
            st.stop()
        with st.form('login_form', border=False):
            username = st.text_input('Username', key='login_username')
            password = st.text_input('Password', type='password', key='login_password')
            submitted = st.form_submit_button('Sign in', type='primary', width='stretch')
        if submitted:
            valid_user = hmac.compare_digest(username.encode(), expected_user.encode())
            valid_password = hmac.compare_digest(password.encode(), expected_password.encode())
            if valid_user and valid_password:
                st.session_state['authenticated'] = True
                del st.session_state['login_username']
                del st.session_state['login_password']
                st.rerun()
            st.error('Wrong username or password.')
    st.stop()


def build_navigation():
    def page(func, title, icon, path, default=False):
        return st.Page(func, title=title, icon=f':material/{icon}:', url_path=path, default=default)

    scenarios = {
        '1_normal': page(story.normal, 'A normal day', 'check_circle', 'normal'),
        '2_latency': page(story.latency, 'A slow service', 'speed', 'latency'),
        '3_drift': page(story.drift, 'Data drift', 'show_chart', 'drift'),
        '4_validation': page(story.validation, 'Bad data', 'rule', 'bad-data'),
        '5_outage': page(story.outage, 'An outage', 'power_off', 'outage'),
        '6_quality': page(story.quality, 'Model quality', 'target', 'quality'),
        '7_business': page(story.business, 'Business impact', 'payments', 'business'),
    }
    story.PAGES.update(scenarios)
    return st.navigation({
        'Overview': [page(story.home, 'Start here', 'home', 'home', default=True)],
        'Scenarios': list(scenarios.values()),
        'Observe': [page(tools.alerts, 'Alerts', 'notifications', 'alerts'),
                    page(tools.logs_traces, 'Logs and traces', 'manage_search', 'logs'),
                    page(tools.grafana, 'Grafana', 'dashboard', 'grafana')],
        'Deep dive': [page(tools.prefect, 'Prefect', 'account_tree', 'prefect'),
                      page(tools.mlflow_page, 'MLflow', 'science', 'mlflow'),
                      page(tools.reports, 'Evidently reports', 'description', 'evidently'),
                      page(tools.prometheus, 'Prometheus', 'monitoring', 'prometheus'),
                      page(tools.alertmanager, 'Alertmanager', 'campaign', 'alertmanager'),
                      page(prediction_page, 'Manual prediction', 'calculate', 'predict')],
    }, expanded=True)


@st.fragment(run_every='5s')
def sidebar_status():
    states = c.safe(c.alert_states)
    ui.system_status(None if states is None else sum(1 for s in states.values() if s == 'firing'))


def main():
    st.set_page_config(page_title='NYC Taxi monitoring', page_icon=':material/local_taxi:', layout='wide')
    ui.inject_theme()
    require_login()
    navigation = build_navigation()
    with st.sidebar:
        sidebar_status()
        ui.theme_switch()
        if st.button('Sign out', width='stretch'):
            st.session_state.clear()
            st.rerun()
    navigation.run()


if __name__ == '__main__':
    main()
