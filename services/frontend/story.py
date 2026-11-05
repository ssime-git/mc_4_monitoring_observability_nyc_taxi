"""The storytelling pages: one page per use case, each with a trigger, live monitoring and observability."""
import pandas as pd
import requests
import streamlit as st

import common as c
import ui
from metric_guides import diagram

LIVE = '3s'
API_JOB = 'job="prediction_api"'
RATE = f'sum(rate(http_requests_total{{{API_JOB},endpoint="/fare_predict"}}[1m]))'
BY_STATUS = f'sum by (status) (rate(http_requests_total{{{API_JOB},endpoint="/fare_predict"}}[1m]))'
ERROR_RATIO = (f'sum(rate(http_requests_total{{{API_JOB},status=~"5.."}}[1m])) '
               f'/ sum(rate(http_requests_total{{{API_JOB}}}[1m])) or vector(0)')
QUOTES = 'sum(rate(prediction_requests_total[1m])) * 60'
REVENUE = 'sum(rate(total_predicted_revenue_dollars_total[1m])) * 60'
PERCENTILES = {f'p{q}': c.P99.format(q=f'0.{q}') for q in (50, 95, 99)}

SCENARIOS = [
    ('1_normal', 'A normal day', 'Healthy traffic: what green looks like, and one request followed end to end.', 'no alert'),
    ('2_latency', 'A slow service', 'Inject latency, watch p99 cross its threshold and the slow traces appear.', 'HighLatencyP99'),
    ('3_drift', 'Data drift', 'Feed shifted data to the pipeline and see which features moved.', 'DataDriftDetected'),
    ('4_validation', 'Bad data', 'Invalid input is rejected, counted and logged, with no false alarm.', 'no alert'),
    ('5_outage', 'An outage', 'Stop the API and follow detection, the cascade and recovery.', 'ServiceDown'),
    ('6_quality', 'Model quality', 'Real fares arrive late; the online error drifts from the validation error.', 'OnlineModelErrorHigh'),
    ('7_business', 'Business impact', 'Traffic collapses while every technical light stays green.', 'BusinessThroughputLow'),
]
PAGES = {}  # filled by app.py once the st.Page objects exist


def frame_of(queries, minutes=10, step=5):
    return c.safe(lambda: c.prom_range(queries, minutes, step), pd.DataFrame())


def number(label, query, unit='', fmt='{:,.0f}', hint=None, scale=1.0, warn=None, crit=None, below=False):
    """A tile fed by Prometheus. warn/crit colour the value when it crosses an alert threshold."""
    text, raw = c.value(query, fmt, scale)
    tone = None
    if raw is not None:
        over = (lambda limit: raw <= limit) if below else (lambda limit: raw >= limit)
        tone = 'crit' if crit is not None and over(crit) else 'warn' if warn is not None and over(warn) else None
    return ui.stat(label, text, unit if raw is not None else '', hint, tone)


def p_tile(q, hint=None):
    return number(f'p{q}', PERCENTILES[f'p{q}'] + ' * 1000', 'ms', hint=hint, warn=500, crit=1000)


def states():
    return c.safe(c.alert_states, {})


def alerts(*names):
    ui.alert_states(names, states())


def pager(index):
    """Previous / next scenario, at the bottom of each page."""
    with st.container(key='pager', horizontal=True, horizontal_alignment='distribute'):
        if index > 1:
            key, title, _, _ = SCENARIOS[index - 2]
            st.page_link(PAGES[key], label=f'Previous: {title}', icon=':material/arrow_back:')
        else:
            st.html('<div></div>')  # keeps "Next" on the right
        if index < len(SCENARIOS):
            key, title, _, _ = SCENARIOS[index]
            st.page_link(PAGES[key], label=f'Next: {title}', icon=':material/arrow_forward:', icon_position='right')


