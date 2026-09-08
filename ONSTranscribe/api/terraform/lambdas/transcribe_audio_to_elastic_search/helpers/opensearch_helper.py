import json
import logging
from helpers.environment_helper import EnvironmentHelper
from helpers.utils_helper import parsing_key, convert_to_timestamp, convert_data_hora_string_to_data_hora_datetime
from repositories.opensearch_repository import OpenSearchRepository
from helpers.entities import Audio, KeyValueEntity
from helpers.transcription_status import (
    build_paused_transcricao_audio,
    build_queued_transcricao_audio,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def is_exist(audio_id, context):
    """
    Verifica se um registro com o ID fornecido já existe no OpenSearch.
    """
    uris = [EnvironmentHelper.get_uri()]
    repository = OpenSearchRepository(context, uris, EnvironmentHelper.get_index())

    audio = await repository.get_by(audio_id)
    return audio is not None and "id" in audio and bool(audio["id"])

async def insert_key_to_opensearch(key, recorder_dict, context):
    """
    Insere uma chave no OpenSearch, criando um registro de áudio.
    """
    result_indexing = False

    # Obter a URI do OpenSearch
    uris = [EnvironmentHelper.get_uri()]
    repository = OpenSearchRepository(context, uris, EnvironmentHelper.get_index())

    # Preparar as opções de serialização JSON
    options = {
        "indent": 4,
        "ensure_ascii": False
    }

    # Processar a chave
    logger.info(f"Key: {key}")
    key_array = []
    gravador = ""

    if "/" in key:
        key_array = key.split("/")
        recorder_folder = key_array[0]
        gravador = recorder_dict.get(recorder_folder)
        key = key_array[1]

    key_parsed = parsing_key(key)
    logger.info(f"keyParsed: {key_parsed}")
    campos_array = key_parsed.split("_", 7)
    logger.info(f"itens: {', '.join(campos_array)}")

    # Construir o objeto Audio
    audio = Audio(
        id=f"{campos_array[0]}_{campos_array[1]}_{campos_array[2]}",
        shorticut=key_parsed.split(".")[0],
        data_full=convert_to_timestamp(
            convert_data_hora_string_to_data_hora_datetime(f"{campos_array[0]}_{campos_array[1]}")
        ),
        data=int(campos_array[0]),
        hora=int(campos_array[1]),
        ramal=int(campos_array[2]),
        locutorUm=KeyValueEntity(
            key=campos_array[3],
            value=campos_array[3].split("-")[0]
        ),
        duracao=int(campos_array[4]),
        direcao=KeyValueEntity(
            key=campos_array[5],
            value="<" if campos_array[5] == "O" else ">"
        ),
        locutorDois=KeyValueEntity(
            key=campos_array[6].replace(EnvironmentHelper.get_media_format(), ""),
            value=campos_array[6].replace(EnvironmentHelper.get_media_format(), "")
        ),
        gravador=gravador,
        # Front mostra speakers; QUEUED (fila) ou PAUSED (processamento desligado).
        transcricao_audio=(
            build_queued_transcricao_audio()
            if EnvironmentHelper.is_transcription_processing_enabled()
            else build_paused_transcricao_audio()
        ),
    )

    # Verificar se o documento já existe
    exists = await is_exist(audio.id, context)
    if not exists:
        result_indexing = await repository.insert_or_update(audio)
        if result_indexing:
            logger.info(f"Documento gravado com sucesso. Id: {audio.id}")

    # Serializar o áudio como JSON
    response_object = json.dumps(audio.to_dict(), **options) if result_indexing else ""

    return result_indexing, response_object, key_parsed, audio
