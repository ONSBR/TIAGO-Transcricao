# Fine-tuning — Guia de Referência

> **Este diretório documenta a abordagem de fine-tuning adotada pelo ONS.**
>
> O que está aqui é uma **sugestão baseada em experiência real**, não um caminho obrigatório. Cada organização tem dados, infraestrutura e restrições diferentes — adapte o fluxo conforme necessário. Os notebooks são templates de ponto de partida, não receitas prontas.

---

## O que foi feito pelo ONS (e o que você pode fazer diferente)

O ONS treinou três modelos para adaptar a pipeline ao domínio do setor elétrico brasileiro:

| Modelo                    | Abordagem usada pelo ONS                                  | Alternativas possíveis                                          |
| ------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------- |
| **Whisper (ASR)**         | Fine-tuning de `openai/whisper-medium` com áudios do setor elétrico | Usar `whisper-large-v3` sem fine-tuning; usar outro modelo ASR compatível com HuggingFace |
| **PyAnnote (diarização)** | Fine-tuning do segmentation model usando RTTM gerados pela solução comercial anterior, como anotação silver-label | Gerar RTTM manualmente; usar `pyannote/speaker-diarization-3.1` sem fine-tuning; usar outro diarizador |
| **Classificador de assuntos** | SVM + TF-IDF com embeddings, treinado em transcrições anotadas | LLM zero-shot (sem dataset), embeddings semânticos, modelos de classificação mais modernos |

> **Usar os modelos sem fine-tuning também é válido.** Para vocabulário técnico muito específico (siglas, nomes de equipamentos, localidades), o fine-tuning do Whisper traz ganho mensurável em WER. Para domínios mais próximos do português geral, os modelos base podem ser suficientes.

---

## Estrutura dos notebooks

| Modelo                    | Pasta                                     | Finalidade                                    |
| ------------------------- | ----------------------------------------- | --------------------------------------------- |
| Whisper (ASR)             | `finetune_whisper/`                     | Transcrição de fala para texto em PT-BR     |
| PyAnnote (diarização)   | `finetune_pyannote-diarization/`        | Identificação de locutores                  |
| Classificador de assuntos | `subject-classification-transcription/` | Categorização semântica das transcrições |

---

## Segurança git — leia antes de começar

O `.gitignore` desta pasta exclui automaticamente:

| O que é excluído                 | Por quê                                                    |
| ---------------------------------- | ----------------------------------------------------------- |
| `data/`                          | Áudios, transcrições e datasets de treinamento           |
| `workspace/`                     | Cópias dos notebooks com outputs de célula e paths locais |
| `**/checkpoint-*/`               | Checkpoints intermediários do Trainer                      |
| `**/*.safetensors`, `**/*.bin` | Binários dos modelos treinados                             |
| `**/*.joblib`, `**/*.npy`      | Classificador treinado                                      |
| `**/runs/`, `**/*.tfevents.*`  | Logs do TensorBoard                                         |

**Nunca commitar:**

- Arquivos de áudio (`.wav`, `.mp3`, `.ogg`, `.flac`)
- CSVs com transcrições reais
- Checkpoints ou modelos treinados com dados proprietários
- Arquivos com paths absolutos do seu ambiente local
- Tokens de HuggingFace ou chaves de API

---

## Pré-requisitos

