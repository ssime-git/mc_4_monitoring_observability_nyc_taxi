"""The storytelling pages: one page per use case, each with a trigger, live monitoring and observability."""
import pandas as pd
import requests
import streamlit as st

import common as c
from metric_guides import diagram

LIVE = '3s'


# ---------------------------------------------------------------------------------------- home
@st.fragment(run_every=LIVE)
def status_strip():
    cols = st.columns(6)
    up = c.safe(lambda: c.prom('min(up{job="prediction_api"})'))
    cols[0].metric('API', '–' if up is None else ('🟢 UP' if up == 1 else '🔴 DOWN'))
    ready = c.safe(lambda: c.prom('max(model_loaded)'))
    cols[1].metric('Model', '–' if ready is None else ('🟢 READY' if ready == 1 else '🔴 NONE'))
    firing = c.safe(lambda: sum(1 for s in c.alert_states().values() if s == 'firing'))
    cols[2].metric('Firing alerts', '–' if firing is None else (('🟢 ' if firing == 0 else '🔴 ') + str(firing)))
    c.metric(cols[3], 'p99 latency', c.P99.format(q=0.99) + ' * 1000', ' ms', '{:,.0f}', help='Slowest 1% of predictions')
    c.metric(cols[4], '5xx errors', 'sum(rate(http_requests_total{job="prediction_api",status=~"5.."}[1m])) '
             '/ sum(rate(http_requests_total{job="prediction_api"}[1m])) or vector(0)', ' %', '{:.1f}', scale=100)
    c.metric(cols[5], 'Quotes / min', 'sum(rate(prediction_requests_total[1m])) * 60', '', '{:,.0f}',
             help='Business indicator: fares priced per minute')


def home():
    st.title('🚕 NYC Taxi — ML monitoring & observability')
    st.markdown('**A fare-prediction service in production. Break it on purpose, then watch how monitoring '
                'and observability tell you what happened.**')
    status_strip()
    diagram('Traffic hits the Prediction API', 'Metrics → Prometheus → Grafana & alerts',
            'Logs → Loki and traces → Tempo', 'Alertmanager notifies; the runbook says what to do')
    c.pillars('Numbers over time: rate, errors, latency, drift, model error, business KPIs. '
              'They are cheap, aggregated, and **tell you that** something is wrong — and fire the alert.',
              'Structured logs and traces tied together by a `request_id` / `trace_id`. '
              'They are detailed and **tell you why** — which request, which model version, which slow step.')
    st.subheader('Pick a story')
    stories = [
        ('1_normal', '🟢', 'A normal day', 'Healthy traffic: what “green” looks like (RED metrics, one request followed end to end).'),
        ('2_latency', '🐢', 'A slow service', 'Inject latency and watch p99 cross its threshold, the alert fire and the slow traces appear.'),
        ('3_drift', '🌊', 'Data drift', 'The world changes: feed shifted data to the pipeline and see which features drift.'),
        ('4_validation', '🧹', 'Bad data at the door', 'Invalid requests are rejected (422), counted and logged — without raising a false alarm.'),
        ('5_outage', '💥', 'An outage', 'Stop the API and follow detection → notification → recovery.'),
        ('6_quality', '🎯', 'Model quality', 'Real fares arrive late. Watch the online error diverge from the validation error.'),
        ('7_business', '💰', 'Business impact', 'Traffic collapses: the model is “up”, yet revenue is lost. Only a business KPI sees it.'),
    ]
    for row in (stories[:4], stories[4:]):
        for col, (path, icon, title, text) in zip(st.columns(4), row):
            with col.container(border=True):
                st.markdown(f'### {icon} {title}')
                st.write(text)
                st.page_link(PAGES[path], label='Open →')
    st.divider()
    st.subheader('Before each story')
    st.caption('Puts the demo back to a healthy baseline: normal traffic, no injected latency, API running.')
    if st.button('🔄 Reset to a healthy baseline'):
        for line in c.reset_everything():
            st.write(line)


