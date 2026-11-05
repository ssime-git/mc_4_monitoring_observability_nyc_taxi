import time
import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

client = boto3.client('s3', endpoint_url='http://minio:9000')
for attempt in range(60):
    try:
        client.create_bucket(Bucket='mlflow-artifacts')
        break
    except ClientError as exc:
        if exc.response['Error']['Code'] in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            break
        raise
    except EndpointConnectionError:
        if attempt == 59:
            raise
        time.sleep(2)
