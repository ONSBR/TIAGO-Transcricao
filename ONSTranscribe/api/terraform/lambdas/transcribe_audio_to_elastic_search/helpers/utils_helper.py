import unicodedata
import re
from datetime import datetime
from helpers.environment_helper import EnvironmentHelper
from helpers.entities import DestinationEnum

def parsing_key(key):
    """
    Decodifica uma chave e remove caracteres especiais e diacríticos.
    """
    key_decoded = key  # Em Python, a decodificação de URL pode ser feita se necessário (não incluída aqui por simplicidade)
    key_without_diacritics = remove_diacritics(key_decoded)
    key_cleaned = remove_special_characters(key_without_diacritics)
    return key_cleaned

def remove_special_characters(text):
    """
    Remove caracteres especiais de uma string.
    """
    return re.sub(r"[^a-zA-Z0-9/_.-]+", "", text)

def remove_diacritics(text):
    """
    Remove diacríticos de caracteres.
    """
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c))

def convert_to_timestamp(value):
    """
    Converte um datetime para um timestamp UNIX.
    """
    return int((value - datetime(1970, 1, 1)).total_seconds())

def convert_data_hora_string_to_data_hora_datetime(date_string):
    """
    Converte uma string no formato 'yyyyMMdd_HHmmss' para um objeto datetime.
    """
    try:
        return datetime.strptime(date_string, "%Y%m%d_%H%M%S")
    except ValueError:
        return None

def extract_key(message: dict, key_path: str) -> str:
    """
    Extrai a chave de um registro SNS, removendo o keyPath e o sufixo.
    """
    for record in message.get("Records", []):
        key = record.get("s3", {}).get("object", {}).get("key")
        if key:
            return key.replace(key_path, "").split(".")[0]
    return None

def make_path_to_destination(key):
    """
    Determina o destino com base no key e nas variáveis de ambiente.

    Ordem importa: JSON sob transcriptions (inclui o veneno ``*.wav.json``) vai
    para TRANSCRIBE (indexar), nunca DATABASE/create_job. Áudio de entrada exige
    sufixo real de mídia (``.wav``), não substring ``"wav" in key``.
    """
    if not key:
        return DestinationEnum.NOWHERE

    lower = key.lower()
    transcriptions = EnvironmentHelper.get_transcriptions_key_path() or ""
    vocabularies = EnvironmentHelper.get_vocabularies_key_path() or ""
    control_room = EnvironmentHelper.get_key_path() or ""
    media = (EnvironmentHelper.get_media_format() or "wav").lstrip(".").lower()
    media_suffix = f".{media}"

    # 1) Saída de transcrição — primeiro, para não confundir com áudio.
    if transcriptions and transcriptions in key and lower.endswith(".json"):
        return DestinationEnum.TRANSCRIBE

    # 2) Vocabulário
    if vocabularies and vocabularies in key and lower.endswith(".txt"):
        return DestinationEnum.VOCABULARY

    # 3) Áudio novo na pasta de gravações (sufixo, não substring).
    if (
        control_room
        and control_room in key
        and lower.endswith(media_suffix)
        and not (transcriptions and transcriptions in key)
    ):
        return DestinationEnum.DATABASE

    return DestinationEnum.NOWHERE
