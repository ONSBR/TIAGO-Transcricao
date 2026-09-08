#!/bin/bash
# Copia os notebooks de exemplo para finetuning/workspace/ (gitignored).
# Execute UMA VEZ antes de iniciar qualquer fine-tuning:
#
#   bash finetuning/scripts/setup_workspace.sh
#
# Os notebooks originais em finetune_whisper/, finetune_pyannote-diarization/ e
# subject-classification-transcription/ são templates — não os execute diretamente.
# Trabalhe sempre com as cópias em workspace/.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FINETUNING_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE="$FINETUNING_DIR/workspace"

echo "Configurando workspace em: $WORKSPACE"
echo ""

# ------------------------------------------------------------------
# Whisper fine-tuning
# ------------------------------------------------------------------
mkdir -p "$WORKSPACE/finetune_whisper"

cp "$FINETUNING_DIR/finetune_whisper/finetune_whisper.ipynb" \
   "$WORKSPACE/finetune_whisper/finetune_whisper.ipynb"

echo "  [OK] finetune_whisper/finetune_whisper.ipynb"

# ------------------------------------------------------------------
# PyAnnote diarization fine-tuning
# ------------------------------------------------------------------
mkdir -p "$WORKSPACE/finetune_pyannote-diarization"

for nb in 1_dataset-preparation.ipynb \
          2_diarization-fine-tuning.ipynb \
          3_model-tests.ipynb \
          4_model_upload.ipynb; do
  cp "$FINETUNING_DIR/finetune_pyannote-diarization/$nb" \
     "$WORKSPACE/finetune_pyannote-diarization/$nb"
done

cp "$FINETUNING_DIR/finetune_pyannote-diarization/database.yml" \
   "$WORKSPACE/finetune_pyannote-diarization/database.yml"
cp "$FINETUNING_DIR/finetune_pyannote-diarization/database-truth.yml" \
   "$WORKSPACE/finetune_pyannote-diarization/database-truth.yml"
cp "$FINETUNING_DIR/finetune_pyannote-diarization/pyproject.toml" \
   "$WORKSPACE/finetune_pyannote-diarization/pyproject.toml"

cp -r "$FINETUNING_DIR/finetune_pyannote-diarization/diarizers" \
      "$WORKSPACE/finetune_pyannote-diarization/diarizers"
cp -r "$FINETUNING_DIR/finetune_pyannote-diarization/dscore_master" \
      "$WORKSPACE/finetune_pyannote-diarization/dscore_master"

echo "  [OK] finetune_pyannote-diarization/ (4 notebooks + diarizers + dscore_master)"

# ------------------------------------------------------------------
# Subject classification
# ------------------------------------------------------------------
mkdir -p "$WORKSPACE/subject-classification"

cp "$FINETUNING_DIR/subject-classification-transcription/notebooks/classification_model.ipynb" \
   "$WORKSPACE/subject-classification/classification_model.ipynb"
cp "$FINETUNING_DIR/subject-classification-transcription/notebooks/pure_LLM.ipynb" \
   "$WORKSPACE/subject-classification/pure_LLM.ipynb"
cp "$FINETUNING_DIR/subject-classification-transcription/notebooks/openai_embedding_transformer.py" \
   "$WORKSPACE/subject-classification/openai_embedding_transformer.py"
cp "$FINETUNING_DIR/subject-classification-transcription/pyproject.toml" \
   "$WORKSPACE/subject-classification/pyproject.toml"

echo "  [OK] subject-classification/ (2 notebooks + pyproject.toml)"

# ------------------------------------------------------------------
# Resumo
# ------------------------------------------------------------------
echo ""
echo "Workspace pronto. Estrutura criada:"
echo "  $WORKSPACE/"
echo "  ├── finetune_whisper/"
echo "  ├── finetune_pyannote-diarization/"
echo "  └── subject-classification/"
echo ""
echo "IMPORTANTE:"
echo "  - Execute os notebooks SEMPRE a partir de workspace/"
echo "  - O workspace/ está no .gitignore — nada aqui vai ao repositório"
echo "  - Os templates originais NÃO devem ser executados diretamente"
echo ""
echo "Próximo passo:"
echo "  cd $WORKSPACE/finetune_whisper && jupyter notebook finetune_whisper.ipynb"