# ---------------------------------------------------------------------------------------- 1 normal
@st.fragment(run_every=LIVE)
def live_normal():
    a, b, c3, d = st.columns(4)
    c.metric(a, 'Requests / s', 'sum(rate(http_requests_total{job="prediction_api",endpoint="/fare_predict"}[1m]))', '', '{:.2f}', help='RED — Rate')
    c.metric(b, 'Errors (5xx)', 'sum(rate(http_requests_total{job="prediction_api",status=~"5.."}[1m])) / sum(rate(http_requests_total{job="prediction_api"}[1m])) or vector(0)',
             ' %', '{:.1f}', scale=100, help='RED — Errors')
    c.metric(c3, 'p99 latency', c.P99.format(q=0.99) + ' * 1000', ' ms', '{:,.0f}', help='RED — Duration')
    c.metric(d, 'Quotes / min', 'sum(rate(prediction_requests_total[1m])) * 60', '', '{:,.0f}', help='Business KPI')
    left, right = st.columns(2)
    with left:
        c.chart(c.safe(_by_status, pd.DataFrame()), 'Requests per second by status (200 ok · 422 rejected input)', 'req/s')
    with right:
        c.chart(c.safe(lambda: c.prom_range({'p50': c.P99.format(q=0.5), 'p95': c.P99.format(q=0.95), 'p99': c.P99.format(q=0.99)}, 10), pd.DataFrame()),
                'Latency percentiles (seconds)', 'seconds', threshold=1, threshold_label='p99 alert')
    c.alert_chips(['HighErrorRate', 'HighLatencyP99', 'BusinessThroughputLow', 'ServiceDown'])


def _by_status():
    return c.prom_range({'HTTP {status}': 'sum(rate(http_requests_total{job="prediction_api",endpoint="/fare_predict"}[1m])) by (status)'}, 10)


def normal():
    c.scenario_header(1, '🟢', 'A normal day',
                      'The service quotes ~2 fares per second. A few clients send invalid input. Nothing is on fire.',
                      'Rate, errors and duration (the **RED** method) stay flat; all four alert lights stay green.')
    section_a, section_b = st.columns([1, 2])
    with section_a:
        c.section('① Trigger')
        if st.button('🚕 Quote one fare', type='primary', width='stretch'):
            try:
                response = requests.post(f'{c.API}/fare_predict', json=c.PAYLOAD, timeout=10)
                response.raise_for_status()
                st.session_state.last_request = (response.headers['x-request-id'], response.json()['predicted_fare'])
            except requests.RequestException as exc:
                st.session_state.last_request = None
                st.warning(f'Prediction unavailable: {exc}')
        if st.session_state.get('last_request'):
            rid, fare = st.session_state.last_request
            st.success(f'Quoted **${fare:.2f}**')
            st.caption('Correlation id returned in the `X-Request-ID` header:')
            st.code(rid)
        if st.button('🔄 Back to normal traffic', width='stretch'):
            c.safe(lambda: c.simulator())
    with section_b:
        c.section('② Monitoring — metrics')
        live_normal()
    c.section('③ Observability — follow one request through logs')
    st.caption('Every log line is JSON and carries `request_id`, `trace_id` and `model_version`. Metrics say *how many*; '
               'this says *which one*. No trip details are ever logged.')
    follow_request()
    with st.expander('See it in Grafana (“Is it OK?” dashboard)'):
        c.grafana_panel('l0-status', 'is-it-ok', 3, 200)


@st.fragment(run_every='4s')
def follow_request():
    frame = c.safe(lambda: c.recent_logs(8), pd.DataFrame())
    last = st.session_state.get('last_request')
    if last and not frame.empty:
        mine = frame[frame['request_id'] == last[0]]
        if not mine.empty:
            st.success(f'Found the log line of your request `{last[0][:8]}…` — status, duration, model version and trace id are all there.')
    if frame.empty:
        st.caption('Waiting for logs from Loki…')
    else:
        st.dataframe(frame, hide_index=True, width='stretch')


