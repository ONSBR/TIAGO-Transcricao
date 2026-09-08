# Notebook de Finetune do Whisper Medium

Notebook para fine-tuning do modelo `openai/whisper-medium` em dados de áudio em português para transcrição de voz (ASR).

## Como usar

1. Configure as variáveis na célula de configuração no topo do notebook:

   - `AUDIO_DATA_PATH`: caminho para a pasta com os arquivos `.wav` e o CSV do dataset
   - `DATASET_CSV_FILENAME`: nome do arquivo CSV com as transcrições
   - `OUTPUT_DATASET_NAME`: nome para salvar o dataset processado
   - `MODEL_OUTPUT_NAME`: nome para salvar o modelo treinado
   - `OUTPUT_DIR`: diretório de saída do treinamento
2. Faça login no HuggingFace via `notebook_login()` ou configure o secret `HF_TOKEN` no Colab.

## Formato esperado do CSV

O arquivo CSV deve conter as colunas:

- `audios`: nome do arquivo de áudio (ex: `audio_001.wav`)
- `transcription`: texto de referência da transcrição
- `referencia`: transcrição de referência para treinamento
- `audio_classes`: classe do áudio (filtrar `"Sem áudio"` e `"Inadequado"`)