- **Python 3.10+** e **[uv](https://docs.astral.sh/uv/)** instalado
- **GPU com CUDA** (recomendado: 16 GB VRAM ou mais)
  - Whisper medium: mínimo 8 GB VRAM, recomendado 16 GB
  - PyAnnote segmentation: mínimo 8 GB VRAM
- **HuggingFace token** para modelos com acesso restrito (ex: `pyannote/segmentation-3.0`)
  - Aceitar termos do modelo em: huggingface.co/pyannote/segmentation-3.0
  - Criar token em: huggingface.co/settings/tokens
  - Exportar: `export HF_TOKEN=hf_...`

---

## Setup do Workspace

Os notebooks originais **não devem ser executados diretamente** — eles são o template. O trabalho real acontece em `workspace/` (gitignored).

### 1. Copiar os notebooks para o workspace

```bash
bash finetuning/scripts/setup_workspace.sh
```

Isso cria:

```
finetuning/workspace/              ← gitignored — nunca vai ao git
├── finetune_whisper/
│   └── finetune_whisper.ipynb
├── finetune_pyannote-diarization/
│   ├── 1_dataset-preparation.ipynb
│   ├── 2_diarization-fine-tuning.ipynb
│   ├── 3_model-tests.ipynb
│   ├── 4_model_upload.ipynb
│   ├── database.yml
│   └── diarizers/
└── subject-classification/
    ├── classification_model.ipynb
    └── pure_LLM.ipynb
```

### 2. Formato dos arquivos de áudio

| Propriedade | Valor esperado | Observação |
|---|---|---|
| Formato do arquivo | `.wav` (PCM) | MP3/M4A requerem ffmpeg instalado |
| Taxa de amostragem | **16 kHz** | O notebook reamostra se necessário, mas 16 kHz evita perda de qualidade |
| Canais | **Mono** | Estéreo é convertido para mono automaticamente |
| Profundidade de bits | 16-bit | 32-bit float também aceito |
| Duração por amostra | 5 – 30 segundos | Amostras < 1 s ou > 30 s tendem a degradar o treinamento do Whisper |
| Codificação | PCM linear | Não usar WAV comprimido (ADPCM, GSM, etc.) |

**Verificar antes de treinar:**
```bash
# Inspecionar um arquivo de exemplo (requer sox ou ffprobe)
soxi audio_001.wav
# ou
ffprobe -i audio_001.wav -show_streams -select_streams a 2>&1 | grep -E "sample_rate|channels|codec"
```

**Converter para o formato correto se necessário:**
```bash
# Converter para WAV mono 16 kHz 16-bit (requer ffmpeg)
ffmpeg -i entrada.mp3 -ar 16000 -ac 1 -sample_fmt s16 saida.wav
```

### 3. Padrão de nomenclatura dos arquivos de áudio

> **Contexto:** Este padrão foi definido pelo ONS para representar metadados de chamadas telefônicas do setor elétrico. **Se a pipeline for usada em outro domínio, o padrão pode e deve ser adaptado** — veja como em `api/terraform/lambdas/transcribe_audio_to_elastic_search/helpers/process_destination_helper.py`.

Se os áudios serão processados pela pipeline completa (Lambda → ECS → OpenSearch), o nome do arquivo deve seguir o padrão de **7 campos separados por `_`** adotado pelo ONS:

```
YYYYMMDD_HHMMSS_RAMAL_OPERADOR_DURACAO_DIRECAO_AGENTE.wav
```

| Campo | Posição | Exemplo | Descrição |
|---|---|---|---|
| Data | 0 | `20240115` | Data no formato YYYYMMDD |
| Hora | 1 | `103045` | Hora no formato HHMMSS |
| Ramal | 2 | `1234` | Número do ramal/extensão |
| Operador | 3 | `operador01` | Identificador do locutor interno (centro de controle) |
| Duração | 4 | `180` | Duração em segundos |
| Direção | 5 | `I` ou `O` | `I` = inbound (agente liga para o centro), `O` = outbound (centro liga para o agente) |
| Agente | 6 | `agente01` | Identificador do locutor externo |

Exemplo: `20240115_103045_1234_operador01_180_I_agente01.wav`

> **Atenção:** a Lambda usa `split("_", 7)` para extrair esses campos. Um arquivo fora desse padrão causa `IndexError` silencioso e a transcrição não é processada.

### 4. Preparar o dataset CSV

Coloque seus áudios e transcrições em `finetuning/data/` (gitignored). O formato mínimo esperado pelos notebooks é um CSV com duas colunas:

```csv
file,transcription
audio_001.wav,"texto transcrito aqui"
audio_002.wav,"outro trecho de fala"
```

---

## Fine-tuning do Whisper (ASR)

> **Abordagem do ONS:** fine-tuning de `openai/whisper-medium` com áudios de chamadas telefônicas do setor elétrico, focando em vocabulário técnico (nomes de equipamentos, localidades, siglas do setor). O modelo base pode ser trocado para `whisper-large-v3` se houver mais GPU disponível, ou para `whisper-small` se o objetivo for velocidade de inferência.

**Notebook:** `workspace/finetune_whisper/finetune_whisper.ipynb`

### Instalar dependências

```bash
cd finetuning/workspace/finetune_whisper
uv sync
```

> O `pyproject.toml` nesta pasta define as dependências (torch, transformers, datasets, etc.).
> Edite a linha `find-links` para a versão de CUDA da sua GPU:
>
> - CUDA 12.8 → `cu128` (RTX 40/50 series)
> - CUDA 12.4 → `cu124`
> - CUDA 11.8 → `cu118`

### Abrir o notebook

```bash
uv run jupyter notebook finetune_whisper.ipynb
```

### Configurar na célula de config

Ajuste as variáveis na primeira célula do notebook:

```python
AUDIO_DATA_PATH      = "/caminho/para/seus/audios/"   # pasta com .wav
DATASET_CSV_FILENAME = "dataset.csv"                   # CSV com file,transcription
OUTPUT_DATASET_NAME  = "asr_dataset"
OUTPUT_DIR           = "./whisper_finetuned"
MODEL_OUTPUT_NAME    = "whisper_model_finetuned"
```

Ou via variáveis de ambiente antes de abrir o notebook:

```bash
export AUDIO_DATA_PATH=/caminho/para/seus/audios/
export DATASET_CSV_FILENAME=dataset.csv
```

### Compatibilidades conhecidas

| Versão                  | Parâmetro afetado                         | Correção                                              |
| ------------------------ | ------------------------------------------ | ------------------------------------------------------- |
| `transformers >= 4.46` | `Seq2SeqTrainer(tokenizer=...)` removido | Substituir por `processing_class=`                    |
| `datasets >= 3.0`      | `trust_remote_code` removido             | Usar `datasets<3.0` para datasets com loading scripts |

---

## Fine-tuning do PyAnnote (Diarização)

> **Abordagem do ONS:** uso da **solução comercial anterior** como ferramenta de anotação automática, para gerar arquivos RTTM (rótulos de locutores por segmento de tempo) a partir dos áudios reais. Esses RTTM servem como "silver labels" (anotação automática, não revisada por humano) para o fine-tuning do modelo de segmentação `pyannote/segmentation-3.0`.
>
> Esta é uma abordagem pragmática quando não há orçamento para anotação manual. **A qualidade do modelo resultante depende da qualidade da ferramenta de anotação escolhida no seu domínio.** Alternativas incluem: anotação manual com [Audacity](https://www.audacityteam.org/) + exportação RTTM, uso de outras ferramentas de diarização para gerar os rótulos, ou compra de datasets anotados.

**Notebooks:** `workspace/finetune_pyannote-diarization/` — fluxo: 1 → 2 → 3 → 4

### Instalar dependências

```bash
cd finetuning/workspace/finetune_pyannote-diarization
uv sync
```

### Configurar variáveis de ambiente

```bash
export HF_TOKEN=hf_...                              # token HuggingFace
export HF_DATASET_REPO=seu-usuario/nome-do-dataset  # repo HF onde o dataset será publicado
export HF_FINETUNED_MODEL=seu-usuario/segmentation-finetuned  # repo HF do modelo resultante
export TRANSCRIBE_BUCKET=seu-bucket-s3              # bucket S3 para os áudios (usado pelo notebook 1)
export TRANSCRIBE_LANG=pt-BR                        # idioma para a ferramenta de anotação
```

### Fluxo dos notebooks

1. **`1_dataset-preparation.ipynb`** — *(Abordagem ONS)* Envia os áudios para o S3 e cria jobs na ferramenta de anotação para gerar RTTM e UEM automaticamente. Se você tiver anotações manuais, pode pular este notebook e partir direto para o 2.
2. **`2_diarization-fine-tuning.ipynb`** — Fine-tuning do `pyannote/segmentation-3.0` com os arquivos RTTM gerados.
3. **`3_model-tests.ipynb`** — Avalia DER (Diarization Error Rate) nos regimes forgiving / fair / full.
4. **`4_model_upload.ipynb`** — Publica o modelo fine-tuned no HuggingFace Hub (privado ou público).

### Converter o checkpoint para formato HuggingFace antes do upload

> **Atenção — passo obrigatório antes de enviar para o S3.**
>
> O treinamento produz um checkpoint Lightning (`best.ckpt` ou `pytorch_model.bin`) no formato nativo do PyAnnote. A API espera o formato HuggingFace (`model.safetensors` + `config.json`). Enviar o `.bin` diretamente para o S3 faz a aplicação falhar com `_pickle.UnpicklingError` ao inicializar.

Se você **fez fine-tuning** (saiu do notebook 2 com um `.ckpt`):

O notebook `4_model_upload.ipynb` já faz essa conversão — ele extrai apenas os tensores, remove o prefixo `model.` adicionado pelo Lightning, e salva como SafeTensors. Execute a seção **"Model Publishing v1"** do notebook até o passo de S3.

Se você **baixou o modelo base sem fine-tuning** (seguindo o README de download do HuggingFace) e o arquivo local é `pytorch_model.bin`:

Execute o script de conversão a partir da raiz do repositório `ONSTranscribe`:

```bash
cd ONSTranscribe

uv run \
  --with torch \
  --with safetensors \
  --with pyyaml \
  --with packaging \
  --with numpy \
  python finetuning/scripts/convert_segmentation_to_hf_format.py \
    --model-dir finetuning/workspace/models/segmentation
```

O script lê `finetuning/workspace/models/segmentation/pytorch_model.bin` e gera:

```
finetuning/workspace/models/segmentation/
├── config.yaml          ← original (não enviado ao S3)
├── pytorch_model.bin    ← original (não enviado ao S3)
├── config.json          ← GERADO — enviar para S3
└── model.safetensors    ← GERADO — enviar para S3
```

Depois do script, faça o upload apenas dos arquivos gerados:

```bash
aws s3 cp finetuning/workspace/models/segmentation/model.safetensors \
  s3://exemplo-audio-bucket/models/segmentation-finetuned/model.safetensors \
  --profile meu-profile-aws --region us-east-1

aws s3 cp finetuning/workspace/models/segmentation/config.json \
  s3://exemplo-audio-bucket/models/segmentation-finetuned/config.json \
  --profile meu-profile-aws --region us-east-1
```

### Formato dos dados necessários

```
dataset/
├── audios/          ← seus .wav (URI.wav)
├── rttms/
│   ├── train/       ← URI.rttm  (gerado pelo notebook 1 via Transcribe, ou manualmente)
│   ├── dev/
│   └── test/
├── uems/
│   ├── train/       ← URI.uem
│   ├── dev/
│   └── test/
└── lists/
    ├── train.txt    ← uma URI por linha, sem extensão
    ├── dev.txt
    └── test.txt
```

---

## Treino do Classificador de Assuntos

> **Abordagem do ONS:** classificador SVM com embeddings TF-IDF, treinado em transcrições reais previamente rotuladas por categoria. É uma abordagem leve e interpretável, que funciona bem quando há um conjunto de categorias bem definidas e dataset anotado disponível. Quando não há dataset anotado, o notebook `pure_LLM.ipynb` permite gerar os rótulos via LLM (abordagem zero-shot ou few-shot), que foi usado pelo ONS na fase inicial antes de acumular dados suficientes para o SVM.

**Notebooks:** `workspace/subject-classification/`

### Instalar dependências

```bash
cd finetuning/workspace/subject-classification
uv sync
```

### Configurar variáveis de ambiente

```bash
export TRAINING_CSV=/caminho/para/dataset_labels.csv
export VALIDATION_CSV=/caminho/para/dataset_validation.csv
export CATCHALL_CATEGORY="Outros assuntos"
export DEFAULT_THRESHOLD=0.5
export OPENAI_API_KEY=sk-...   # necessário apenas se usar embeddings OpenAI
```

### Dois notebooks disponíveis

| Notebook                       | Abordagem                 | Quando usar                                |
| ------------------------------ | ------------------------- | ------------------------------------------ |
| `classification_model.ipynb` | SVM + TF-IDF + embeddings | Dataset anotado disponível (abordagem usada pelo ONS em produção) |
| `pure_LLM.ipynb`             | LLM zero-shot / few-shot  | Sem dataset anotado — gera labels via LLM (abordagem usada pelo ONS na fase inicial) |

O `pure_LLM.ipynb` suporta três provedores via variável `LLM_PROVIDER`:

```bash
export LLM_PROVIDER=bedrock     # AWS Bedrock (padrão — usado pelo ONS)
export LLM_PROVIDER=openai      # OpenAI
export LLM_PROVIDER=anthropic   # Anthropic direto
```

### Formato do CSV de treino

```csv
transcription,label
"texto da transcrição","Categoria A"
"outro trecho","Categoria B"
```

---

## Onde salvar os modelos treinados

Os modelos resultantes do treinamento **não devem ficar no repositório**. Opções:

- **HuggingFace Hub** (privado ou público) — use os notebooks de upload; o ONS usou repo privado para os modelos fine-tuned
- **S3** — upload direto para o bucket da pipeline (opção mais simples se não quiser depender do HF Hub)
- **Local** — qualquer pasta fora do repositório

O `TRANSCRIBE_S3_MODEL_PATH` e `TRANSCRIBE_S3_SEGMENTATION_MODEL_PATH` no `terraform.tfvars` apontam para onde a pipeline vai buscar os modelos no S3.

---

## Estrutura exata de arquivos exigida no S3

> **Esta seção descreve o que precisa estar no S3 para a API inicializar corretamente.**
> A API baixa os modelos do S3 no startup do container ECS. Se qualquer arquivo estiver faltando ou no formato errado, a aplicação falhará ao processar o primeiro áudio.

O bucket usado é o configurado em `terraform.tfvars` (`bucket_name`). Nos exemplos abaixo ele aparece como `exemplo-audio-bucket`.

### Modelo de segmentação (diarização) — `models/segmentation-finetuned/`

```
s3://exemplo-audio-bucket/models/segmentation-finetuned/
├── model.safetensors    ← OBRIGATÓRIO — pesos do modelo em formato SafeTensors
└── config.json          ← OBRIGATÓRIO — configuração da arquitetura SegmentationModel
```

**Formato dos arquivos:**

- `model.safetensors` — gerado pelo script de conversão ou pelo notebook `4_model_upload.ipynb`. **Nunca enviar `pytorch_model.bin` diretamente** — esse formato contém objetos pickle do PyAnnote que o PyTorch 2.x rejeita.
- `config.json` — gerado automaticamente pelo script de conversão. Conteúdo esperado:

```json
{
  "architectures": ["SegmentationModel"],
  "chunk_duration": 10,
  "max_speakers_per_chunk": 3,
  "max_speakers_per_frame": 2,
  "min_duration": null,
  "model_type": "pyannet",
  "sample_rate": 16000,
  "torch_dtype": "float32",
  "transformers_version": "4.46.0",
  "warm_up": [0.0, 0.0],
  "weigh_by_cardinality": false
}
```

### Modelo Whisper (ASR) — `models/whisper-finetuned/`

```
s3://exemplo-audio-bucket/models/whisper-finetuned/
├── config.json
├── generation_config.json
├── model.safetensors          ← ou pytorch_model.bin (Whisper usa formato HF nativo)
├── preprocessor_config.json
├── special_tokens_map.json
├── tokenizer.json
├── tokenizer_config.json
├── merges.txt                 ← (alguns checkpoints incluem)
└── vocab.json                 ← (alguns checkpoints incluem)
```

Todos esses arquivos são produzidos automaticamente pelo `Seq2SeqTrainer` ao salvar o modelo com `save_pretrained()`. O Whisper **não precisa de conversão** — ele já sai do treinamento no formato HuggingFace.

### Classificador de assuntos — `models/subject-classification-model/`

```
s3://exemplo-audio-bucket/models/subject-classification-model/
├── pipeline_embeddings_tfidf_svm.joblib   ← OBRIGATÓRIO — pipeline sklearn serializado
└── classes.npy                            ← OBRIGATÓRIO — array numpy com as classes
```

Esses arquivos são gerados pelo notebook `classification_model.ipynb` ao final do treinamento.

### Verificar o que está no S3

```bash
# Listar todos os arquivos dos três modelos
aws s3 ls s3://exemplo-audio-bucket/models/ --recursive --profile meu-profile-aws --region us-east-1
```

### Pastas S3 criadas pela pipeline (não precisam existir antes do deploy)

As pastas abaixo são criadas automaticamente pela pipeline quando os primeiros áudios são processados:

```
s3://exemplo-audio-bucket/
├── audios/salas/audio/          ← trigger: depositar .wav aqui aciona o fluxo
├── audios/salas/transcriptions/ ← saída: JSONs de transcrição gerados aqui
└── audios/salas/vocabulary/     ← vocabulário customizado (opcional)
```

> Para testar o pipeline completo, copie arquivos `.wav` para `audios/salas/audio/` com o padrão de nomenclatura documentado na seção anterior. O SNS Event Notification do bucket aciona a Lambda automaticamente.
