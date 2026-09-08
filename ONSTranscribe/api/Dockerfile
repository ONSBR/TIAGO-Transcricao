# Usar imagem base com CUDA para suporte a GPU
FROM nvidia/cuda:13.0.0-cudnn-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y \
    build-essential \
    ffmpeg \
    libsndfile1-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar uv
RUN curl -Ls https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Alocador CUDA com segmentos expansíveis: reduz a fragmentação da VRAM (memória
# "reserved but unallocated" que o empty_cache não devolve), mitigando o CUDA OOM
# sob carga sustentada. Recomendado pela própria mensagem de erro do PyTorch.
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

WORKDIR /app

COPY pyproject.toml .python-version uv.lock ./

# Instalar dependências via uv
RUN uv lock && uv sync --frozen --no-dev

COPY . .

EXPOSE 80

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