def scenario_header(index, title, story, watch):
    ui.page_header(f'Scenario {index} of {len(SCENARIOS)}', title, story)
    ui.watch(watch)


def run(action, done=None):
    """Run a control action; say so when the backend refused it instead of failing silently."""
    if c.safe(action) is None:
        st.toast('The service did not answer. Is the stack running?')
    elif done:
        st.toast(done)


# ---------------------------------------------------------------------------------------- home
@st.fragment(run_every=LIVE)
def status_strip():
    up, _ = c.value(f'min(up{{{API_JOB}}})', '{:.0f}')
    ready, _ = c.value('max(model_loaded)', '{:.0f}')
    alert_map = c.safe(c.alert_states)
    firing = None if alert_map is None else sum(1 for s in alert_map.values() if s == 'firing')
    ui.stats([
        ui.stat('API', {'1': 'Up', '0': 'Down'}.get(up, '–'), tone={'1': 'good', '0': 'crit'}.get(up)),
        ui.stat('Model', {'1': 'Ready', '0': 'None'}.get(ready, '–'), tone={'1': 'good', '0': 'crit'}.get(ready)),
        ui.stat('Firing alerts', '–' if firing is None else firing, tone=None if firing is None else 'crit' if firing else 'good'),
        number('p99 latency', PERCENTILES['p99'] + ' * 1000', 'ms', warn=500, crit=1000),
        number('5xx errors', ERROR_RATIO, '%', '{:.1f}', scale=100, warn=1, crit=5),
        number('Quotes per minute', QUOTES, warn=60, crit=30, below=True),
    ])


def home():
    ui.page_header('Monitoring masterclass', 'NYC Taxi fare service',
                   'A fare-prediction service in production. Break it on purpose, then watch how monitoring and '
                   'observability explain what happened.')
    status_strip()
    diagram('Traffic hits the Prediction API', 'Metrics go to Prometheus, then Grafana and alert rules',
            'Logs go to Loki, traces to Tempo', 'Alertmanager notifies; the runbook says what to do')
    ui.split('Monitoring: is something wrong?',
             'Numbers over time: rate, errors, latency, drift, model error, business KPIs. Cheap, aggregated, and they fire the alert.',
             'Observability: why is it wrong?',
             'Structured logs and traces tied together by `request_id` and `trace_id`. Detailed: which request, which model version, which slow step.')
    ui.heading('Scenarios')
    with st.container(key='stories'):
        for index, (key, title, text, alert) in enumerate(SCENARIOS, 1):
            with st.container(key=f'story_{index}'):
                st.html(f'<div class="story"><span>{index:02d}</span><b>{title}</b><p>{text}</p><code>{alert}</code></div>')
                st.page_link(PAGES[key], label=f'Open {title}')
    ui.heading('Before each scenario', 'Back to a healthy baseline: normal traffic, no injected latency, API running.')
    if st.button('Reset to baseline'):
        for label, ok in c.reset_everything():
            st.toast(f"{label}: {'done' if ok else 'failed'}")


# ---------------------------------------------------------------------------------------- 1 normal
@st.fragment(run_every=LIVE)
def live_normal():
    ui.stats([
        number('Requests per second', RATE, fmt='{:.2f}', hint='Rate'),
        number('5xx errors', ERROR_RATIO, '%', '{:.1f}', 'Errors', scale=100, warn=1, crit=5),
        number('p99 latency', PERCENTILES['p99'] + ' * 1000', 'ms', hint='Duration', warn=500, crit=1000),
        number('Quotes per minute', QUOTES, hint='Business', warn=60, crit=30, below=True),
    ])
    left, right = st.columns(2)
    with left:
        ui.chart(frame_of({'HTTP {status}': BY_STATUS}),
                 'Requests by status', 'req/s')
    with right:
        ui.chart(frame_of(PERCENTILES), 'Latency percentiles', 'seconds', threshold=1, threshold_label='p99 alert')
    alerts('HighErrorRate', 'HighLatencyP99', 'BusinessThroughputLow', 'ServiceDown')