# ---------------------------------------------------------------------------------------- 2 latency
@st.fragment(run_every=LIVE)
def live_latency():
    a, b, c3 = st.columns(3)
    c.metric(a, 'p50', c.P99.format(q=0.5) + ' * 1000', ' ms', '{:,.0f}', help='Half of the requests are faster')
    c.metric(b, 'p95', c.P99.format(q=0.95) + ' * 1000', ' ms', '{:,.0f}')
    c.metric(c3, 'p99', c.P99.format(q=0.99) + ' * 1000', ' ms', '{:,.0f}', help='1% of requests are slower than this. An average would hide it.')
    c.chart(c.safe(lambda: c.prom_range({'p50': c.P99.format(q=0.5), 'p95': c.P99.format(q=0.95), 'p99': c.P99.format(q=0.99)}, 10), pd.DataFrame()),
            'Latency percentiles from the histogram (seconds)', 'seconds', threshold=1, threshold_label='p99 alert')
    c.alert_chips(['HighLatencyP99'])
    left, right = st.columns(2)
    with left:
        c.section('③ Observability — slowest traces')
        st.caption('Same `trace_id` appears in the logs and in Tempo.')
        c.traces_panel()
    with right:
        c.section('③ Observability — slow requests in logs')
        c.logs_panel(5, only_slow=True)
    c.section('Notification received by the on-call channel')
    c.notification_inbox(3, {'HighLatencyP99'})


def latency():
    c.scenario_header(2, '🐢', 'A slow service',
                      'A downstream dependency becomes slow. Averages barely move, but a growing share of customers waits.',
                      'p99 climbs past the red line → `HighLatencyP99` goes pending then firing (~40 s) → a notification arrives.')
    left, right = st.columns([1, 3])
    with left:
        c.section('① Trigger')
        ms = st.select_slider('Injected latency', [0, 250, 500, 1000, 1500, 2500], value=1500, format_func=lambda v: f'{v} ms')
        if st.button('🐢 Apply latency', type='primary', width='stretch'):
            c.safe(lambda: c.set_latency(ms))
            st.toast(f'Latency set to {ms} ms')
        if st.button('✅ Remove latency', width='stretch'):
            c.safe(lambda: c.set_latency(0))
            st.toast('Latency removed')
        st.caption('The fault is injected inside the API handler (`PUT /admin/chaos`, enabled by `CHAOS_ENABLED`).')
    with right:
        c.section('② Monitoring — metrics')
        live_latency()
    with st.expander('See it in Grafana'):
        c.grafana_panel('l0-status', 'is-it-ok', 8, 280)


