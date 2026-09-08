"""Configuração centralizada da aplicação via variáveis de ambiente."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AWSConfig:
    region: str
    bedrock_model: str | None
    s3_bucket: str | None
    s3_model_path: str | None
    s3_segmentation_model_path: str | None
    dynamodb_table: str
    sqs_queue_url: str | None


@dataclass(frozen=True)
class BedrockPromptsConfig:
    beautify_id: str | None
    beautify_version: str | None
    beautify_system_id: str | None
    beautify_system_version: str | None
    glossary_id: str | None
    glossary_version: str | None
    key_entities_id: str | None
    key_entities_version: str | None
    subject_id: str | None
    subject_version: str | None
    summary_id: str | None
    summary_version: str | None
    installation_disambiguation_id: str | None
    installation_disambiguation_version: str | None


@dataclass(frozen=True)
class InstallationsConfig:
    """Configuração da normalização de instalações/usinas contra a lista válida."""
    list_s3_key: str | None
    match_threshold_high: int
    match_threshold_low: int


@dataclass(frozen=True)
class ModelConfig:
    whisper_local_path: str
    segmentation_local_path: str
    whisper_base_model: str
    diarization_base_model: str
    temp_dir: str


@dataclass(frozen=True)
class TranscriptionConfig:
    max_prompt_tokens: int = 5000
    diarization_num_speakers: int = 2
    min_segment_duration: float = 1.0
    # 1 = sequencial (padrão): evita picos de VRAM com vários Whisper no mesmo job.
    # Valores >1 só se a GPU tiver margem comprovada sob carga real.
    max_workers: int = 1
    # Batch pyannote (segmentação/embedding). Default do lib (32) estoura VRAM:
    # "MemoryError: batch_size ( 32) is probably too large".
    diarization_batch_size: int = 8
    whisper_no_speech_threshold: float = 0.3
    whisper_logprob_threshold: float = -1.0
    whisper_beam_width: int = 2
    whisper_chunk_length_s: int = 30
    target_sample_rate: int = 16000
    metadata_timeout_s: int = 60
    # Teto do job completo (GPU + metadados). 0 desliga. Default = visibility SQS (15 min).
    job_timeout_s: int = 900


@dataclass(frozen=True)
class AppConfig:
    aws: AWSConfig
    prompts: BedrockPromptsConfig
    transcription: TranscriptionConfig
    models: ModelConfig
    installations: InstallationsConfig


def load_config() -> AppConfig:
    """Carrega configurações das variáveis de ambiente."""
    return AppConfig(
        aws=AWSConfig(
            region=os.getenv("AWS_REGION", "us-east-1"),
            bedrock_model=os.getenv("TRANSCRIBE_BEDROCK_MODEL"),
            s3_bucket=os.getenv("TRANSCRIBE_S3_NAME"),
            s3_model_path=os.getenv("TRANSCRIBE_S3_MODEL_PATH"),
            s3_segmentation_model_path=os.getenv("TRANSCRIBE_S3_SEGMENTATION_MODEL_PATH"),
            dynamodb_table=os.getenv("TRANSCRIPTIONS_TABLE", "TranscriptionsTable"),
            sqs_queue_url=os.getenv("TRANSCRIBE_SQS_QUEUE_URL"),
        ),
        prompts=BedrockPromptsConfig(
            beautify_id=os.getenv("RUN_BEAUTIFY_PROMPT_ID"),
            beautify_version=os.getenv("RUN_BEAUTIFY_PROMPT_VERSION"),
            beautify_system_id=os.getenv("RUN_BEAUTIFY_SYSTEM_PROMPT_ID"),
            beautify_system_version=os.getenv("RUN_BEAUTIFY_SYSTEM_PROMPT_VERSION"),
            glossary_id=os.getenv("GLOSSARY_PROMPT_ID"),
            glossary_version=os.getenv("GLOSSARY_PROMPT_VERSION"),
            key_entities_id=os.getenv("KEY_ENTITIES_PROMPT_ID"),
            key_entities_version=os.getenv("KEY_ENTITIES_PROMPT_VERSION"),
            subject_id=os.getenv("SUBJECT_PROMPT_ID"),
            subject_version=os.getenv("SUBJECT_PROMPT_VERSION"),
            summary_id=os.getenv("SUMMARY_PROMPT_ID"),
            summary_version=os.getenv("SUMMARY_PROMPT_VERSION"),
            installation_disambiguation_id=os.getenv("INSTALLATION_DISAMBIGUATION_PROMPT_ID"),
            installation_disambiguation_version=os.getenv("INSTALLATION_DISAMBIGUATION_PROMPT_VERSION"),
        ),
        installations=InstallationsConfig(
            list_s3_key=os.getenv("INSTALLATIONS_LIST_S3_KEY"),
            match_threshold_high=int(os.getenv("INSTALLATION_MATCH_THRESHOLD_HIGH", "90")),
            match_threshold_low=int(os.getenv("INSTALLATION_MATCH_THRESHOLD_LOW", "60")),
        ),
        transcription=TranscriptionConfig(
            max_workers=int(os.getenv("MAX_TRANSCRIBE_WORKERS", "1")),
            diarization_batch_size=int(os.getenv("DIARIZATION_BATCH_SIZE", "8")),
            job_timeout_s=int(os.getenv("JOB_TIMEOUT_S", "900")),
        ),
        models=ModelConfig(
            whisper_local_path=os.getenv("MODEL_WHISPER_LOCAL_PATH", "models/whisper_finetuned"),
            segmentation_local_path=os.getenv("MODEL_SEGMENTATION_LOCAL_PATH", "models/segmentation_finetuned"),
            whisper_base_model=os.getenv("WHISPER_BASE_MODEL", "openai/whisper-medium"),
            diarization_base_model=os.getenv("DIARIZATION_BASE_MODEL", "fatymatariq/speaker-diarization-3.1"),
            temp_dir=os.getenv("TEMP_DIR", "/tmp"),
        ),
    )


config = load_config()
