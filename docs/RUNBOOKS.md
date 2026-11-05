# Runbooks

One short section per alert. Each section answers: what does it mean, how do I confirm it, what do I do.
Alert rules live in `services/prometheus/alert_rules.yml` and point here through `runbook_url`.

## HighErrorRate
- **Meaning:** more than 5% of API responses are 5xx (server failures, not client mistakes).
- **Confirm:** Grafana *Is it OK?* → Errors panel; Loki: `{service="prediction_api"} | json | level="ERROR"`.
- **Act:** use the `request_id` of a failing log line to find the trace; roll back the champion alias in MLflow if it started after a promotion.

## HighLatencyP99
- **Meaning:** 1% of `/fare_predict` calls take more than 1 s (normal: ~0.1 s).
- **Confirm:** Grafana *RED* panel (p50/p95/p99); open a slow trace in Tempo.
- **Act:** find the slow span; restart or scale the API; remove any injected latency (`PUT /admin/chaos {"latency_ms": 0}`).

## ServiceDown
- **Cascade:** while the API is down, `ModelNotLoaded` (~1 min) and `BusinessThroughputLow` also fire in Prometheus. Alertmanager *inhibits* their notifications so only this root cause pages.
- **Meaning:** Prometheus cannot scrape a target for 15 s.
- **Act:** `docker compose ps`, `docker compose logs <service>`, `docker compose up -d <service>`.

## ModelNotLoaded
- **Meaning:** the API has no champion model; predictions return 503 and `/health` is red.
- **Act:** check the `champion` alias in MLflow; run the Prefect flow `nyc-taxi-demo` if no model exists.

## DataDriftDetected
- **Meaning:** at least 30% of features (threshold in `config/monitoring.yml`) moved away from the frozen reference (PSI ≥ 0.2 or KS ≥ 0.2).
- **Act:** read the per-feature PSI in Grafana, open the Evidently report, check the upstream source, then retrain on recent data. The alert clears after a run without drift.

## OnlineModelErrorHigh
- **Meaning:** now that real fares have arrived, the online RMSE is more than 25% above the validation RMSE.
- **Act:** compare recent predictions with real fares, retrain with fresh labels, promote only if the candidate beats the champion.

## ModelPerformanceDegraded
- **Meaning:** the candidate trained by the last run is 20% worse than the champion on the same validation set.
- **Act:** the champion is kept automatically; inspect data quality (quarantine count) of the last run.

## BusinessThroughputLow
- **Meaning:** fewer than 30 fares are quoted per minute (usual demo level ~120).
- **Act:** check whether the app/simulator is still sending traffic, then the 422 and 5xx rates.