# ---------------------------------------------------------------------------------------- 3 drift
@st.fragment(run_every='4s')
def live_drift():
    run_id = st.session_state.get('drift_run')
    if run_id:
        name, state = c.safe(lambda: c.pipeline_state(run_id), ('?', 'UNKNOWN'))
        icon = {'COMPLETED': '✅', 'FAILED': '❌', 'CRASHED': '❌', 'RUNNING': '⏳', 'PENDING': '⏳', 'SCHEDULED': '⏳'}.get(state, '⏳')
        st.info(f'{icon} Pipeline run **{name}**: {state} (takes ~1 minute: download → validate → drift → train)')
    frame = c.safe(lambda: c.call('GET', f'{c.PROM}/api/v1/query', params={'query': 'feature_drift_psi'})['data']['result'], [])
    if frame:
        import altair as alt
        data = pd.DataFrame({'feature': [r['metric']['feature'] for r in frame], 'PSI': [float(r['value'][1]) for r in frame]})
        bars = alt.Chart(data).mark_bar().encode(
            y=alt.Y('feature:N', sort='-x', title=None), x=alt.X('PSI:Q', title='PSI (higher = more drift)'),
            color=alt.condition(alt.datum.PSI >= 0.2, alt.value('#d62728'), alt.value('#4c78a8')))
        rule = alt.Chart(pd.DataFrame({'x': [0.2]})).mark_rule(color='#d62728', strokeDash=[6, 4]).encode(x='x:Q')
        st.markdown('**Drift per feature — last pipeline run vs frozen reference** (red line: PSI threshold 0.2 from `config/monitoring.yml`)')
        st.altair_chart((bars + rule).properties(height=280), width='stretch')
    else:
        st.caption('No drift measurement yet.')
    a, b, c3 = st.columns(3)
    c.metric(a, 'Features drifting', 'data_drift_share * 100', ' %', '{:.0f}', help='Dataset drifts when ≥ 30% of features drift')
    c.metric(b, 'Dataset drift flag', 'data_drift_detected', '', '{:.0f}', help='1 = drift detected')
    c.metric(c3, 'Rows quarantined', 'pipeline_rows_quarantined', '', '{:,.0f}', help='Rows rejected by schema validation in the last ingestion')
    c.alert_chips(['DataDriftDetected', 'ModelPerformanceDegraded'])
    c.notification_inbox(3, {'DataDriftDetected', 'ModelPerformanceDegraded'})


def drift():
    c.scenario_header(3, '🌊', 'Data drift',
                      'Next month the trips look different: other pickup zones, other hours, bigger groups. '
                      'The model was trained on the old world.',
                      'Per-feature PSI bars turn red, the dataset flag flips to 1 and `DataDriftDetected` fires.')
    left, right = st.columns([1, 3])
    with left:
        c.section('① Trigger')
        st.caption('Each run is the real Prefect pipeline: ingest TLC data → validate → drift check → train → promote if better.')
        if st.button('🌊 Run pipeline with shifted data', type='primary', width='stretch'):
            try:
                st.session_state.drift_run = c.start_pipeline(simulate_drift=True)
            except requests.RequestException as exc:
                st.error(f'Cannot start the pipeline: {exc}')
        if st.button('✅ Run pipeline on normal data', width='stretch'):
            try:
                st.session_state.drift_run = c.start_pipeline()
            except requests.RequestException as exc:
                st.error(f'Cannot start the pipeline: {exc}')
        st.caption('The alert clears after a run **without** drift (the pipeline publishes its last result).')
        diagram('Reference frozen with the champion', 'Compare PSI / KS per feature', 'Thresholds from config file', 'Flag → alert')
    with right:
        c.section('② Monitoring + ③ Observability')
        live_drift()
    with st.expander('Where to look next'):
        st.markdown('- **Evidently reports** page: the full visual drift report of each run.\n'
                    '- **Prefect** page: logs of every task of the run.\n'
                    '- **MLflow** page: reference data (with its SHA-256), metrics and model version.')


# ---------------------------------------------------------------------------------------- 4 validation
@st.fragment(run_every=LIVE)
def live_validation():
    a, b, c3 = st.columns(3)
    c.metric(a, 'Rejected / s (422)', 'sum(rate(http_requests_total{job="prediction_api",status="422"}[1m])) or vector(0)', '', '{:.2f}', help='Client mistakes: counted, not alarmed')
    c.metric(b, 'Server errors (5xx)', 'sum(rate(http_requests_total{job="prediction_api",status=~"5.."}[1m])) or vector(0)', '', '{:.2f}')
    c.metric(c3, 'Rejected so far', 'sum(invalid_requests_total)', '', '{:,.0f}')
    c.chart(c.safe(lambda: c.prom_range({'accepted (200)': 'sum(rate(http_requests_total{job="prediction_api",endpoint="/fare_predict",status="200"}[1m]))',
                                         'rejected (422)': 'sum(rate(http_requests_total{job="prediction_api",endpoint="/fare_predict",status="422"}[1m])) or vector(0)',
                                         'server errors (5xx)': 'sum(rate(http_requests_total{job="prediction_api",status=~"5.."}[1m])) or vector(0)'}, 10), pd.DataFrame()),
            'What happens to the requests (req/s)', 'req/s')
    c.alert_chips(['HighErrorRate'])
    if (c.safe(lambda: c.prom('sum(rate(http_requests_total{status="422"}[1m]))')) or 0) > 0.5:
        st.success('Lots of rejections, yet **HighErrorRate stays green**: a 422 means *the client is wrong*, not *we are failing*. '
                   'Alerting on symptoms the customer feels avoids alert fatigue.')
    c.section('③ Observability — the rejection logs (field names only, never values)')
    c.logs_panel(6, levels=['WARNING'])


