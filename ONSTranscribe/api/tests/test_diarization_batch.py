"""Regressão: batch pyannote 32 estoura VRAM; default efetivo deve ser 8."""
from types import SimpleNamespace

import pytest

from app.services.diarization_batch import apply_diarization_batch_size


@pytest.mark.unit
def test_apply_diarization_batch_size_sets_segmentation_and_embedding():
    pipeline = SimpleNamespace(
        _segmentation=SimpleNamespace(batch_size=32),
        _embedding=SimpleNamespace(batch_size=32),
    )

    apply_diarization_batch_size(pipeline, 8)

    assert pipeline._segmentation.batch_size == 8
    assert pipeline._embedding.batch_size == 8


@pytest.mark.unit
def test_apply_diarization_batch_size_tolerates_missing_attrs():
    pipeline = SimpleNamespace()
    apply_diarization_batch_size(pipeline, 8)  # não levanta


@pytest.mark.unit
def test_config_default_diarization_batch_size_is_eight():
    from app.config import TranscriptionConfig

    assert TranscriptionConfig().diarization_batch_size == 8
