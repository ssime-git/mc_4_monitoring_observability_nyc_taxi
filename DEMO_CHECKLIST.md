# Demo checklist

Reopen this before every session. **Before the session:** `make up`, wait ~1–2 min until `curl localhost:8000/health` returns 200,
open http://localhost:8501, click *Start here → Reset to a healthy baseline*. **Quick proof the stack is healthy:** `make test` and `make demo-normal`.

Legend: ✅ OK · 🔧 fixed in this audit · ➖ not applicable · ⚠️ known limit (see “Gaps”).

## 1 · Checklist

| # | Point | Before | After | Proof |
|---|---|---|---|---|
| **1 Infra & service** | | | | |
| 1.1 | RED metrics on `/metrics` | ✅ | ✅ | `services/prediction_api/app.py:53` (`http_requests_total`), `tests/test_integration.py::test_traffic_shows_up_in_prometheus_with_red_metrics` |
| 1.2 | Latency as histogram (p50/p95/p99) | ✅ | ✅ | `app.py:60` buckets 5 ms–10 s; PromQL in `story.py` / dashboard L0 panel “Latency p50 / p95 / p99” |
| 1.3 | `/health` really checks the model | to fix: always 200 | 🔧 503 without model, runs a smoke prediction | `app.py` `def health`; `tests/test_api.py::test_health_*`; container `healthcheck` in `docker-compose.yml` |
| 1.4 | Bounded label cardinality | to fix: raw URL path as label | 🔧 route template, else `unmatched` | `app.py:167`; `test_endpoint_label_cardinality_is_bounded`; integration `test_endpoint_label_has_no_unbounded_values` |
| 1.5 | `scrape_interval` coherent | ✅ 5 s, undocumented | 🔧 documented: outage seen in ≈15 s, rule windows ≥ 12× interval | `services/prometheus/prometheus.yml:2-6` |
| **2 Data** | | | | |
| 2.1 | Schema and value validation | partial (hand-written mask, none at the API) | 🔧 pandera on batches (types, bounds, nulls) + Pydantic bounds at the API, same contract | `services/pipeline/taxi_validation.py`; `RequestData` in `app.py`; `test_api_bounds_match_the_pipeline_schema`, `tests/test_drift_validation.py` |
| 2.2 | Defined behaviour on invalid data | to fix: rows dropped silently | 🔧 API: 422 + `invalid_requests_total` + WARNING log (field names only). Batch: quarantine table with reason + `pipeline_rows_quarantined`; >20% invalid = flow fails | `app.py:185`; `flow.py:92`; `test_validation_quarantines_bad_rows_with_reason` |
| **3 Model** | | | | |
| 3.1 | Frozen, versioned drift reference | partial (parquet per run, no integrity) | 🔧 reference stored with the champion run in MLflow/MinIO, `reference_sha256` + `model_version` tags | `flow.py:154` |
| 3.2 | Per-feature PSI/KS, thresholds in config | to fix: dataset-level, Evidently defaults | 🔧 PSI and KS per feature; thresholds in `config/monitoring.yml` | `services/pipeline/taxi_drift.py`; `test_no_drift_…`, `test_injected_drift_…`, `test_thresholds_come_from_config_not_from_code` |
| 3.3 | Performance tracked when labels arrive | to fix: offline RMSE only | 🔧 `POST /feedback` → online RMSE (counters) vs validation RMSE; alert `OnlineModelErrorHigh` | `app.py:356`; `test_feedback_updates_online_rmse_counters`; scenario `make demo-performance` |
| 3.4 | Model version in logs and metrics | partial (gauge only) | 🔧 every log line has `model_version`; gauge `model_version{version}`; `/health` returns it | `JSONFormatter` in `app.py:24`; `test_logs_are_json_with_correlation_and_model_version_and_no_payload` |
| **4 LLM / RAG** | | | | |
| 4.1–4.3 | Tokens, cost, RAGAS/DeepEval, step traces | ➖ | ➖ | No LLM or RAG component exists in this demo; adding one only to tick the box would be invented code. |
| **5 Business** | | | | |
| 5.1 | Business KPI tied to the model, on a dashboard | ✅ L1 only | 🔧 quotes/min and predicted revenue/min on L0 + L1, with an alert | `L0_status.json` panels “Quotes / min”, “Predicted revenue per minute”; `BusinessThroughputLow` |
| **6 Observability** | | | | |
| 6.1 | Structured JSON logs with level, timestamp, correlation id, model version | partial (no id, no version) | ✅ API and simulator; ⚠️ Prefect task logs keep Prefect's own format | `app.py:24`; Streamlit “Logs & traces” page |
| 6.2 | No personal data or secret in logs | partial (`str(exception)` logged) | 🔧 only field names / exception types; trip values never logged; credentials moved to `.env` | `app.py` (`reject_invalid_request`, prediction error log); `test_logs_are_json_…` asserts no payload field in the line |
| 6.3 | Request id linking logs, metrics, traces | to fix: only `trace_id` | 🔧 `X-Request-ID` (accepted or generated) in header, log line, trace id; shown end to end on Streamlit page 1 | `tests/test_api.py::test_valid_request_returns_prediction_and_request_id`; ⚠️ no Prometheus exemplars |
| **7 Alerts** | | | | |
| 7.1 | Rules in versioned files, loaded by the demo | ✅ | ✅ | `services/prometheus/alert_rules.yml`; `test_prometheus_loads_rules_and_talks_to_alertmanager` |
| 7.2 | Threshold justified, `for:`, severity, actionable message | partial (no justification, no action) | 🔧 comment per rule, `for:` on all, severity, `action` + `runbook_url` | `test_every_alert_has_duration_severity_message_and_action`; `docs/RUNBOOKS.md` |
| 7.3 | Alertmanager with a test receiver | to fix: absent | 🔧 Alertmanager → `alert_webhook` (fake Slack), shown in Streamlit | `services/alertmanager/alertmanager.yml`; `tests/test_alerts.py::test_alertmanager_has_a_test_receiver` |
| 7.4 | One alert per layer (p99, errors, drift, business) | partial (no business alert; thresholds unusable in a demo) | 🔧 `HighLatencyP99`, `HighErrorRate`, `DataDriftDetected`, `BusinessThroughputLow` (+ `ServiceDown`, `ModelNotLoaded`, `OnlineModelErrorHigh`) | `test_each_layer_is_covered`; promtool unit tests `services/prometheus/tests/alert_rules_test.yml` |
| 7.5 | No alert without an action | to fix | 🔧 `action` annotation + runbook section enforced by a test | `test_every_alert_has_…` |
| **8 Dashboards** | | | | |
| 8.1 | Provisioned from files | ✅ | ✅ | `services/grafana/provisioning/`; `test_dashboards_are_provisioned_from_files` (checks `provisioned: true` through Grafana's API) |
| 8.2 | “Is it OK?” in < 10 s | to fix: none | 🔧 **L0**: 6 colour-coded tiles, then RED / model / business / alerts rows | `services/grafana/dashboards/L0_status.json`; also the “Start here” strip in Streamlit |
| 8.3 | Units and thresholds on every panel | partial | 🔧 units on all data panels (tested); threshold lines on latency, errors, drift, business | `test_every_data_panel_declares_a_unit_…` |
| **9 Reproducibility** | | | | |
| 9.1 | One command up, one command down | partial (manual `.env`, undocumented) | 🔧 `make up` / `make down` (`make reset` erases data); verified from an empty state, first model after ≈ 60 s | `Makefile` |
| 9.2 | Pinned images and dependencies | partial | 🔧 Python base images, Postgres, Go and Debian build images by digest; third-party images by exact tag; Python deps by `uv.lock` (`--frozen`) | `services/*/Dockerfile`, `docker-compose.yml` |
| 9.3 | README: prerequisites, launch, URLs, demo credentials | partial (French, no Alertmanager) | 🔧 English README with URL/credential table | `README.md` |

## 2 · Tests (last full run, stack up: **59 passed, 1 skipped**; the skipped test is the slow end-to-end wrapper, covered by `make demo-all`)

| Level | What | Where |
|---|---|---|
| Unit | data validation, quarantine, drift without/with injected drift, thresholds read from config, API contract, logs, alert rules (structure + promtool) | `tests/test_drift_validation.py`, `test_api.py`, `test_alerts.py`, `test_pipeline.py` |
| Portal | login, every page renders even when all backends are down | `tests/test_portal.py`, `tests/test_metric_guides.py` |
| Integration | traffic → Prometheus, bounded labels, `/health`, rules loaded, Alertmanager connected, dashboards provisioned | `tests/test_integration.py` (auto-skipped without the stack) |
| Scenarios | the four required + two bonus, each measuring the alert delay | `make demo-*` → `scripts/demo_scenarios.py` |

## 3 · Scenario results (clean start, measured)

Delays are seconds **after the fault is in place** (for `drift` that is after the pipeline run completes, ≈ 105 s of pipeline time earlier).

| # | Scenario (`make …`) | Fault | Alert | Prometheus firing | Alertmanager | Webhook notification | Resolved after fix |
|---|---|---|---|---|---|---|---|
| 1 | `demo-normal` | normal traffic (2 req/s, 10 % invalid inputs) | none for 90 s | – | – | – | – |
| 2 | `demo-latency` | +1.5 s injected | `HighLatencyP99` | 36 s | 36 s | 40 s | 56 s |
| 3 | `demo-drift` | pipeline run on shifted data | `DataDriftDetected` | 22 s | 22 s | 26 s | 4 s (after a clean run) |
| 4 | `demo-outage` | `prediction_api` stopped | `ServiceDown` | 12 s | 12 s | 18 s | 6 s |
| 5 | `demo-performance` (bonus) | real fares +100 % | `OnlineModelErrorHigh` | 64 s | 64 s | 68 s | 110 s |
| 6 | `demo-business` (bonus) | traffic paused | `BusinessThroughputLow` | 76 s | 76 s | 80 s | 20 s |

Run: `make reset && make up`, first model after 60 s, then `scripts/demo_scenarios.py all` → “All requested scenarios passed.”
Why these delays: Prometheus `for:` (15–30 s) + 1-minute rate windows; Alertmanager adds a 5 s `group_wait`.

## 4 · Gaps not fixed

- ➖ **LLM / RAG** checks (section 4): nothing to monitor in this demo.
- ⚠️ **Prefect task logs** are not JSON and carry no `model_version`; they remain in Prefect's UI/worker output. Rewriting Prefect's logging was out of scope.
- ⚠️ **No Prometheus exemplars**: logs and traces share `request_id`/`trace_id`, but you cannot click from a metric point to a trace. Needs OpenMetrics exemplars plus Grafana configuration.
- ⚠️ **Delayed labels are synthetic** (prediction + noise, plus a bias on demand). Real ground truth would come from billing.
- ⚠️ **Alert receiver is a fake webhook.** A real Slack/PagerDuty receiver needs a secret that I did not invent; swap the receiver in `alertmanager.yml` and add the secret to `.env`.
- ⚠️ **Fault injection and controller** (`CHAOS_ENABLED`, `controller` with the Docker socket) are demo-only and must not reach a real environment.
- ⚠️ **Pushgateway gauges** describe the last pipeline run, so `DataDriftDetected` stays on until a clean run. Documented in the runbook and README.
- ⚠️ Streamlit pages are tested for rendering (and checked by hand with Chromium screenshots in both themes), not by an automated browser end-to-end test.
- ⚠️ The sidebar theme switch drives Streamlit's own menu because Streamlit has no Python API for it. If an upgrade breaks it, the top-right menu still switches theme.
- ⚠️ The simulator sends requests one at a time, so injected latency also lowers throughput: at 1500 ms `BusinessThroughputLow` can fire after `HighLatencyP99`. It is a real knock-on effect, worth saying out loud.
- ⚠️ Evidently `DataDriftPreset` still produces the HTML report; the *decision* now comes from our PSI/KS code, so the two can disagree on borderline features.

## 5 · Commands

```bash
make up && make test && make demo-all   # full proof from scratch (≈ 25 min with demo-all)
```
