"""Gerenciamento de ciclo de vida e cache dos modelos de ML."""
import logging
import os
import threading

import boto3
import torch
from pyannote.audio import Pipeline
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
from transformers.pipelines.automatic_speech_recognition import (
    AutomaticSpeechRecognitionPipeline,
)

from app.config import config
from app.services.diarization_batch import apply_diarization_batch_size
from app.services.diarizers import SegmentationModel

logger = logging.getLogger(__name__)

_model_lock = threading.Lock()
_diarization_lock = threading.Lock()

_cached_whisper: dict = {"processor": None, "model": None, "pipe": None}
_cached_diarization_pipeline = None


class _WhisperPipelinePatched(AutomaticSpeechRecognitionPipeline):
    """Normaliza o retorno de model.generate() em out["tokens"] para tensor.

    Origem (transformers 5.5.3): conflito entre return_dict_in_generate=True
    (necessário para _need_fallback não crashar) e postprocess (espera tensor).
    _forward armazenava o output bruto de model.generate() em out["tokens"];
    com return_dict_in_generate=True, WhisperGenerationMixin.generate() retorna
    um dict (plain ou ModelOutput) com chave "sequences" e o postprocess tentava
    out["tokens"].dtype → AttributeError. Para return_timestamps=True (nosso
    caso) o código original não extraía o tensor — daí este fix.

    A partir de transformers 5.10.x o upstream faz a mesma normalização dentro
    do próprio _forward (branch `elif isinstance(tokens, dict) and "sequences"
    in tokens`), então na versão fixada hoje (5.10.1) este override é inócuo:
    super()._forward() já devolve tensor em out["tokens"], o isinstance(dict)
    é falso e nada é reescrito — não há normalização dupla. Mantido como rede
    de segurança para caminhos/versões em que o upstream não normalize.

    Usa isinstance(tokens, dict) para cobrir tanto ModelOutput (subclasse de
    OrderedDict) quanto plain dict. Sem alteração no resultado da transcrição.
    """

    def _forward(self, model_inputs, **generate_kwargs):
        result = super()._forward(model_inputs, **generate_kwargs)
        tokens = result.get("tokens")
        if isinstance(tokens, dict) and "sequences" in tokens:
            result["tokens"] = tokens["sequences"]
        return result


def get_whisper_asr():
    """Carrega e mantém em cache o processador, modelo e pipeline Whisper.

    Usa double-checked locking para garantir inicialização única por processo.

    Returns:
        Tupla (processor, model, pipe) prontos para inferência.
    """
    if _cached_whisper["pipe"] is None:
        with _model_lock:
            if _cached_whisper["pipe"] is None:
                from transformers import pipeline as hf_pipeline

                device = "cuda:0" if torch.cuda.is_available() else "cpu"
                logger.info("Inicializando Whisper no device: %s", device)

                processor = AutoProcessor.from_pretrained(config.models.whisper_base_model)
                model = AutoModelForSpeechSeq2Seq.from_pretrained(
                    config.models.whisper_local_path,
                    attn_implementation="sdpa",
                ).to(device)

                # transformers 5.x: _need_fallback exige return_dict_in_generate=True
                # (caminho seguro com BeamSearchDecoderOnlyOutput); output_scores=True
                # garante os scores no output. O generation_whisper.py é idêntico
                # entre 5.5.3 e 5.10.1, então essa exigência continua valendo; o
                # _WhisperPipelinePatched cobre o conflito com o postprocess (ver
                # classe — hoje redundante com o upstream).
                model.generation_config.num_beams = config.transcription.whisper_beam_width
                model.generation_config.output_scores = True

                pipe = hf_pipeline(
                    task="automatic-speech-recognition",
                    model=model,
                    tokenizer=processor.tokenizer,
                    feature_extractor=processor.feature_extractor,
                    device=device,
                    return_timestamps=True,
                    chunk_length_s=config.transcription.whisper_chunk_length_s,
                    dtype=torch.float16 if device == "cuda:0" else torch.float32,
                    pipeline_class=_WhisperPipelinePatched,
                    generate_kwargs={
                        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                        "logprob_threshold": config.transcription.whisper_logprob_threshold,
                        "no_speech_threshold": config.transcription.whisper_no_speech_threshold,
                        "compression_ratio_threshold": 1.35,
                        "condition_on_prev_tokens": False,
                        "return_dict_in_generate": True,
                    },
                )

                _cached_whisper.update({"processor": processor, "model": model, "pipe": pipe})

    return _cached_whisper["processor"], _cached_whisper["model"], _cached_whisper["pipe"]


def get_diarization_pipeline():
    """Carrega e mantém em cache o pipeline de diarização com modelo fino ajustado.

    Returns:
        Pipeline de diarização pyannote pronto para uso.
    """
    global _cached_diarization_pipeline
    if _cached_diarization_pipeline is None:
        with _diarization_lock:
            if _cached_diarization_pipeline is None:
                device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
                diarization_pipeline = Pipeline.from_pretrained(
                    config.models.diarization_base_model
                ).to(device)

                seg_model = SegmentationModel().from_pretrained(
                    config.models.segmentation_local_path
                )
                seg_model = seg_model.to_pyannote_model()
                diarization_pipeline._segmentation.model = seg_model.to(device)

                # Default pyannote (32) estoura VRAM sob carga em HOM.
                apply_diarization_batch_size(
                    diarization_pipeline, config.transcription.diarization_batch_size
                )

                _cached_diarization_pipeline = diarization_pipeline

    return _cached_diarization_pipeline


def download_model_if_not_exists(
    s3_bucket: str, s3_folder_path: str, local_folder_path: str
) -> None:
    """Baixa arquivos de uma pasta S3 para o sistema local, se ainda não existirem.

    Args:
        s3_bucket: Nome do bucket S3.
        s3_folder_path: Prefixo da pasta no S3.
        local_folder_path: Caminho local de destino.

    Raises:
        Exception: Se ocorrer erro ao listar ou baixar arquivos do S3.
    """
    session = boto3.Session()
    s3_client = session.client("s3")

    os.makedirs(local_folder_path, exist_ok=True)

    try:
        objects = s3_client.list_objects_v2(Bucket=s3_bucket, Prefix=s3_folder_path)
        if "Contents" not in objects:
            logger.warning(
                "Nenhum arquivo encontrado em s3://%s/%s", s3_bucket, s3_folder_path
            )
            return

        for obj in objects["Contents"]:
            s3_key = obj["Key"]
            if s3_key.endswith("/"):
                continue

            local_file_path = os.path.join(
                local_folder_path, os.path.relpath(s3_key, s3_folder_path)
            )
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)

            if not os.path.exists(local_file_path):
                try:
                    s3_client.download_file(s3_bucket, s3_key, local_file_path)
                    logger.info("Arquivo %s baixado para %s", s3_key, local_file_path)
                except Exception:
                    logger.exception("Erro ao baixar %s", s3_key)
                    raise
            else:
                logger.debug("Arquivo já existe: %s", local_file_path)

    except Exception:
        logger.exception(
            "Erro ao listar arquivos em s3://%s/%s", s3_bucket, s3_folder_path
        )
        raise