def validation():
    c.scenario_header(4, '🧹', 'Bad data at the door',
                      'Clients send impossible values: location 9999, 99 passengers, text instead of numbers. '
                      'The contract is checked on every request; the pipeline does the same on batches (pandera, with quarantine).',
                      'The 422 line rises, 5xx stays at zero, the error alert stays green, and logs name the offending **fields**.')
    left, right = st.columns([1, 3])
    with left:
        c.section('① Trigger')
        share = st.slider('Share of invalid requests', 0, 100, 50, format='%d %%')
        if st.button('🧹 Apply', type='primary', width='stretch'):
            c.safe(lambda: c.simulator(error_rate=share / 100))
            st.toast(f'{share}% of simulated requests are now invalid')
        if st.button('🧪 Send one invalid request', width='stretch'):
            bad = {**c.PAYLOAD, 'pu_location_id': 9999, 'passenger_count': 99}
            try:
                response = requests.post(f'{c.API}/fare_predict', json=bad, timeout=10)
                st.code(f'HTTP {response.status_code}\n{response.text}', language='json')
            except requests.RequestException as exc:
                st.warning(str(exc))
        if st.button('🔄 Back to normal traffic', width='stretch'):
            c.safe(lambda: c.simulator())
        st.caption('Defined behaviour: **reject (422) + count + log**. Batch data: bad rows are **quarantined** with a reason, '
                   'and the run fails if more than 20% is invalid.')
    with right:
        c.section('② Monitoring')
        live_validation()
    c.section('④ Batch data — where do the rejected rows go?')
    st.caption('The pipeline never drops bad rows silently: they are stored in PostgreSQL (`quarantine_trips`) with the failed check. '
               'The counter you see in Grafana is only the tip; this is the evidence.')
    quarantine_view()


@st.fragment(run_every='15s')
def quarantine_view():
    try:
        result = c.quarantine()
    except Exception:  # psycopg2 errors are not requests errors; the page must still render when the DB is down
        result = None
    if result is None:
        st.warning('Cannot read the quarantine table (PostgreSQL unreachable).')
        return
    by_reason, sample = result
    if by_reason.empty:
        st.success('Nothing quarantined yet: no invalid row in the ingested batches.')
        return
    left, right = st.columns([2, 3])
    left.metric('Rows in quarantine', f"{int(by_reason['rows'].sum()):,}")
    left.dataframe(by_reason, hide_index=True, width='stretch')
    right.markdown('**Random sample** (the failing value is visible, e.g. a zero or negative fare)')
    right.dataframe(sample, hide_index=True, width='stretch')


