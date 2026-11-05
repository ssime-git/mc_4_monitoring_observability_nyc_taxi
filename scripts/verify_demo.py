"""Real integration verification against the running demo (no mocks)."""
import time
import boto3
import mlflow
import requests

mlflow.set_tracking_uri('http://localhost:5000')
client = mlflow.MlflowClient()
model_name = 'NYC_Taxi_Fare_Model'


def wait_for_model(timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return client.get_model_version_by_alias(model_name, 'champion')
        except mlflow.exceptions.MlflowException:
            time.sleep(3)
    raise TimeoutError('No champion registered within 10 minutes; inspect Prefect logs')


def second_run():
    api = 'http://localhost:4200/api'
    response = requests.get(f'{api}/deployments/name/nyc-taxi-demo/demo', timeout=15)
    response.raise_for_status()
    deployment = response.json()
    response = requests.post(f"{api}/deployments/{deployment['id']}/create_flow_run", json={}, timeout=15)
    response.raise_for_status()
    run_id = response.json()['id']
    for _ in range(120):
        response = requests.get(f'{api}/flow_runs/{run_id}', timeout=15)
        response.raise_for_status()
        current = response.json()
        if current['state_type'] in ('COMPLETED', 'FAILED', 'CRASHED', 'CANCELLED'):
            assert current['state_type'] == 'COMPLETED', current['state']
            return
        time.sleep(3)
    raise TimeoutError('Second Prefect run did not finish')


if __name__ == '__main__':
    champion = wait_for_model()
    print(f'Champion registered: version {champion.version}', flush=True)
    s3 = boto3.client('s3', endpoint_url='http://localhost:9000',
                      aws_access_key_id='minioadmin', aws_secret_access_key='minioadmin')
    objects = s3.list_objects_v2(Bucket='mlflow-artifacts')['Contents']
    assert any(o['Key'].endswith('model.pkl') for o in objects)
    model = mlflow.sklearn.load_model(f'models:/{model_name}@champion')
    assert model is not None
    payload = dict(vendor_id=1, rate_code_id=1, pu_location_id=1, do_location_id=2,
                   passenger_count=1, pickup_hour=12, pickup_dayofweek=1, pickup_month=1, pickup_weekday=1)
    response = requests.post('http://localhost:8000/fare_predict', json=payload, timeout=30)
    response.raise_for_status()
    assert response.json()['predicted_fare'] > 0
    print('MinIO artifact and API prediction verified', flush=True)
    second_run()
    experiment = client.get_experiment_by_name('NYC_Taxi_Fare_Prediction_MinIO')
    latest = client.search_runs([experiment.experiment_id], order_by=['attributes.start_time DESC'], max_results=1)[0]
    assert latest.info.status == 'FINISHED'
    assert client.get_model_version_by_alias(model_name, 'champion').version == champion.version, 'Identical data should retain the champion'
    report = mlflow.artifacts.load_text(f'runs:/{latest.info.run_id}/drift/drift.html')
    assert '<html' in report.lower()
    print(f'Second flow and Evidently HTML report verified: {latest.info.run_id}', flush=True)
