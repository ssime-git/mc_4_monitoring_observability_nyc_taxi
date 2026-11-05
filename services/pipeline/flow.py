"""Automatic demo: real TLC data -> SQL -> Evidently -> MLflow -> MinIO."""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import requests
from evidently.metric_preset import DataDriftPreset
from evidently.report import Report
from mlflow import MlflowClient
from prefect import flow, task
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import taxi_drift  # noqa: E402
import taxi_validation  # noqa: E402

FEATURES = ['vendor_id', 'rate_code_id', 'pu_location_id', 'do_location_id',
            'passenger_count', 'pickup_hour', 'pickup_dayofweek', 'pickup_month', 'pickup_weekday']
MODEL_NAME = 'NYC_Taxi_Fare_Model'
EXPERIMENT = 'NYC_Taxi_Fare_Prediction_MinIO'


def prepare(raw):
    return prepare_with_quarantine(raw)[0]


def prepare_with_quarantine(raw):
    df = raw.rename(columns={'VendorID': 'vendor_id', 'RatecodeID': 'rate_code_id',
        'PULocationID': 'pu_location_id', 'DOLocationID': 'do_location_id', 'Airport_fee': 'airport_fee'}).copy()
    required = ['vendor_id', 'rate_code_id', 'pu_location_id', 'do_location_id',
                'passenger_count', 'tpep_pickup_datetime', 'fare_amount']
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f'Missing required columns: {sorted(missing)}')
    dt = pd.to_datetime(df['tpep_pickup_datetime'], errors='coerce')
    df['pickup_hour'], df['pickup_dayofweek'], df['pickup_month'] = dt.dt.hour, dt.dt.dayofweek, dt.dt.month
    df['pickup_weekday'] = (dt.dt.dayofweek < 5).astype(int)
    df['total_fare'] = pd.to_numeric(df['fare_amount'], errors='coerce')
    for col in ['extra', 'mta_tax', 'improvement_surcharge', 'tolls_amount', 'airport_fee']:
        if col in df:
            df['total_fare'] += pd.to_numeric(df[col], errors='coerce').fillna(0)
    # Invalid rows are quarantined with a reason (pandera). Too many invalid rows means the
    # source itself is broken, so the flow fails loudly instead of training on what is left.
    cfg = taxi_drift.load_config()['validation']
    df = df[FEATURES + ['total_fare']].apply(pd.to_numeric, errors='coerce')
    valid, quarantined = taxi_validation.split_valid_invalid(df)
    if len(quarantined) / max(len(df), 1) > cfg['max_invalid_share']:
        raise ValueError(f"More than {cfg['max_invalid_share']:.0%} of rows fail quality validation")
    df = valid.reset_index(drop=True)
    if len(df) < cfg['min_valid_rows']:
        raise ValueError(f"At least {cfg['min_valid_rows']} valid trips are required")
    return df, quarantined


@task(retries=3, retry_delay_seconds=10, persist_result=False)
def ingest(year, month, limit):
    if not 1 <= month <= 12 or limit < 100:
        raise ValueError('Invalid month or row limit')
    url = f'https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{year}-{month:02d}.parquet'
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'trips.parquet'
        with requests.get(url, stream=True, timeout=(15, 300)) as response:
            response.raise_for_status()
            with path.open('wb') as dest:
                for chunk in response.iter_content(1024 * 1024):
                    dest.write(chunk)
        raw = next(pq.ParquetFile(path).iter_batches(batch_size=limit)).to_pandas()
    df, quarantined = prepare_with_quarantine(raw)
    df.attrs['quarantined_rows'] = len(quarantined)
    # A source + sampling key makes retries and restarts idempotent.
    source = hashlib.sha256(f'{url}:{limit}:v1'.encode()).hexdigest()
    engine = create_engine(os.environ['DATABASE_URL'])
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE IF NOT EXISTS ingestion_batches (source VARCHAR(64) PRIMARY KEY)'))
            exists = conn.execute(text('SELECT source FROM ingestion_batches WHERE source=:source'), {'source': source}).first()
            if not exists:
                stored = df.assign(source=source)
                stored.to_sql('training_trips', conn, if_exists='append', index=False, method='multi', chunksize=500)
                if len(quarantined):  # defined behaviour for bad rows: kept aside with the reason, never lost silently
                    quarantined.assign(source=source).to_sql('quarantine_trips', conn, if_exists='append', index=False)
                conn.execute(text('INSERT INTO ingestion_batches VALUES (:source)'), {'source': source})
    finally:
        engine.dispose()
    return df


@task(persist_result=False)
def check_drift(current, client, directory):
    try:
        champion = client.get_model_version_by_alias(MODEL_NAME, 'champion')
    except mlflow.exceptions.MlflowException as exc:
        if exc.error_code != 'RESOURCE_DOES_NOT_EXIST' and not ('alias' in str(exc).lower() and 'not found' in str(exc).lower()):
            raise
        return {'baseline': True, 'drift_detected': False, 'drift_share': 0.0, 'features': {}}
    reference_file = client.download_artifacts(champion.run_id, 'reference/reference.parquet', directory)
    reference = pd.read_parquet(reference_file)
    # Decision = our per-feature PSI/KS with thresholds from config/monitoring.yml.
    # Evidently is kept for the human-readable HTML report.
    decision = taxi_drift.feature_drift(reference, current, FEATURES)
    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference[FEATURES], current_data=current[FEATURES])
    report.save_html(str(Path(directory) / 'drift.html'))
    report.save_json(str(Path(directory) / 'drift.json'))
    return {'baseline': False, 'drift_detected': decision['drift_detected'], 'drift_share': decision['drift_share'],
            'features': decision['features'], 'reference_run_id': champion.run_id}