# ---------------------------------------------------------------------------------------- 5 outage
@st.fragment(run_every='2s')
def live_outage():
    state = c.safe(lambda: c.call('GET', f'{c.CONTROLLER}/services/prediction_api')['state'], 'unknown')
    st.metric('Prediction API container', {'running': '🟢 running', 'exited': '🔴 stopped'}.get(state, state))
    a, b = st.columns(2)
    up = c.safe(lambda: c.prom('min(up{job="prediction_api"})'))
    a.metric('Prometheus scrape (up)', '–' if up is None else ('🟢 1' if up == 1 else '🔴 0'))
    health = c.safe(lambda: requests.get(f'{c.API}/health', timeout=2).status_code, None)
    b.metric('/health', 'unreachable' if health is None else ('🟢 200' if health == 200 else f'🔴 {health}'))
    c.chart(c.safe(lambda: c.prom_range({'up': 'min(up{job="prediction_api"})'}, 5, 3), pd.DataFrame()), 'Target availability (1 = scraped successfully)', 'up')
    c.alert_chips(['ServiceDown', 'ModelNotLoaded', 'BusinessThroughputLow'])
    c.section('Notifications received')
    c.notification_inbox(4, {'ServiceDown', 'ModelNotLoaded'})


def outage():
    c.scenario_header(5, '💥', 'An outage',
                      'The prediction service crashes. Customers get no quote at all.',
                      '`up` drops to 0 → `ServiceDown` fires in ~15 s, then the cascade: `ModelNotLoaded` (~1 min) and `BusinessThroughputLow` turn red, but Alertmanager only notifies the root cause. '
                      'Observability note: with the process gone, **only metrics from outside** (Prometheus) can notice.')
    left, right = st.columns([1, 3])
    with left:
        c.section('① Trigger')
        if st.button('💥 Stop the API', type='primary', width='stretch'):
            c.safe(lambda: c.call('POST', f'{c.CONTROLLER}/services/prediction_api/stop', timeout=30))
        if st.button('🩹 Restart the API', width='stretch'):
            c.safe(lambda: c.call('POST', f'{c.CONTROLLER}/services/prediction_api/start', timeout=30))
        st.caption('A small `controller` service can stop/start **only** `prediction_api` (allow-list). '
                   'After restart the model reloads from MLflow (~20 s), then `/health` turns green.')
    with right:
        c.section('② Monitoring')
        live_outage()


# ---------------------------------------------------------------------------------------- 6 quality
@st.fragment(run_every=LIVE)
def live_quality():
    a, b, c3 = st.columns(3)
    c.metric(a, 'Online RMSE', c.ONLINE_RMSE, ' $', '{:.1f}', help='Error measured on predictions whose real fare is now known')
    c.metric(b, 'Validation RMSE', 'scalar(model_drift_rmse_reference)', ' $', '{:.1f}', help='Error measured at training time')
    c.metric(c3, 'Labeled predictions', 'sum(labeled_predictions_total)', '', '{:,.0f}', help='Real fares are fed back ~15 s after each quote (synthetic here)')
    ref = c.safe(lambda: c.prom('scalar(model_drift_rmse_reference)'))
    c.chart(c.safe(lambda: c.prom_range({'online RMSE': c.ONLINE_RMSE}, 10), pd.DataFrame()),
            'Online RMSE vs the alert line (1.25 × validation RMSE), in dollars', '$',
            threshold=None if ref is None else round(1.25 * ref, 2), threshold_label='alert')
    c.alert_chips(['OnlineModelErrorHigh', 'ModelPerformanceDegraded'])
    c.notification_inbox(3, {'OnlineModelErrorHigh'})
    c.section('③ Observability — which model produced these predictions?')
    c.logs_panel(4)


def quality():
    c.scenario_header(6, '🎯', 'Model quality with delayed labels',
                      'You only learn the true fare after the trip. The world changes (fares +100%); the model does not know.',
                      'Online RMSE climbs above 1.25 × the validation RMSE → `OnlineModelErrorHigh`. Drift tells you the *inputs* moved; this tells you *predictions are wrong*.')
    left, right = st.columns([1, 3])
    with left:
        c.section('① Trigger')
        if st.button('📈 Real fares jump by +100%', type='primary', width='stretch'):
            c.safe(lambda: c.simulator(label_bias=1.0))
            st.toast('Ground truth now diverges from predictions (≈ 15 s delay)')
        if st.button('✅ Back to normal fares', width='stretch'):
            c.safe(lambda: c.simulator(label_bias=0.0))
        st.caption('Labels are **synthetic**: prediction + noise (+ bias when triggered), sent to `POST /feedback` and matched by `request_id`. '
                   'In production they would come from billing.')
    with right:
        c.section('② Monitoring')
        live_quality()
    quality_explainer()


