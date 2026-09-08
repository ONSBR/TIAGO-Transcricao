import boto3
import json
from time import sleep
import os

def movimentador(key, path_target):
    gateway_bucket = os.getenv("GATEWAY_BUCKET")
    session = boto3.Session()
    s3 = session.resource('s3')
    file_name = key.split('/')[2]
    copy_source = {
        'Bucket': gateway_bucket,
        'Key': f'{key}'
    }
    bucket = s3.Bucket(os.getenv("AUDIO_BUCKET_NAME"))
    bucket.copy(copy_source, f'{os.getenv("CONTROL_ROOM_KEY_PATH")}{path_target}/{file_name}')
    sleep(3)
    client = boto3.client('s3', region_name = 'us-east-1')
    client.delete_object(Bucket=gateway_bucket, Key=key)
    print('Arquivo:', file_name, 'movido com sucesso!')

def lambda_handler(event, context):
    print('EVENTO: ', event)
    valid_locations = [c.strip() for c in os.getenv("LOCATION_CODES", "").split(",") if c.strip()]
    try:
        path_target = json.loads(event['Records'][0]['Sns']['Message'])['Records'][0]['s3']['object']['key'].split('/')[1]
    except:
        print('Evento não previsto para esse processo!')
        path_target = None

    if path_target in valid_locations:
        key = json.loads(event['Records'][0]['Sns']['Message'])['Records'][0]['s3']['object']['key']
        print('Key: ', key)
        print('Path target: ', path_target)
        movimentador(key, path_target)
    else:
        pass