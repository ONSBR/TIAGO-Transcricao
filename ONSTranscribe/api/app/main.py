from fastapi import FastAPI
from app.api.v1.transcribe import router as transcribe_router
from app.services.sqs_worker import start_worker
from app.services.gpu_metrics import start_gpu_metrics
from app.utils.logging_config import setup_logging

setup_logging()
app = FastAPI()

app.include_router(transcribe_router, prefix="/api/v1")


@app.on_event("startup")
def _start_sqs_worker():
    # O worker consome a fila direto e transcreve, fora do caminho HTTP.
    # A API continua servindo /health e /api/v1/transcribe/status para o ALB e
    # consultas; a transcrição não passa mais por requisição síncrona.
    start_worker()


@app.on_event("startup")
def _start_gpu_metrics():
    # Publica utilização/memória da GPU no CloudWatch (e loga). Num task de 1 GPU
    # só a própria API enxerga a GPU, então é ela quem mede.
    start_gpu_metrics()
