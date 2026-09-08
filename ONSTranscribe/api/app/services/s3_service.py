"""Serviços de acesso ao S3: download e upload de arquivos."""
import logging
from urllib.parse import urlparse

import boto3

from app.config import config

logger = logging.getLogger(__name__)

_s3_client = boto3.client("s3", region_name=config.aws.region)


def _parse_s3_url(s3_url: str) -> tuple[str, str]:
    """Extrai bucket e key de uma URL S3 no formato HTTPS.

    Args:
        s3_url: URL no formato 'https://bucket.s3.amazonaws.com/key'.

    Returns:
        Tupla (bucket_name, s3_key).
    """
    parsed = urlparse(s3_url)
    bucket_name = parsed.netloc.split(".")[0]
    s3_key = parsed.path.lstrip("/")
    return bucket_name, s3_key


def download_file_from_s3(s3_url: str, local_path: str) -> None:
    """Baixa um arquivo do S3 para o sistema local.

    Args:
        s3_url: URL completa do S3 (formato HTTPS).
        local_path: Caminho local onde o arquivo será salvo.

    Raises:
        Exception: Se o download falhar.
    """
    bucket_name, s3_key = _parse_s3_url(s3_url)
    logger.info("Baixando s3://%s/%s para %s", bucket_name, s3_key, local_path)
    _s3_client.download_file(bucket_name, s3_key, local_path)


def upload_file_to_s3(local_path: str, s3_url: str) -> None:
    """Faz upload de um arquivo local para o S3.

    Args:
        local_path: Caminho local do arquivo a enviar.
        s3_url: URL completa de destino no S3 (formato HTTPS).

    Raises:
        Exception: Se o upload falhar.
    """
    bucket_name, s3_key = _parse_s3_url(s3_url)
    logger.info("Enviando %s para s3://%s/%s", local_path, bucket_name, s3_key)
    _s3_client.upload_file(local_path, bucket_name, s3_key)
