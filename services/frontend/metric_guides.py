"""Plain-language explanations tied to the metrics actually exported by the demo."""
from html import escape

import streamlit as st

GUIDED_PAGES = ('Grafana', 'Prometheus', 'MLflow', 'Evidently reports')


def diagram(*steps):
    boxes = '<span aria-hidden="true">→</span>'.join(
        f'<div class="metric-step">{escape(step)}</div>' for step in steps)
    st.html('''<style>
    .metric-flow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0 20px}
    .metric-step{flex:1;min-width:125px;padding:14px;border:1px solid #6483a5;
    border-top:3px solid #f08c46;border-radius:8px;font-size:14px}
    </style><div class="metric-flow" role="group" aria-label="How the measurement is obtained">'''+boxes+'</div>')


BUSINESS = [
    ('Quotes per minute', 'Prometheus measures how fast the `prediction_requests_total` counter grows over 1 minute, times 60.',
     '120/min means about 2 successful quotes per second. Failures are not counted. Below 30/min the business alert fires.'),
    ('Predicted revenue', 'The sum of all predicted fares (`total_predicted_revenue_dollars_total`).',
     'A volume proxy, not money actually collected. It follows traffic and model behaviour.'),
    ('Fare distribution', 'Each fare increments cumulative buckets: ≤5 $, ≤10 $, ≤15 $…',
     'Shows whether prices concentrate or move toward high values. A shift can come from different trips or a new model.'),
]
SYSTEM = [
    ('Request rate', 'The middleware counts requests by route, method and status; `rate()` turns the counter into requests per second.',
     'Includes /health and /metrics, so total HTTP rate differs from the number of predictions.'),
    ('Status codes', 'Each response is classified by its HTTP code.',
     '200 served · 422 invalid input (client mistake) · 503 model unavailable · 500 inference failure.'),
    ('Error rate (5xx)', 'Responses 500–599 divided by all responses over the same window.',
     '5 errors out of 100 = 5 %. 422s are excluded. With no traffic, zero proves nothing.'),
    ('Latency p50 / p95 / p99', 'The API records each duration in a histogram; Prometheus estimates percentiles from the buckets.',
     'p99 = 200 ms: about 99 % of requests are faster. It is neither the maximum nor an average — averages hide slow tails.'),
    ('Logs', 'Promtail ships container output to Loki. Every API line is JSON with `request_id`, `trace_id`, `model_version`.',
     'Read them for the *why* behind a metric. An empty ERROR panel can simply mean no error.'),
    ('Traces', 'OpenTelemetry records timed steps linked by a trace id, stored in Tempo.',
     'A trace locates the slow step of one request. The same `trace_id` is in the log line.'),
]
MODEL = [
    ('Drift per feature (PSI)', 'Compares the distribution of each feature with the frozen reference. PSI ≥ 0.2 (or KS ≥ 0.2) flags the feature.',
     'Thresholds live in `config/monitoring.yml`. The dataset drifts when ≥ 30 % of features drift.'),
    ('Validation RMSE', 'Square each error, average, take the root — on the same validation set for candidate and champion.',
     'Lower is better. RMSE of 8 $ penalises large errors; it is not the mean absolute error.'),
    ('Online RMSE', 'Same formula on live predictions once the real fare is known (`POST /feedback`).',
     'Compared with the validation RMSE: a ratio above 1.25 raises `OnlineModelErrorHigh`.'),
    ('R² in MLflow', 'Compares the model error with a constant prediction equal to the mean fare.',
     '1 perfect · 0 as good as the mean · negative worse. It is not a percentage of correct predictions.'),
    ('Alerts', 'Prometheus evaluates rules; a condition must hold for the configured `for:` duration.',
     'Pending = watching · firing = notified through Alertmanager. Every alert carries an action and a runbook.'),
]


def table(rows):
    titles = [row[0] for row in rows]
    selected = st.selectbox('Which metric do you want to understand?', titles, key='guide_' + titles[0])
    title, calculation, reading = rows[titles.index(selected)]
    st.markdown(f'#### {title}')
    st.markdown(f'**How it is obtained:** {calculation}')
    st.info(f'**How to read it:** {reading}')


def render_guide(page):
    if page not in GUIDED_PAGES:
        return
    with st.expander('Understand the metrics: calculation, diagrams and interpretation', expanded=False):
        st.caption('Teaching examples: the numbers below are not the live values of the dashboard.')
        if page == 'Grafana':
            diagram('A request arrives', 'Timing and status code recorded', 'Counters and histograms in /metrics',
                    'Prometheus computes rates, ratios and percentiles')
            table(SYSTEM + BUSINESS)
            st.markdown('**Example:** 100 requests with 5 responses 500 → **5 % server errors**. A duration of 0.2 s is 200 ms.')
        elif page == 'Prometheus':
            diagram('Counters and histograms', 'Scraped every 5 s', 'PromQL turns them into rates', 'Rules become alerts')
            table(SYSTEM)
            st.code('rate(prediction_requests_total[1m]) * 60', language='promql')
            st.caption('Read: estimate the counter speed over the last minute, expressed per minute.')
        else:
            diagram('Validated batch', 'Compared with the champion reference', 'Candidate and champion scored on the same trips',
                    'MLflow stores results and artefacts')
            table(MODEL)
            st.markdown('**RMSE example:** errors of +3 $ and −4 $ → squares 9 and 16 → mean 12.5 → root ≈ **3.54 $**.')
            st.info('On the very first run there is no reference, so drift shows 0 (“baseline”), which is not proof of no drift. '
                    'Pushgateway keeps the last published result, so a visible value may come from an older run: check Prefect.')
