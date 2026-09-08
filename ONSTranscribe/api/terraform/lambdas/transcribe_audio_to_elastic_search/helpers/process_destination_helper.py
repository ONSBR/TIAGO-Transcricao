import logging
from helpers.utils_helper import make_path_to_destination, extract_key
from helpers.environment_helper import EnvironmentHelper
from helpers.opensearch_helper import insert_key_to_opensearch
from helpers.entities import DestinationEnum

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if EnvironmentHelper.get_transcribe_provider() == "internal":
    from helpers.transcribe_helper import create_job, process_transcribe
else:
    from helpers.aws_transcribe_helper import create_job, process_transcribe

async def execute(context, return_sns, validate_key, key, result, recorder_dict):
    destination = make_path_to_destination(validate_key)

    if destination == DestinationEnum.DATABASE:
        await database_destination(context, return_sns, validate_key, key, result, recorder_dict)
    elif destination == DestinationEnum.TRANSCRIBE:
        logger.info(f"Executando o transcribe para {key}")
        await transcribe_destination(context, return_sns, key)
    elif destination == DestinationEnum.VOCABULARY:
        custom_vocabulary_destination(context, return_sns)
    elif destination == DestinationEnum.TRANSCODER:
        await transcoder_destination(context, validate_key, key)


def should_enqueue_transcription_job() -> bool:
    """Contrato da flag de processamento (testável sem I/O OpenSearch/SQS)."""
    return EnvironmentHelper.is_transcription_processing_enabled()


async def database_destination(context, return_sns, validate_key, key, result, recorder_dict):
    logger.info(f"Destination => {DestinationEnum.DATABASE.value}")
    logger.info(f"validateKey => {validate_key}")
    
    key = extract_key(return_sns, EnvironmentHelper.get_key_path())
    logger.info(f"key => {key}")

    # Inserir chave no Elasticsearch
    result = await insert_key_to_opensearch(key, recorder_dict, context)

    # Pause de custo: registra no OS (PAUSED) mas não enfileira worker/LLM.
    if not should_enqueue_transcription_job():
        logger.info(
            "transcription_processing_enabled SSM=false — skip create_job (key=%s)",
            key,
        )
        return

    # Criar job de transcrição (usando o helper definido pela variável de ambiente)
    await create_job(validate_key, key, context)


async def transcribe_destination(context, return_sns, key):
    logger.info(f"{context.aws_request_id} - Processamento iniciado")
    key = extract_key(return_sns, EnvironmentHelper.get_transcriptions_key_path())
    logger.info(f"Destination => {DestinationEnum.TRANSCRIBE.value}")
    logger.info(f"key => {key}")

    # Processar transcrição (usando o helper definido pela variável de ambiente)
    await process_transcribe(key, context)
    logger.info(f"{context.aws_request_id} - Processamento finalizado")


def custom_vocabulary_destination(context, return_sns):
    key = extract_key(return_sns, EnvironmentHelper.get_vocabularies_key_path())
    logger.info(f"Destination => {DestinationEnum.VOCABULARY.value}")
    logger.info(f"key => {key}")


async def transcoder_destination(context, validate_key, key):
    logger.info(f"Destination => {DestinationEnum.TRANSCODER.value}")
    logger.info(f"Processamento de Áudio via AWS Elastic Transcoder identificado. Áudio: {validate_key}")
    logger.info(f"{context.aws_request_id} - Processamento da Transcrição iniciado")
    
    if not should_enqueue_transcription_job():
        logger.info(
            "transcription_processing_enabled SSM=false — skip create_job transcoder (key=%s)",
            key,
        )
        return

    # Criar job de transcrição (usando o helper definido pela variável de ambiente)
    await create_job(validate_key, key, context)
    logger.info(f"{context.aws_request_id} - Processamento da Transcrição finalizado")