def normal():
    scenario_header(1, 'A normal day',
                    'The service quotes about two fares per second. A few clients send invalid input. Nothing is on fire.',
                    'rate, errors and duration (the **RED** method) stay flat, and all four alerts stay inactive.')
    left, right = st.columns([1, 3], gap='large')
    with left:
        with st.container(border=True, key='trigger'):
            ui.heading('Trigger', step=1)
            if st.button('Quote one fare', type='primary', width='stretch'):
                try:
                    response = requests.post(f'{c.API}/fare_predict', json=c.PAYLOAD, timeout=10)
                    response.raise_for_status()
                    st.session_state.last_request = (response.headers['x-request-id'], response.json()['predicted_fare'])
                except requests.RequestException as exc:
                    st.session_state.last_request = None
                    st.warning(f'Prediction unavailable: {exc}')
            if st.session_state.get('last_request'):
                rid, fare = st.session_state.last_request
                ui.stats([ui.stat('Quoted fare', f'${fare:.2f}')])
                ui.hint('Correlation id, from the `X-Request-ID` header:')
                st.code(rid, language=None)
            if st.button('Back to normal traffic', width='stretch'):
                run(c.simulator, 'Normal traffic restored')
    with right:
        ui.heading('Monitoring', step=2)
        live_normal()
    ui.heading('Observability', 'Every log line is JSON with `request_id`, `trace_id` and `model_version`. Metrics say *how many*; '
               'this says *which one*. Trip details are never logged. Your request is highlighted.', step=3)
    follow_request()
    with st.expander('Same data in Grafana'):
        st.iframe(c.grafana_panel_url('l0-status', 'is-it-ok', 3, ui.color_scheme()), height=200)
    pager(1)


@st.fragment(run_every='4s')
def follow_request():
    frame = c.safe(lambda: c.recent_logs(8), pd.DataFrame())
    last = st.session_state.get('last_request')
    ui.logs(frame, highlight=(lambda row: row['request'] == last[0]) if last else None)


# ---------------------------------------------------------------------------------------- 2 latency
@st.fragment(run_every=LIVE)
def live_latency():
    ui.stats([p_tile(50, 'Half of requests are faster'), p_tile(95),
              p_tile(99, '1% are slower. An average would hide it.')])
    ui.chart(frame_of(PERCENTILES), 'Latency percentiles, from the histogram', 'seconds', threshold=1, threshold_label='p99 alert')
    alerts('HighLatencyP99')
    ui.heading('Observability', step=3)
    left, right = st.columns(2)
    with left:
        ui.hint('Slowest traces. The same `trace_id` appears in the logs and in Tempo.')
        ui.traces(c.safe(c.slowest_traces, pd.DataFrame()))
    with right:
        ui.hint('Requests slower than 500 ms, in the logs.')
        ui.logs(c.filtered_logs(5, only_slow=True).drop(columns=['level', 'model_version', 'trace_id'], errors='ignore'))
    ui.heading('On-call channel')
    ui.inbox(c.notifications({'HighLatencyP99'}), 3)


def latency():
    scenario_header(2, 'A slow service',
                    'A downstream dependency becomes slow. Averages barely move, but a growing share of customers waits.',
                    'p99 climbs past the red line, `HighLatencyP99` goes pending then firing in about 40 s, and a notification arrives.')
    left, right = st.columns([1, 3], gap='large')
    with left:
        with st.container(border=True, key='trigger'):
            ui.heading('Trigger', step=1)
            ms = st.select_slider('Injected latency', [0, 250, 500, 1000, 1500, 2500], value=1500, format_func=lambda v: f'{v} ms')
            if st.button('Apply latency', type='primary', width='stretch'):
                run(lambda: c.set_latency(ms), f'Latency set to {ms} ms')
            if st.button('Remove latency', width='stretch'):
                run(lambda: c.set_latency(0), 'Latency removed')
            ui.hint('Injected inside the API handler through `PUT /admin/chaos`, enabled by `CHAOS_ENABLED`.')
    with right:
        ui.heading('Monitoring', step=2)
        live_latency()
    with st.expander('Same data in Grafana'):
        st.iframe(c.grafana_panel_url('l0-status', 'is-it-ok', 8, ui.color_scheme()), height=280)
    pager(2)