@task(persist_result=False)
def train_and_register(current, client, directory, drift):
    engine = create_engine(os.environ['DATABASE_URL'])
    try:
        data = pd.read_sql_table('training_trips', engine)[FEATURES + ['total_fare']]
    finally:
        engine.dispose()
    X_train, X_test, y_train, y_test = train_test_split(data[FEATURES], data.total_fare, test_size=.2, random_state=42)
    candidate = RandomForestRegressor(n_estimators=int(os.getenv('N_ESTIMATORS', '50')), max_depth=8, random_state=42)
    candidate.fit(X_train, y_train)
    prediction = candidate.predict(X_test)
    rmse = float(mean_squared_error(y_test, prediction) ** .5)
    # Evaluate both models on the SAME validation data.
    try:
        champion = mlflow.sklearn.load_model(f'models:/{MODEL_NAME}@champion')
        champion_rmse = float(mean_squared_error(y_test, champion.predict(X_test)) ** .5)
    except mlflow.exceptions.MlflowException as exc:
        if exc.error_code != 'RESOURCE_DOES_NOT_EXIST' and not ('alias' in str(exc).lower() and 'not found' in str(exc).lower()):
            raise
        champion_rmse = None
    info = mlflow.sklearn.log_model(candidate, artifact_path='model', registered_model_name=MODEL_NAME,
                                  signature=mlflow.models.infer_signature(X_train, candidate.predict(X_train)))
    version = info.registered_model_version
    promoted = champion_rmse is None or rmse < champion_rmse
    mlflow.log_metrics({'rmse': rmse, 'r2': float(r2_score(y_test, prediction)),
                       'data_drift_detected': int(drift['drift_detected']), 'data_drift_share': drift['drift_share']})
    mlflow.log_params({'training_rows': len(X_train), 'validation_rows': len(X_test), 'n_estimators': candidate.n_estimators})
    mlflow.log_dict(drift, 'drift/summary.json')
    if not drift['baseline']:
        mlflow.log_artifact(str(Path(directory) / 'drift.html'), 'drift')
        mlflow.log_artifact(str(Path(directory) / 'drift.json'), 'drift')
    current.to_parquet(Path(directory) / 'reference.parquet', index=False)
    mlflow.log_artifact(str(Path(directory) / 'reference.parquet'), 'reference')
    # The reference is frozen with the model version it belongs to; its hash makes it verifiable.
    mlflow.set_tags({'reference_sha256': hashlib.sha256((Path(directory) / 'reference.parquet').read_bytes()).hexdigest(),
                     'model_version': str(version)})
    # Promotion happens only once all artifacts are safely persisted.
    if promoted:
        client.set_registered_model_alias(MODEL_NAME, 'champion', version)
    mlflow.set_tag('promoted_to_champion', str(promoted))
    return {'version': version, 'promoted': promoted, 'rmse': rmse, 'champion_rmse': champion_rmse, 'rows': len(data)}


def inject_drift(df):
    """Demo scenario: simulate an upstream change (new zones, other hours, bigger groups).
    Applied to the data compared with the reference only; training data and the stored reference stay untouched."""
    shifted = df.copy()
    shifted['pu_location_id'] = (shifted.pu_location_id + 100).clip(upper=265)
    shifted['pickup_hour'] = (shifted.pickup_hour + 8) % 24
    shifted['passenger_count'] = (shifted.passenger_count + 2).clip(upper=6)
    return shifted


@flow(name='nyc-taxi-demo', log_prints=True, persist_result=False)
def taxi_pipeline(year=2024, month=1, limit=5000, simulate_drift=False):
    current = ingest(year, month, limit)
    mlflow.set_tracking_uri(os.environ['MLFLOW_TRACKING_URI'])
    mlflow.set_experiment(EXPERIMENT)
    client = MlflowClient()
    with mlflow.start_run() as run, tempfile.TemporaryDirectory() as tmp:
        mlflow.set_tags({'source_month': f'{year}-{month:02d}', 'pipeline': 'prefect'})
        drift = check_drift(inject_drift(current) if simulate_drift else current, client, tmp)
        result = train_and_register(current, client, tmp, drift)
        gateway = os.getenv('PUSHGATEWAY_URL')
        if gateway:
            registry = CollectorRegistry()
            for name, value in [('data_drift_detected', int(drift['drift_detected'])), ('data_drift_share', drift['drift_share']), ('pipeline_training_rows', result['rows']), ('model_drift_rmse_current', result['rmse']), ('model_drift_rmse_reference', result['champion_rmse'] if result['champion_rmse'] is not None else result['rmse'])]:
                Gauge(name, name, registry=registry).set(value)
            per_feature = {n: Gauge(n, n, ['feature'], registry=registry) for n in
                           ('feature_drift_psi', 'feature_drift_ks', 'feature_drifted')}
            for feature, f in drift['features'].items():
                per_feature['feature_drift_psi'].labels(feature).set(f['psi'])
                per_feature['feature_drift_ks'].labels(feature).set(f['ks'])
                per_feature['feature_drifted'].labels(feature).set(int(f['drifted']))
            Gauge('pipeline_rows_quarantined', 'Rows rejected by validation in the last ingestion', registry=registry).set(
                current.attrs.get('quarantined_rows', 0))
            Gauge('pipeline_champion_version', 'Model version trained by the last run', registry=registry).set(int(result['version']))
            push_to_gateway(gateway, job='taxi_pipeline', registry=registry)
        print(f'Run {run.info.run_id}: {result}')
        return result


if __name__ == '__main__':
    taxi_pipeline()
