import json
import logging
# import boto3
# import os
from helpers.environment_helper import EnvironmentHelper
from repositories.opensearch_repository import OpenSearchRepository
from helpers.utils_helper import (
    convert_to_timestamp,
    convert_data_hora_string_to_data_hora_datetime,
)
from boto3 import client
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# URL base da sua API de transcrição
TRANSCRIPTION_API_BASE_URL = (
    EnvironmentHelper.get_transcription_api_base_url()
)  # Ajuste conforme necessário


async def get_vocabulary(key, transcribe_client, context):
    """
    Não há vocabulário na nova API. Simplesmente retornamos o key.
    """
    logger.info(
        f"get_vocabulary chamada, mas API não possui vocabulário. Retornando key: {key}"
    )
    return key


async def create_job(raw_key, key, context):
    # """
    # Envia um job para a sqs, onde existem workers da API monitorando para executá-los, e registra a entrada no dynamo.
    # """

    # # cria uma entrada no dynamo para o áudio como pending.
    # job_id = os.path.splitext(os.path.basename(key))[0]
    # bucket_name = EnvironmentHelper.get_bucket_name()
    # transcriptions_path = EnvironmentHelper.get_transcriptions_key_path()
    # audio_source_link = f"s3://{bucket_name}/{raw_key}"
    # destination_link = f"s3://{bucket_name}/{transcriptions_path}{job_id}.json"
    # language = "pt"

    # # instancia o cliente dynamo
    # dynamo = boto3.resource("dynamodb")
    # table_name = EnvironmentHelper.get_dynamo_transcriptions_table()
    # table = dynamo.Table(table_name)

    # # grava a entrada na tabela de transcrições
    # logger.info(f"Registrando job como PENDING na tabela dynamo {table_name}...")
    # table.put_item(
    #     Item={
    #         "job_id": job_id,
    #         "status": "PENDING",
    #         "source_link": audio_source_link,
    #         "destination_link": destination_link,
    #         "language": language,
    #     }
    # )

    sqs_client = client("sqs")
    sts_client = client("sts")
    acc_id = sts_client.get_caller_identity()["Account"]
    queue_name = EnvironmentHelper.get_queue_name()

    respons_get_sqs_url = sqs_client.get_queue_url(
        QueueName=queue_name, QueueOwnerAWSAccountId=acc_id
    )
    sqs_url = respons_get_sqs_url["QueueUrl"]

    message = {"raw_key": raw_key, "key": key}
    # message = {
    #     "job_id": job_id,
    #     "source_link": audio_source_link,
    #     "destination_link": destination_link,
    #     "language": language,
    # }
    message_str = json.dumps(message)

    print(f"Enviando {message_str} para a fila {queue_name}")

    sqs_client.send_message(
        QueueUrl=sqs_url, MessageBody=message_str
    )

    # sqs_client.send_message(
    #     QueueUrl=sqs_url,
    #     MessageBody=message_str,
    #     MessageGroupId=job_id,
    #     MessageDeduplicationId=job_id,
    # )


async def read_job_transcription_from_s3(filename_plus_path):
    """
    Lê o arquivo de transcrição do S3.
    Agora assumimos que o arquivo já está no novo formato:
    {
      "transcricaoAudio": {
        "speakers": [
          { "name": "...", "startTime": ..., "endTime": ..., "text": "..." },
          ...
        ]
      }
    }
    """
    s3_client = client("s3", region_name="us-east-1")
    bucket_name = EnvironmentHelper.get_bucket_name()
    key_path = f"{EnvironmentHelper.get_transcriptions_key_path()}{filename_plus_path}"

    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=key_path)
        json_string = response["Body"].read().decode("utf-8")

        # s3_client.delete_object(Bucket=bucket_name, Key=key_path)
        return json.loads(json_string)
    except ClientError as e:
        logger.error(f"Erro ao ler o arquivo do S3: {e.response['Error']['Message']}")
        raise