# ---------------------------------------------------------------------------------------- 3 drift
@st.fragment(run_every='4s')
def live_drift():
    run_id = st.session_state.get('drift_run')
    if run_id:
        name, state = c.safe(lambda: c.pipeline_state(run_id), ('unknown', 'UNKNOWN'))
        tone = {'COMPLETED': 'good', 'FAILED': 'crit', 'CRASHED': 'crit'}.get(state, 'warn')
        ui.stats([ui.stat(f'Pipeline run {name}', state.capitalize(), tone=tone,
                          hint='About a minute: download, validate, drift, train')])
    result = c.safe(lambda: c.call('GET', f'{c.PROM}/api/v1/query', params={'query': 'feature_drift_psi'})['data']['result'], [])
    if result:
        data = pd.DataFrame({'feature': [r['metric']['feature'] for r in result], 'PSI': [float(r['value'][1]) for r in result]})
        ui.bar_chart(data, 'feature', 'PSI', 0.2, 'Drift per feature, last run vs frozen reference', 'PSI, threshold 0.2')
    else:
        ui.waiting('No drift measurement yet.')
    ui.stats([
        number('Features drifting', 'data_drift_share * 100', '%', hint='Dataset drifts at 30%', crit=30),
        number('Dataset drift flag', 'data_drift_detected', hint='1 means drift detected', crit=1),
        number('Rows quarantined', 'pipeline_rows_quarantined', hint='Last ingestion'),
    ])
    alerts('DataDriftDetected', 'ModelPerformanceDegraded')
    ui.inbox(c.notifications({'DataDriftDetected', 'ModelPerformanceDegraded'}), 3)


def start_run(**parameters):
    try:
        st.session_state.drift_run = c.start_pipeline(**parameters)
    except requests.RequestException as exc:
        st.error(f'Cannot start the pipeline: {exc}')


def drift():
    scenario_header(3, 'Data drift',
                    'Next month the trips look different: other pickup zones, other hours, bigger groups. '
                    'The model was trained on the old world.',
                    'PSI bars turn red, the dataset flag flips to 1 and `DataDriftDetected` fires.')
    left, right = st.columns([1, 3], gap='large')
    with left:
        with st.container(border=True, key='trigger'):
            ui.heading('Trigger', step=1)
            ui.hint('Each run is the real Prefect pipeline: ingest TLC data, validate, check drift, train, promote if better.')
            if st.button('Run on shifted data', type='primary', width='stretch'):
                start_run(simulate_drift=True)
            if st.button('Run on normal data', width='stretch'):
                start_run()
            ui.hint('The alert clears after a run without drift: the pipeline publishes its last result.')
    with right:
        ui.heading('Monitoring and observability', step=2)
        live_drift()
    diagram('Reference frozen with the champion', 'PSI and KS compared per feature', 'Thresholds read from config', 'Flag raised, alert fires')
    with st.expander('Where to look next'):
        st.markdown('- **Evidently reports**: the full visual drift report of each run.\n'
                    '- **Prefect**: logs of every task of the run.\n'
                    '- **MLflow**: reference data with its SHA-256, metrics and model version.')
    pager(3)


