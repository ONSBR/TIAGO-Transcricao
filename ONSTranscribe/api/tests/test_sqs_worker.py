"""Testes da lógica pura do worker SQS desacoplado.

O worker consome a fila direto (sem HTTP), então precisa reproduzir a tradução
que hoje vive no consumer Lambda: de {raw_key, key} para os campos do job
(source_link, destination_link, job_id). Estes testes travam essa tradução e a
regra de idempotência, sem tocar AWS nem carregar o modelo.
"""
from app.services.sqs_worker import (
    _make_sqs_client,
    build_job,
    message_should_delete,
    should_skip,
)


def test_build_job_deriva_source_destino_e_jobid():
    body = {
        "raw_key": "audios/salas/LOC1/20200705_235959_8048_CENTRO_1_Geracao.wav",
        "key": "LOC1/20200705_235959_8048_CENTRO_1_Geracao",
    }
    job = build_job(body, "exemplo-audio-bucket", "audios/salas/transcriptions/")

    assert job["source_link"] == (
        "s3://exemplo-audio-bucket/audios/salas/LOC1/"
        "20200705_235959_8048_CENTRO_1_Geracao.wav"
    )
    assert job["destination_link"] == (
        "s3://exemplo-audio-bucket/audios/salas/transcriptions/"
        "20200705_235959_8048_CENTRO_1_Geracao.json"
    )
    assert job["job_id"] == "20200705_235959_8048_CENTRO_1_Geracao"
    assert job["language"] == "pt"


def test_build_job_key_com_wav_nao_gera_destino_wav_json():
    """Regressão poison: stress/producer que manda key com .wav gerava *.wav.json
    no prefixo transcriptions; o SNS re-enfileirava o JSON como áudio.
    """
    body = {
        "raw_key": (
            "testes/oom-repro/2026-07-20/audios/salas/LOC1/"
            "20260716_190728_8052_CENTRO_1-GERACAO-2_82_I_AGENTE_1_EXEMPLO_1.wav"
        ),
        "key": (
            "testes/oom-repro/2026-07-20/audios/salas/LOC1/"
            "20260716_190728_8052_CENTRO_1-GERACAO-2_82_I_AGENTE_1_EXEMPLO_1.wav"
        ),
    }
    job = build_job(body, "exemplo-audio-bucket", "audios/salas/transcriptions/")

    assert job["destination_link"].endswith(
        "transcriptions/20260716_190728_8052_CENTRO_1-GERACAO-2_82_I_AGENTE_1_EXEMPLO_1.json"
    )
    assert ".wav.json" not in job["destination_link"]
    assert job["job_id"] == "20260716_190728_8052_CENTRO_1-GERACAO-2_82_I_AGENTE_1_EXEMPLO_1"


def test_build_job_key_com_e_sem_wav_mesmo_destino():
    base = "audios/salas/LOC1/audio_id_foo"
    with_ext = build_job(
        {"raw_key": f"{base}.wav", "key": f"{base}.wav"},
        "b",
        "audios/salas/transcriptions/",
    )
    without = build_job(
        {"raw_key": f"{base}.wav", "key": base},
        "b",
        "audios/salas/transcriptions/",
    )
    assert with_ext["destination_link"] == without["destination_link"]
    assert with_ext["job_id"] == without["job_id"]


def test_should_skip_pula_completed_e_in_progress():
    assert should_skip("COMPLETED") is True
    assert should_skip("IN_PROGRESS") is True
    assert should_skip("PENDING") is False
    assert should_skip("FAILED") is False
    assert should_skip(None) is False


def test_message_should_delete_terminais():
    assert message_should_delete("COMPLETED") is True
    assert message_should_delete("FAILED") is True
    assert message_should_delete("IN_PROGRESS") is True
    assert message_should_delete("PENDING") is False


def test_make_sqs_client_passa_region_explicita(monkeypatch):
    # regressão: sem region_name o boto3 trava resolvendo região pelo IMDS no ECS,
    # e a thread do worker ficava presa na criação do cliente. A região tem que ir
    # explícita, igual aos outros clientes do app.
    import boto3

    captured = {}

    def fake_client(service, region_name=None):
        captured["service"] = service
        captured["region_name"] = region_name
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)
    _make_sqs_client("us-east-1")

    assert captured == {"service": "sqs", "region_name": "us-east-1"}