def extract_speakers_by_audio_to_dict(audio_name):
    """
    Determina os locutores a partir do nome do arquivo de áudio.
    EM TODAS AS TRANSCRIÇÕES, o primeiro spk sempre será spk_0 e o segundo spk_1.
    """
    campos_array = audio_name.split("_", 7)
    locutor_centro = campos_array[3]
    locutor_agente = campos_array[6].replace(
        f".{EnvironmentHelper.get_media_format()}", ""
    )

    if campos_array[5] == "I":
        # I: ligação Agente -> Centro de Controle (inbound); centro atende, logo fala primeiro
        return {"spk_0": locutor_centro, "spk_1": locutor_agente}
    elif campos_array[5] == "O":
        # O: ligação Centro de Controle -> Agente (outbound); agente atende, logo fala primeiro
        return {"spk_0": locutor_agente, "spk_1": locutor_centro}
    return {}


async def process_transcribe(key, context):
    """
    Agora assumimos que `key` é o identificador do job e que o arquivo
    de transcrição no S3 já está no formato final, com `transcricaoAudio`.
    """

    # Leitura do arquivo usando key
    transcription_result = await read_job_transcription_from_s3(f"{key}.json")

    # Verifica se o campo transcricaoAudio existe e possui dados
    if "transcricaoAudio" not in transcription_result or not transcription_result[
        "transcricaoAudio"
    ].get("speakers"):
        raise Exception(
            f"O arquivo {key}.json do áudio está vazio ou não contém `transcricaoAudio`"
        )

    # Falha publicada pela API (timeout etc.): ainda indexa no OS para o front
    # deixar de mostrar card em branco (speakers system + error).

    # A lógica para extrair audio_id permanece a mesma
    keys = key.split("_", 7)
    audio_id = f"{keys[0]}_{keys[1]}_{keys[2]}"
    data_full = convert_to_timestamp(
        convert_data_hora_string_to_data_hora_datetime(f"{keys[0]}_{keys[1]}")
    )

    # Extrai os speakers do nome do arquivo
    speakers_dict = extract_speakers_by_audio_to_dict(key.split("/")[-1])

    # Agora `dialogue` é o dicionário `transcricaoAudio`
    dialogue = transcription_result["transcricaoAudio"]

    # Concatena falas sequenciais do mesmo locutor
    speakers_concatenados = []
    speaker_atual = None

    for speaker in dialogue["speakers"]:
        if not speaker_atual:
            speaker_atual = speaker
            continue

        if speaker["name"] == speaker_atual["name"]:
            # Mesmo locutor - concatena o texto e atualiza endTime
            speaker_atual["text"] += " " + speaker["text"]
            speaker_atual["endTime"] = speaker["endTime"]
        else:
            # Locutor diferente - adiciona o anterior e começa novo
            speakers_concatenados.append(speaker_atual)
            speaker_atual = speaker

    # Adiciona o último speaker
    if speaker_atual:
        speakers_concatenados.append(speaker_atual)

    # Atualiza o dialogue com os speakers concatenados
    dialogue["speakers"] = speakers_concatenados

    # Adiciona os nomes dos speakers ao diálogo
    if speakers_dict:
        for speaker in dialogue["speakers"]:
            speaker_id = speaker.get("name")
            if speaker_id in speakers_dict:
                speaker["name"] = speakers_dict[speaker_id]

    # Corrige os tipos de startTime e endTime para float
    for speaker in dialogue["speakers"]:
        speaker["startTime"] = int(float(speaker["startTime"]) * 10000000)
        speaker["endTime"] = int(float(speaker["endTime"]) * 10000000)

    # Salvar no OpenSearch
    uris = [EnvironmentHelper.get_uri()]
    repository = OpenSearchRepository(context, uris, EnvironmentHelper.get_index())
    audio = await repository.get_by(audio_id)
    if audio and audio.get("id") == audio_id:
        # Atualiza o campo transcricaoAudio com o diálogo retornado pela API
        await repository.partial_update(audio_id, {"transcricaoAudio": dialogue})
    else:
        raise Exception(f"Áudio {audio_id} não localizado para adição de diálogo")

    logger.info(
        f"{context.aws_request_id} - Diálogo inserido no áudio {audio_id} com sucesso"
    )
