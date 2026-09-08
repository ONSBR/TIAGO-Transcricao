import json
import logging
import boto3
from botocore.exceptions import ClientError
from helpers.environment_helper import EnvironmentHelper
from repositories.opensearch_repository import OpenSearchRepository
from helpers.utils_helper import convert_to_timestamp, convert_data_hora_string_to_data_hora_datetime
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

async def get_vocabulary(key, transcribe_client):
    """
    Obtém o vocabulário do Amazon Transcribe.
    """
    logger.info(f"Buscando vocabulário com key: {key}")
    try:
        response = transcribe_client.get_vocabulary(VocabularyName=key)
        return response.get("VocabularyName")
    except ClientError as e:
        logger.error(f"Erro ao buscar o vocabulário '{key}': {e.response['Error']['Message']}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar o vocabulário '{key}': {str(e)}", exc_info=True)
        raise
    
def delete_existing_job(transcribe_client, key_parsed):
    """
    Verifica e remove um job existente, se necessário.
    """
    try:
        job_response = transcribe_client.get_transcription_job(TranscriptionJobName=key_parsed)
        if job_response.get("TranscriptionJob", {}).get("TranscriptionJobName") == key_parsed:
            transcribe_client.delete_transcription_job(TranscriptionJobName=key_parsed)
            logger.info(f"Job existente '{key_parsed}' foi deletado.")
    except transcribe_client.exceptions.BadRequestException as e:
        logger.warning(f"Job '{key_parsed}' não encontrado. Mensagem: {e}")
        # Não faz nada, pois o job não existir é esperado em alguns casos
    except ClientError as e:
        logger.error(f"Erro ao verificar/deletar job existente: {e.response['Error']['Message']}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao verificar/deletar job '{key_parsed}': {str(e)}", exc_info=True)
        raise


def create_transcribe_request(raw_key, key_parsed, vocabulary):
    """
    Cria a configuração do job de transcrição.
    """
    return {
        "TranscriptionJobName": key_parsed,
        "LanguageCode": "pt-BR",
        "JobExecutionSettings": {
            "AllowDeferredExecution": True,
            "DataAccessRoleArn": EnvironmentHelper.get_data_access_role_arn(),
        },
        "MediaFormat": EnvironmentHelper.get_media_format().replace(".", ""),
        "Media": {"MediaFileUri": f"s3://{EnvironmentHelper.get_bucket_name()}/{raw_key}"},
        "OutputBucketName": EnvironmentHelper.get_bucket_name(),
        "OutputKey": f"{EnvironmentHelper.get_transcriptions_key_path()}{key_parsed}.json",
        "Settings": {
            "ShowSpeakerLabels": True,
            "MaxSpeakerLabels": 2,
            "ChannelIdentification": False,
            "ShowAlternatives": False,
            "VocabularyName": vocabulary,
        },
    }


async def create_job(raw_key, key, context):
    """
    Cria um job no Amazon Transcribe.
    """
    key_parsed = key.split("/")[-1] if "/" in key else key
    transcribe_client = boto3.client("transcribe", region_name="us-east-1")

    try:
        # Preparar vocabulário
        vocab_name_or_key = EnvironmentHelper.get_vocabulary_name() or key_parsed
        vocabulary = await get_vocabulary(vocab_name_or_key, transcribe_client)

        # Verificar job existente
        delete_existing_job(transcribe_client, key_parsed)

        # Configurar e iniciar job
        request = create_transcribe_request(raw_key, key_parsed, vocabulary)
        transcription_job_response = transcribe_client.start_transcription_job(**request)

        logger.info(f"Job '{key_parsed}' criado com sucesso")
        logger.info(f"Request => {json.dumps(request, indent=4, default=str)}")
        logger.info(f"Response => {json.dumps(transcription_job_response, indent=4, default=str)}")
    except Exception as e:
        logger.error(f"Erro ao criar o job '{key_parsed}': {str(e)}", exc_info=True)
        raise


async def read_job_transcription_from_s3(filename_plus_path):
    """
    Lê o arquivo de transcrição do S3 e retorna o JSON.
    """
    s3_client = boto3.client("s3", region_name="us-east-1")
    bucket_name = EnvironmentHelper.get_bucket_name()
    key_path = f"{EnvironmentHelper.get_transcriptions_key_path()}{filename_plus_path}"

    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=key_path)
        json_string = response["Body"].read().decode("utf-8")

        # Deletar o arquivo após leitura
        s3_client.delete_object(Bucket=bucket_name, Key=key_path)
        logger.info(f"Arquivo '{filename_plus_path}' lido e deletado com sucesso do S3.")
        return json.loads(json_string)
    except ClientError as e:
        logger.error(f"Erro ao acessar o S3 para o arquivo '{filename_plus_path}': {e.response['Error']['Message']}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar JSON do arquivo '{filename_plus_path}': {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao processar o arquivo '{filename_plus_path}': {str(e)}", exc_info=True)
        raise


