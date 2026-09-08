"""Publica utilização e memória da GPU no CloudWatch.

Num task de 1 GPU, um sidecar (CW agent/DCGM) não enxerga a GPU — ela é assinada
ao container da API. Então a própria API lê `nvidia-smi` e publica a métrica. A
dimensão é o nome do serviço (estável), não o task-id, para não criar uma métrica
nova a cada deploy (cada métrica custa). Além de publicar, loga o valor, para que
o dado fique disponível mesmo sem permissão de publicar (ex: antes do IAM aplicar).

O parsing do nvidia-smi fica isolado (`parse_nvidia_smi`) para ser testável sem GPU.
"""
import logging
import os
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# O namespace tem que casar com a condicao cloudwatch:namespace da policy de
# PutMetricData (terraform/roles.tf). O Terraform injeta o valor de var.project_name;
# se divergir, a publicacao e negada silenciosamente.
GPU_METRICS_NAMESPACE = os.getenv("GPU_METRICS_NAMESPACE", "transcribe")
GPU_METRICS_DIMENSION = [{"Name": "ServiceName", "Value": "fastapi-task-gpu"}]
GPU_METRICS_INTERVAL = 60  # segundos


def parse_nvidia_smi(output: str) -> dict | None:
    """Traduz a saída CSV do nvidia-smi (util.gpu, mem.used, mem.total) em números.

    Formato esperado (--format=csv,noheader,nounits): "37, 4096, 16384".
    Retorna None se a linha não tiver as 3 colunas numéricas — o chamador pula
    a publicação naquele ciclo em vez de quebrar a thread.
    """
    lines = output.strip().splitlines()
    if not lines:
        return None
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 3:
        return None
    try:
        return {
            "utilization_gpu": float(parts[0]),
            "memory_used_mb": float(parts[1]),
            "memory_total_mb": float(parts[2]),
        }
    except ValueError:
        return None


def _read_gpu() -> dict | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        logger.exception("[gpu-metrics] falha ao rodar nvidia-smi")
        return None
    return parse_nvidia_smi(result.stdout)


def _publish(cw, m: dict) -> None:
    cw.put_metric_data(
        Namespace=GPU_METRICS_NAMESPACE,
        MetricData=[
            {"MetricName": "GPUUtilization", "Value": m["utilization_gpu"],
             "Unit": "Percent", "Dimensions": GPU_METRICS_DIMENSION},
            {"MetricName": "GPUMemoryUsedMB", "Value": m["memory_used_mb"],
             "Unit": "Megabytes", "Dimensions": GPU_METRICS_DIMENSION},
        ],
    )


def gpu_metrics_loop() -> None:
    import boto3
    from app.config import config

    cw = boto3.client("cloudwatch", region_name=config.aws.region)
    logger.info("[gpu-metrics] iniciando, intervalo=%ss", GPU_METRICS_INTERVAL)
    while True:
        m = _read_gpu()
        if m:
            logger.info(
                "[gpu-metrics] util=%.0f%% mem=%.0f/%.0f MB",
                m["utilization_gpu"], m["memory_used_mb"], m["memory_total_mb"],
            )
            try:
                _publish(cw, m)
            except Exception:
                logger.exception("[gpu-metrics] falha ao publicar no CloudWatch")
        time.sleep(GPU_METRICS_INTERVAL)


def start_gpu_metrics() -> None:
    threading.Thread(target=gpu_metrics_loop, daemon=True).start()
