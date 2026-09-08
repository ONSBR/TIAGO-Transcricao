"""Ajuste de batch size do pyannote (segmentação/embedding).

Default interno 32 estoura VRAM em HOM:
  MemoryError: batch_size ( 32) is probably too large
"""


def apply_diarization_batch_size(pipeline, batch_size: int) -> None:
    """Define batch_size em _segmentation e _embedding quando existirem."""
    seg = getattr(pipeline, "_segmentation", None)
    if seg is not None and hasattr(seg, "batch_size"):
        seg.batch_size = batch_size

    emb = getattr(pipeline, "_embedding", None)
    if emb is not None and hasattr(emb, "batch_size"):
        emb.batch_size = batch_size
