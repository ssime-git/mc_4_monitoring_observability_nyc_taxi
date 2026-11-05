"""NYC Taxi portal: login, then a guided story (one page per use case) and deep-dive tools."""
import hmac
import os
from datetime import datetime

import requests
import streamlit as st

import common as c
import story
import tools


def prediction_page():
    st.title('🚖 Manual fare prediction')
    st.markdown('Any trip you enter goes through the same API, metrics and logs as the simulated traffic.')
    col1, col2 = st.columns(2)
    with col1:
        st.subheader('Trip details')
        vendor_id = st.selectbox('Vendor ID', [1, 2], index=0)
        passenger_count = st.number_input('Passenger count', min_value=1, max_value=10, value=1)
        rate_code_id = st.number_input('Rate code ID', min_value=1.0, max_value=6.0, value=1.0)
    with col2:
        st.subheader('Zones and time')
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
            st.success(f"Predicted fare: ${response.json()['predicted_fare']:.2f}")
            st.caption(f"request_id: `{response.headers.get('x-request-id')}`")
        else:
            st.error(f'API error {response.status_code}: {response.text}')


def require_login():
    if st.session_state.get('authenticated', False):
        return
    st.title('NYC Taxi portal — sign in')
    expected_user = os.getenv('PORTAL_USERNAME', 'demo')
    expected_password = os.getenv('PORTAL_PASSWORD', '')
    if not expected_password:
        st.error('Set PORTAL_PASSWORD to enable the portal.')
        st.stop()
    with st.form('login'):
        username = st.text_input('Username', key='login_username')
        password = st.text_input('Password', type='password', key='login_password')
        submitted = st.form_submit_button('Sign in', width='stretch')
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
    def page(func, title, icon, path):
        return st.Page(func, title=title, icon=icon, url_path=path)

    story_pages = {
        '1_normal': page(story.normal, '1 · A normal day', '🟢', 'normal'),
        '2_latency': page(story.latency, '2 · A slow service', '🐢', 'latency'),
        '3_drift': page(story.drift, '3 · Data drift', '🌊', 'drift'),
        '4_validation': page(story.validation, '4 · Bad data', '🧹', 'bad-data'),
        '5_outage': page(story.outage, '5 · An outage', '💥', 'outage'),
        '6_quality': page(story.quality, '6 · Model quality', '🎯', 'quality'),
        '7_business': page(story.business, '7 · Business impact', '💰', 'business'),
    }
    story.PAGES.update(story_pages)
    home = page(story.home, 'Start here', '🏠', 'home')
    return st.navigation({
        'Story': [home, *story_pages.values()],
        'Observe': [page(tools.alerts, 'Alerts & notifications', '🚨', 'alerts'),
                    page(tools.logs_traces, 'Logs & traces', '🔎', 'logs'),
                    page(tools.grafana, 'Grafana dashboards', '📊', 'grafana')],
        'Deep dive': [page(tools.prefect, 'Prefect', '🔁', 'prefect'),
                      page(tools.mlflow_page, 'MLflow', '🧪', 'mlflow'),
                      page(tools.reports, 'Evidently reports', '📑', 'evidently'),
                      page(tools.prometheus, 'Prometheus', '🔥', 'prometheus'),
                      page(tools.alertmanager, 'Alertmanager', '🔔', 'alertmanager'),
                      page(prediction_page, 'Manual prediction', '🚖', 'predict')],
    })


def main():
    st.set_page_config(page_title='NYC Taxi · Monitoring', page_icon='🚕', layout='wide')
    require_login()
    navigation = build_navigation()
    with st.sidebar:
        if st.button('Sign out', width='stretch'):
            st.session_state.clear()
            st.rerun()
    navigation.run()


if __name__ == '__main__':
    main()
