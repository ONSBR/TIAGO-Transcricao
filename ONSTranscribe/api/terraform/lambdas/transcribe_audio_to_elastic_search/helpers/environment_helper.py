from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class EnvironmentHelper:
    @staticmethod
    def get_transcriptions_key_path():
        return os.getenv("TranscriptionsKeyPath")

    # @staticmethod
    # def get_dynamo_transcriptions_table():
    #     return os.getenv("TranscriptionsTable", "TranscriptionsTable")

    @staticmethod
    def get_vocabularies_key_path():
        return os.getenv("VocabulariesKeyPath")

    @staticmethod
    def get_uri():
        return os.getenv("ElasticsearchUri")

    @staticmethod
    def get_index():
        return os.getenv("index")

    @staticmethod
    def get_key_path():
        return os.getenv("KeyPath")

    @staticmethod
    def get_data_access_role_arn():
        return os.getenv("DataAccessRoleArn")

    @staticmethod
    def get_recorder_string_list():
        return os.getenv("RecodersList")

    @staticmethod
    def get_bucket_name():
        return os.getenv("BucketName")

    @staticmethod
    def get_vocabulary_name():
        return os.getenv("VocabularyName")

    @staticmethod
    def get_media_format():
        return os.getenv("MediaFormat")

    @staticmethod
    def get_transcribe_provider():
        return os.getenv("TranscribeProvider")

    @staticmethod
    def get_transcription_api_base_url():
        return os.getenv("TranscribeApiUrl")

    @staticmethod
    def get_queue_name():
        return os.getenv("CreateJobQueueName")

    @staticmethod
    def parse_processing_enabled(raw: str | None, *, default: bool = True) -> bool:
        """Interpreta 'true'/'false'/1/0/on/off. Valor ausente ou desconhecido → default."""
        if raw is None:
            return default
        value = raw.strip().lower()
        if not value:
            return default
        if value in _TRUE_VALUES:
            return True
        if value in _FALSE_VALUES:
            return False
        return default

    @staticmethod
    def get_ssm_parameter_value(name: str) -> str:
        """I/O: lê Parameter Store. Separado para teste unitário sem rede."""
        from boto3 import client

        ssm = client("ssm")
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]

    @staticmethod
    def is_transcription_processing_enabled() -> bool:
        """Gate de enqueue (não controla ECS).

        Fonte: SSM no path de TranscriptionProcessingEnabledSsmParameter.
        Default true se o path não estiver setado, se o valor for inválido, ou se
        a leitura falhar (rede/IAM/endpoint) — não pausa o pipeline por acidente.
        Flip operacional (sem deploy):
          aws ssm put-parameter --name <path> --value false --overwrite --type String
        """
        param_name = (os.getenv("TranscriptionProcessingEnabledSsmParameter") or "").strip()
        if not param_name:
            return True

        try:
            raw = EnvironmentHelper.get_ssm_parameter_value(param_name)
        except Exception:
            logger.exception(
                "Falha ao ler SSM %s — assumindo processing enabled",
                param_name,
            )
            return True

        return EnvironmentHelper.parse_processing_enabled(raw, default=True)
