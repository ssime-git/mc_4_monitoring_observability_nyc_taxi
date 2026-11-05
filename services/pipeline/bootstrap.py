"""Register a Docker deployment and submit its first run on Compose startup."""
import asyncio
import os
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect_docker import DockerWorker
from flow import taxi_pipeline


async def bootstrap():
    async with get_client() as client:
        await client.create_work_pool(WorkPoolCreate(name='taxi-docker', type='docker',
            base_job_template=DockerWorker.get_default_base_job_template()), overwrite=True)
    deployment_flow = await taxi_pipeline.from_source(
        source='/app', entrypoint='services/pipeline/flow.py:taxi_pipeline'
    )
    deployment_id = await deployment_flow.deploy(name='demo', work_pool_name='taxi-docker', image='nyc-taxi-pipeline:demo',
             build=False, push=False, interval=86400, concurrency_limit=1,
             parameters={'limit': int(os.getenv('DEMO_ROWS', '5000'))},
             job_variables={'auto_remove': True, 'image_pull_policy': 'Never',
                'networks': ['nyc-taxi-ml'],
                'env': {key: os.environ[key] for key in
                    ['PREFECT_API_URL', 'DATABASE_URL', 'MLFLOW_TRACKING_URI', 'PUSHGATEWAY_URL']}})
    async with get_client() as client:
        await client.create_flow_run_from_deployment(deployment_id)


if __name__ == '__main__':
    asyncio.run(bootstrap())