# ---------------------------------------------------------------------------------------- 4 validation
@st.fragment(run_every=LIVE)
def live_validation():
    rejected = f'sum(rate(http_requests_total{{{API_JOB},status="422"}}[1m])) or vector(0)'
    server = f'sum(rate(http_requests_total{{{API_JOB},status=~"5.."}}[1m])) or vector(0)'
    ui.stats([
        number('Rejected per second', rejected, fmt='{:.2f}', hint='422: counted, not alarmed'),
        number('Server errors per second', server, fmt='{:.2f}', hint='5xx', crit=0.01),
        number('Rejected so far', 'sum(invalid_requests_total)'),
    ])
    ui.chart(frame_of({'accepted (200)': f'sum(rate(http_requests_total{{{API_JOB},endpoint="/fare_predict",status="200"}}[1m]))',
                       'rejected (422)': rejected, 'server errors (5xx)': server}),
             'What happens to the requests', 'req/s')
    alerts('HighErrorRate')
    if (c.safe(lambda: c.prom('sum(rate(http_requests_total{status="422"}[1m]))')) or 0) > 0.5:
        ui.hint('Many rejections, yet `HighErrorRate` stays inactive: a 422 means the client is wrong, not that we are failing. '
                'Alerting on what customers feel avoids alert fatigue.')
    ui.heading('Observability', 'Rejection logs carry field names only, never values.', step=3)
    ui.logs(c.filtered_logs(6, contains='Rejected').drop(columns=['duration_ms', 'trace_id'], errors='ignore'))


def validation():
    scenario_header(4, 'Bad data',
                    'Clients send impossible values: location 9999, 99 passengers, text instead of numbers. The contract is '
                    'checked on every request, and on every batch by the pipeline (pandera, with quarantine).',
                    'the 422 line rises, 5xx stays at zero, the error alert stays inactive, and logs name the offending **fields**.')
    left, right = st.columns([1, 3], gap='large')
    with left:
        with st.container(border=True, key='trigger'):
            ui.heading('Trigger', step=1)
            share = st.slider('Share of invalid requests', 0, 100, 50, format='%d %%')
            if st.button('Apply', type='primary', width='stretch'):
                run(lambda: c.simulator(error_rate=share / 100), f'{share}% of simulated requests are now invalid')
            if st.button('Send one invalid request', width='stretch'):
                bad = {**c.PAYLOAD, 'pu_location_id': 9999, 'passenger_count': 99}
                try:
                    response = requests.post(f'{c.API}/fare_predict', json=bad, timeout=10)
                    st.code(f'HTTP {response.status_code}\n{response.text}', language='json')
                except requests.RequestException as exc:
                    st.warning(str(exc))
            if st.button('Back to normal traffic', width='stretch'):
                run(c.simulator, 'Normal traffic restored')
            ui.hint('Defined behaviour: reject (422), count, log. Batches: bad rows are quarantined with a reason, and the run fails above 20% invalid.')
    with right:
        ui.heading('Monitoring', step=2)
        live_validation()
    ui.heading('Where rejected rows go', 'The pipeline never drops bad rows silently: they stay in PostgreSQL (`quarantine_trips`) '
               'with the check that failed. The Grafana counter is the summary; this is the evidence.', step=4)
    quarantine_view()
    pager(4)


@st.fragment(run_every='15s')
def quarantine_view():
    try:
        result = c.quarantine()
    except Exception:  # psycopg2 errors are not requests errors; the page must still render when the DB is down
        result = None
    if result is None:
        ui.waiting('Cannot read the quarantine table: PostgreSQL is unreachable.')
        return
    by_reason, sample = result
    if by_reason.empty:
        ui.waiting('Nothing quarantined yet: no invalid row in the ingested batches.')
        return
    left, right = st.columns([2, 3])
    with left:
        ui.stats([ui.stat('Rows in quarantine', f"{int(by_reason['rows'].sum()):,}")])
        ui.table(by_reason, mono=('reason',), numeric=('rows',))
    with right:
        ui.hint('Random sample. The failing value is visible, such as a negative fare.')
        ui.table(sample.head(8).rename(columns={'pu_location_id': 'pickup', 'do_location_id': 'dropoff',
                                                'passenger_count': 'passengers', 'total_fare': 'fare', 'pickup_hour': 'hour'}),
                 mono=('reason', 'pickup', 'dropoff', 'passengers', 'fare', 'hour'))


