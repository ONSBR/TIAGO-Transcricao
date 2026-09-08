# Finetuning PyAnnote — Speaker Diarization

Este diretório contém os notebooks e scripts para preparar um dataset de diarização e fazer fine-tuning do modelo **pyannote/segmentation-3.0** com áudios do seu domínio.

---

## Pré-requisitos

1. Instale o [Astral UV](https://docs.astral.sh/uv/) e baixe as dependências:
```bash
uv sync
```

2. Configure as variáveis de ambiente antes de executar os notebooks:

```bash
export AWS_PROFILE=meu-profile-aws
export AWS_REGION=us-east-1
export TRANSCRIBE_LANG=pt-BR
export TRANSCRIBE_BUCKET=meu-bucket-s3
export ANNOTATIONS_FILE=annotations.xlsx
export HF_TOKEN=hf_...          # token do Hugging Face Hub
export HF_DATASET_REPO=seu-usuario/nome-do-dataset
export HF_FINETUNED_MODEL=seu-usuario/segmentation-finetuned
export HF_MODEL_REPO=seu-usuario/segmentation-finetuned
export LOCAL_MODEL_CKPT=lightning_logs/version_0/checkpoints/best.ckpt
export TEST_AUDIO_PATH=dataset/audios/sample.wav
export RESULTS_DIR=results        # pasta com os RTTMs de comparação
```

---

## Fluxo de trabalho

```
1_dataset-preparation.ipynb   ← prepara RTTM/UEM a partir dos seus áudios
         ↓
2_diarization-fine-tuning.ipynb  ← faz o fine-tuning do modelo
         ↓
3_model-tests.ipynb           ← avalia e compara modelos (DER)
         ↓
4_model_upload.ipynb          ← publica o modelo no Hugging Face Hub
```

---

## Estrutura de pastas esperada

```text
dataset/
├─ audios/              ← seus arquivos .wav (URI.wav)
├─ rttms/
│  ├─ train/            ← URI.rttm
│  ├─ dev/
│  └─ test/
├─ uems/
│  ├─ train/            ← URI.uem
│  ├─ dev/
│  └─ test/
├─ lists/
│  ├─ train.txt         ← uma URI por linha (sem extensão)
│  ├─ dev.txt
│  └─ test.txt
└─ transcribe_jsons/    ← JSONs da ferramenta de anotação (gerados pelo notebook 1)
```

> **URI** = nome-base do arquivo sem extensão (ex: `audio_001`).

---

## Formato RTTM

Cada linha descreve um segmento de fala:

```
SPEAKER <uri> 1 <inicio> <duracao> <NA> <NA> <speaker_id> <NA>
```

Exemplo:
```
SPEAKER audio_001 1 4.179 6.270 <NA> <NA> SPK_1 <NA>
```

---

## Formato UEM

Define a janela do áudio avaliada nas métricas (DER/JER):

```
<uri> 1 <inicio> <fim>
```

Exemplo:
```
audio_001 1 0.000 17.400
```

---

## Protocolo PyAnnote (database.yml)

O arquivo `database.yml` (e `database-truth.yml`) configura o protocolo do PyAnnote apontando para os splits:

```yaml
Databases:
  CustomDataset: dataset/audios/{uri}.wav

Protocols:
  CustomDataset:
    SpeakerDiarization:
      default:
        scope: file
        train:
          uri: dataset/lists/train.txt
          annotation: dataset/rttms/train/{uri}.rttm
          annotated: dataset/uems/train/{uri}.uem
        development:
          uri: dataset/lists/dev.txt
          annotation: dataset/rttms/dev/{uri}.rttm
          annotated: dataset/uems/dev/{uri}.uem
        test:
          uri: dataset/lists/test.txt
          annotation: dataset/rttms/test/{uri}.rttm
          annotated: dataset/uems/test/{uri}.uem
```

---

## Pipeline de preparação de dados (notebook 1)

1. **Descoberta dos `.wav`** — varre `dataset/audios/` e filtra pelos nomes presentes na planilha de anotações (`ANNOTATIONS_FILE`).
2. **Split 80/10/10** — embaralha com `seed=42` e gera `train.txt`, `dev.txt`, `test.txt`.
3. **Upload assíncrono para S3** — usa `aioboto3` com `MAX_CONCURRENT=50` uploads em paralelo.
4. **Jobs na ferramenta de anotação** — 50 jobs em paralelo com `LanguageCode=TRANSCRIBE_LANG` e `MaxSpeakerLabels=2`.
5. **Polling com backoff** — até `MAX_POLL_CONCURRENCY=12` chamadas simultâneas; timeout de 1h por job.
6. **Download e parsing** — converte o JSON do Transcribe para `.rttm` (speaker_labels ou channel_labels).
7. **Geração do UEM** — lê duração via `soundfile` e escreve a janela `[0.000, duração]`.

---

## Fine-tuning (notebook 2)

Baseado na biblioteca [diarizers](https://github.com/huggingface/diarizers):

```bash
python diarizers/train_segmentation.py \
  --dataset_name=$HF_DATASET_NAME \
  --model_name_or_path=pyannote/segmentation-3.0 \
  --output_dir=./speaker-segmentation-finetuned \
  --do_train --do_eval \
  --num_train_epochs=15 \
  --per_device_train_batch_size=16 \
  --learning_rate=3e-3
```

---

## Métricas de avaliação (DER)

DER (Diarization Error Rate) = (Miss + False Alarm + Confusion) / Duração × 100%

Três regimes utilizados:
- **forgiving**: collar=0.25s, ignora overlaps
- **fair**: collar=0.25s, considera overlaps
- **full**: collar=0.0s, considera overlaps (mais rígido)

> Quanto menor o DER, melhor.