def extract_speakers_by_audio_to_dict(audio_name):
    """
    Determina os locutores a partir do nome do arquivo de áudio.
    """
    campos_array = audio_name.split("_", 7)
    locutor_um = campos_array[3]
    locutor_dois = campos_array[6].replace(f".{EnvironmentHelper.get_media_format()}", "")

    if campos_array[5] == "I":
        return {"spk_0": locutor_um, "spk_1": locutor_dois}
    elif campos_array[5] == "O":
        return {"spk_0": locutor_dois, "spk_1": locutor_um}
    return {}

def create_dialogue(transcription_result, data_full):
    """
    Cria um diálogo com base nos resultados da transcrição.
    """
    speakers = extract_speakers_by_audio_to_dict(transcription_result.get("jobName", ""))
    dialogue = {"speakers": []}

    for segment in transcription_result["results"]["speaker_labels"]["segments"]:
        items = transcription_result["results"]["items"]
        item_by_segment = [
            item for item in items
            if float(item.get("start_time", 0)) >= float(segment["start_time"])
            and float(item.get("end_time", 0)) <= float(segment["end_time"])
        ]

        text_extracted = " ".join(
            alt["content"] for item in item_by_segment for alt in item.get("alternatives", [])
        )

        dialogue["speakers"].append({
            "name": speakers.get(segment["speaker_label"], "Unknown"),
            "start_time": float(segment["start_time"]) + (data_full.timestamp() if isinstance(data_full, datetime) else data_full),
            "end_time": float(segment["end_time"]) + (data_full.timestamp() if isinstance(data_full, datetime) else data_full),
            "text": text_extracted,
        })

    return dialogue


def get_transcription_job(client, job_name):
    try:
        logger.info(f"Recuperando o Job {job_name}")
        return client.get_transcription_job(TranscriptionJobName=job_name)
    except ClientError as e:
        logger.error(f"Erro ao recuperar o job: {e.response['Error']['Message']}")
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao recuperar o job {job_name}: {str(e)}", exc_info=True)
        raise


async def read_transcription_file(key_parsed):
    logger.info(f"Lendo o arquivo {key_parsed}.json do S3")
    transcription_result = await read_job_transcription_from_s3(f"{key_parsed}.json")
    if not transcription_result.get("results"):
        raise Exception(f"O arquivo {key_parsed} do áudio está vazio")
    return transcription_result


async def update_audio_repository(repository, audio_id, dialogue):
    logger.info(f"Buscando o Áudio {audio_id}")
    audio = await repository.get_by(audio_id)
    if audio and audio.get("id") == audio_id:
        logger.info(f"Salvando o Diálogo no Áudio {audio_id}")
        await repository.partial_update(audio_id, {"transcricaoAudio": dialogue})
    else:
        raise Exception(f"Áudio {audio_id} não localizado para adição de diálogo no comentário")


async def process_transcribe(key, context):
    """
    Processa o resultado de um job de transcrição.
    """
    try:
        # Preparação inicial
        key_parsed = key.split("/")[-1] if "/" in key else key
        uris = [EnvironmentHelper.get_uri()]
        repository = OpenSearchRepository(context, uris, EnvironmentHelper.get_index())
        keys = key_parsed.split("_", 7)
        audio_id = f"{keys[0]}_{keys[1]}_{keys[2]}"
        data_full = convert_to_timestamp(
            convert_data_hora_string_to_data_hora_datetime(f"{keys[0]}_{keys[1]}")
        )

        # Recupera o job de transcrição
        transcribe_client = boto3.client("transcribe", region_name="us-east-1")
        job_response = get_transcription_job(transcribe_client, key_parsed)

        # Lê a transcrição do S3
        transcription_result = await read_transcription_file(key_parsed)

        # Cria o diálogo
        dialogue = create_dialogue(transcription_result, data_full)

        # Atualiza o repositório
        await update_audio_repository(repository, audio_id, dialogue)

    except Exception as e:
        logger.error(f"Erro durante o processamento do job {key}: {str(e)}", exc_info=True)
        raise
    finally:
        try:
            transcribe_client.delete_transcription_job(TranscriptionJobName=key_parsed)
            logger.info(f"Job {key_parsed} apagado com sucesso.")
        except Exception as e:
            logger.error(f"Erro ao apagar o job {key_parsed}: {str(e)}", exc_info=True)