def quality_explainer():
    st.divider()
    c.section('💡 How to read this page — where does the “real fare” come from?')
    diagram('A trip is priced (prediction, e.g. $25)', 'The API remembers it under its request_id',
            '~15 s later the real fare arrives (e.g. $31)', 'Error = prediction − real fare → RMSE',
            'Compared with the validation RMSE → alert')
    demo, prod = st.columns(2)
    with demo.container(border=True):
        st.markdown('##### 🎭 In this demo — synthetic')
        st.markdown('- The **simulator invents** the real fare: `prediction × (1 + bias) + random noise`.\n'
                    '- Noise (≈ $12) is set close to the validation error, so the ratio starts near **1×**.\n'
                    '- The **+100 %** button sets the bias to 1: real fares double and the model does not know.\n'
                    '- It does **not** prove the model is bad; it proves the *measurement chain and the alert work*.')
    with prod.container(border=True):
        st.markdown('##### 🏭 In production — real')
        st.markdown('- The real fare comes from **billing or the end of the trip** (the taximeter).\n'
                    '- It is joined to the prediction by an **id** (here `request_id`, in practice a trip id).\n'
                    '- It arrives **late** (minutes to days): that is why the metric is called *delayed labels*.\n'
                    '- In the NYC TLC data this is the `fare_amount` column, used for training.')
    st.markdown('**The alert rule, in one line:** online RMSE > **1.25 × validation RMSE** for 30 s '
                '(the threshold follows each newly trained model instead of being a magic number). '
                'Drift looks at the **inputs**; this looks at whether the **predictions are wrong**.')


# ---------------------------------------------------------------------------------------- 7 business
@st.fragment(run_every=LIVE)
def live_business():
    a, b = st.columns(2)
    c.metric(a, 'Quotes / min', 'sum(rate(prediction_requests_total[1m])) * 60', '', '{:,.0f}')
    c.metric(b, 'Predicted revenue / min', 'sum(rate(total_predicted_revenue_dollars_total[1m])) * 60', ' $', '{:,.0f}')
    left, right = st.columns(2)
    with left:
        c.chart(c.safe(lambda: c.prom_range({'quotes/min': 'sum(rate(prediction_requests_total[1m])) * 60'}, 10), pd.DataFrame()),
                'Quotes per minute', 'quotes/min', threshold=30, threshold_label='alert')
    with right:
        c.chart(c.safe(lambda: c.prom_range({'USD/min': 'sum(rate(total_predicted_revenue_dollars_total[1m])) * 60'}, 10), pd.DataFrame()),
                'Predicted revenue per minute ($)', '$/min')
    c.alert_chips(['BusinessThroughputLow', 'HighErrorRate', 'HighLatencyP99', 'ServiceDown'])
    c.notification_inbox(3, {'BusinessThroughputLow'})


def business():
    c.scenario_header(7, '💰', 'Business impact',
                      'The app stops sending requests (a bad release, a broken button). The API is perfectly healthy — and the business is losing quotes.',
                      'Technical alerts stay green; only `BusinessThroughputLow` fires. A KPI tied to the model catches what infrastructure monitoring cannot.')
    left, right = st.columns([1, 3])
    with left:
        c.section('① Trigger')
        if st.button('⏸ Traffic collapses', type='primary', width='stretch'):
            c.safe(lambda: c.simulator(running=False))
        if st.button('▶ Traffic back', width='stretch'):
            c.safe(lambda: c.simulator())
    with right:
        c.section('② Monitoring')
        live_business()


# Filled by app.py once st.Page objects exist (used for page_link).
PAGES = {}