# ---------------------------------------------------------------------------------------- 5 outage
@st.fragment(run_every='2s')
def live_outage():
    state = c.safe(lambda: c.call('GET', f'{c.CONTROLLER}/services/prediction_api')['state'], 'unknown')
    up = c.safe(lambda: c.prom(f'min(up{{{API_JOB}}})'))
    health = c.safe(lambda: requests.get(f'{c.API}/health', timeout=2).status_code)
    ui.stats([
        ui.stat('Container', {'running': 'Running', 'exited': 'Stopped'}.get(state, state.capitalize()),
                tone={'running': 'good', 'exited': 'crit'}.get(state)),
        ui.stat('Prometheus scrape', '–' if up is None else f'up = {up:.0f}', tone=None if up is None else 'good' if up == 1 else 'crit'),
        ui.stat('/health', 'Unreachable' if health is None else health, tone='good' if health == 200 else 'crit'),
    ])
    ui.chart(frame_of({'up': f'min(up{{{API_JOB}}})'}, 5, 3), 'Target availability', '1 = scraped')
    alerts('ServiceDown', 'ModelNotLoaded', 'BusinessThroughputLow')
    ui.heading('On-call channel', 'Only the root cause notifies: Alertmanager inhibits the two consequences.')
    ui.inbox(c.notifications({'ServiceDown', 'ModelNotLoaded'}), 4)


def outage():
    scenario_header(5, 'An outage', 'The prediction service crashes. Customers get no quote at all.',
                    '`up` drops to 0 and `ServiceDown` fires in about 15 s. Then the cascade: `ModelNotLoaded` (about 1 min) and '
                    '`BusinessThroughputLow`. With the process gone, **only metrics from outside** can notice.')
    left, right = st.columns([1, 3], gap='large')
    with left:
        with st.container(border=True, key='trigger'):
            ui.heading('Trigger', step=1)
            if st.button('Stop the API', type='primary', width='stretch'):
                run(lambda: c.call('POST', f'{c.CONTROLLER}/services/prediction_api/stop', timeout=30))
            if st.button('Restart the API', width='stretch'):
                run(lambda: c.call('POST', f'{c.CONTROLLER}/services/prediction_api/start', timeout=30))
            ui.hint('A small `controller` service can stop or start **only** `prediction_api`. After a restart the model reloads from MLflow in about 20 s.')
    with right:
        ui.heading('Monitoring', step=2)
        live_outage()
    pager(5)


# ---------------------------------------------------------------------------------------- 6 quality
@st.fragment(run_every=LIVE)
def live_quality():
    reference = c.safe(lambda: c.prom('scalar(model_drift_rmse_reference)'))
    limit = None if reference is None else round(1.25 * reference, 2)
    ui.stats([
        number('Online RMSE', c.ONLINE_RMSE, '$', '{:.1f}', 'Predictions whose real fare is known', crit=limit),
        number('Validation RMSE', 'scalar(model_drift_rmse_reference)', '$', '{:.1f}', 'Measured at training time'),
        number('Labeled predictions', 'sum(labeled_predictions_total)', hint='Fed back about 15 s after each quote'),
    ])
    ui.chart(frame_of({'online RMSE': c.ONLINE_RMSE}), 'Online RMSE vs 1.25 × validation RMSE', 'dollars', threshold=limit)
    alerts('OnlineModelErrorHigh', 'ModelPerformanceDegraded')
    ui.inbox(c.notifications({'OnlineModelErrorHigh'}), 3)
    ui.heading('Observability', 'Which model produced these predictions?', step=3)
    ui.logs(c.filtered_logs(4))


def quality():
    scenario_header(6, 'Model quality',
                    'You only learn the true fare after the trip. The world changes (fares +100%); the model does not know.',
                    'online RMSE climbs above 1.25 × the validation RMSE and `OnlineModelErrorHigh` fires. '
                    'Drift says the *inputs* moved; this says *predictions are wrong*.')
    left, right = st.columns([1, 3], gap='large')
    with left:
        with st.container(border=True, key='trigger'):
            ui.heading('Trigger', step=1)
            if st.button('Real fares jump +100%', type='primary', width='stretch'):
                run(lambda: c.simulator(label_bias=1.0), 'Real fares now diverge from predictions, with a 15 s delay')
            if st.button('Back to normal fares', width='stretch'):
                run(lambda: c.simulator(label_bias=0.0), 'Normal fares restored')
            ui.hint('Labels are **synthetic**: prediction plus noise, plus a bias when triggered, sent to `POST /feedback` and matched by `request_id`.')
    with right:
        ui.heading('Monitoring', step=2)
        live_quality()
    quality_explainer()
    pager(6)


def quality_explainer():
    ui.heading('How to read this page', 'Where does the real fare come from?')
    diagram('A trip is priced, say $25', 'The API remembers it under its request_id',
            'About 15 s later the real fare arrives, say $31', 'Error = prediction − real fare, then RMSE',
            'Compared with the validation RMSE')
    ui.split('In this demo: synthetic',
             ['The **simulator invents** the real fare: `prediction × (1 + bias) + random noise`.',
              'Noise (about $12) is set close to the validation error, so the ratio starts near **1×**.',
              'The **+100%** button sets the bias to 1: real fares double and the model does not know.',
              'It does **not** prove the model is bad. It proves the *measurement chain and the alert work*.'],
             'In production: real',
             ['The real fare comes from **billing or the end of the trip** (the taximeter).',
              'It is joined to the prediction by an **id**: here `request_id`, in practice a trip id.',
              'It arrives **late**, from minutes to days: hence the name *delayed labels*.',
              'In the NYC TLC data this is the `fare_amount` column, used for training.'])
    ui.hint('The alert rule in one line: online RMSE above **1.25 × validation RMSE** for 30 s. The threshold follows each newly '
            'trained model instead of being a magic number. Drift looks at the **inputs**; this looks at whether the **predictions are wrong**.')


# ---------------------------------------------------------------------------------------- 7 business
@st.fragment(run_every=LIVE)
def live_business():
    ui.stats([number('Quotes per minute', QUOTES, warn=60, crit=30, below=True), number('Predicted revenue per minute', REVENUE, '$')])
    left, right = st.columns(2)
    with left:
        ui.chart(frame_of({'quotes/min': QUOTES}), 'Quotes per minute', 'quotes/min', threshold=30)
    with right:
        ui.chart(frame_of({'USD/min': REVENUE}), 'Predicted revenue per minute', 'dollars/min')
    alerts('BusinessThroughputLow', 'HighErrorRate', 'HighLatencyP99', 'ServiceDown')
    ui.inbox(c.notifications({'BusinessThroughputLow'}), 3)


def business():
    scenario_header(7, 'Business impact',
                    'The app stops sending requests: a bad release, a broken button. The API is perfectly healthy, and the business is losing quotes.',
                    'technical alerts stay inactive and only `BusinessThroughputLow` fires. A KPI tied to the model catches what infrastructure monitoring cannot.')
    left, right = st.columns([1, 3], gap='large')
    with left:
        with st.container(border=True, key='trigger'):
            ui.heading('Trigger', step=1)
            if st.button('Traffic collapses', type='primary', width='stretch'):
                run(lambda: c.simulator(running=False), 'Traffic paused')
            if st.button('Traffic back', width='stretch'):
                run(c.simulator, 'Traffic restored')
    with right:
        ui.heading('Monitoring', step=2)
        live_business()
    pager(7)
