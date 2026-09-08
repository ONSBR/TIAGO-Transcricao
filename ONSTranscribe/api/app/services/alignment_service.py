"""Alinhamento forçado de texto à sequência de tokens do áudio via Levenshtein."""
import re
from collections import defaultdict

import librosa
import numpy as np
import torch
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

_WAV2VEC2_MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese"
_CHAR_PATTERN = re.compile(r"[^a-z0-9çáéíóúàãõ]")


def process_audio_with_wav2vec2(audio_path: str) -> list[dict]:
    """Extrai tokens alinhados no tempo usando Wav2Vec2.

    Args:
        audio_path: Caminho para o arquivo de áudio.

    Returns:
        Lista de dicts com 'token_text', 'start_time' e 'end_time'.
    """
    processor = Wav2Vec2Processor.from_pretrained(_WAV2VEC2_MODEL_ID)
    model = Wav2Vec2ForCTC.from_pretrained(_WAV2VEC2_MODEL_ID)

    audio, sr = librosa.load(audio_path, sr=16000)
    audio_duration = len(audio) / 16000.0

    input_values = processor(audio, return_tensors="pt", sampling_rate=16000).input_values
    with torch.no_grad():
        logits = model(input_values).logits

    predicted_ids = torch.argmax(logits, dim=-1)[0].cpu().numpy()
    num_frames = predicted_ids.shape[0]
    time_per_frame = audio_duration / num_frames

    aligned_tokens: list[dict] = []
    prev_id = None

    for i, tid in enumerate(predicted_ids):
        token_text = processor.tokenizer._convert_id_to_token(int(tid))
        if tid == processor.tokenizer.pad_token_id:
            prev_id = tid
            continue
        if tid == prev_id and aligned_tokens:
            aligned_tokens[-1]["end_time"] = (i + 1) * time_per_frame
        else:
            aligned_tokens.append({
                "token_text": token_text.lower(),
                "start_time": i * time_per_frame,
                "end_time": (i + 1) * time_per_frame,
            })
        prev_id = tid

    return aligned_tokens


def levenshtein_alignment(ref_seq: list, hyp_seq: list) -> list[tuple]:
    """Alinha duas sequências de caracteres usando programação dinâmica de Levenshtein.

    Args:
        ref_seq: Sequência de referência (texto corrigido).
        hyp_seq: Sequência hipótese (tokens reconhecidos).

    Returns:
        Lista de (i, j, op) onde op ∈ {'match', 'sub', 'ins', 'del'}.
    """
    len_r, len_h = len(ref_seq), len(hyp_seq)
    dp = np.zeros((len_r + 1, len_h + 1), dtype=int)

    for i in range(len_r + 1):
        dp[i][0] = i
    for j in range(len_h + 1):
        dp[0][j] = j

    for i in range(1, len_r + 1):
        for j in range(1, len_h + 1):
            if ref_seq[i - 1] == hyp_seq[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    alignments: list[tuple] = []
    i, j = len_r, len_h

    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref_seq[i - 1] == hyp_seq[j - 1]:
            alignments.append((i - 1, j - 1, "match"))
            i -= 1
            j -= 1
        else:
            candidates = []
            if i > 0:
                candidates.append((dp[i - 1][j], "del"))
            if j > 0:
                candidates.append((dp[i][j - 1], "ins"))
            if i > 0 and j > 0:
                candidates.append((dp[i - 1][j - 1], "sub"))
            best = min(candidates, key=lambda x: x[0])
            if best[1] == "del":
                alignments.append((i - 1, None, "del"))
                i -= 1
            elif best[1] == "ins":
                alignments.append((None, j - 1, "ins"))
                j -= 1
            else:
                alignments.append((i - 1, j - 1, "sub"))
                i -= 1
                j -= 1

    alignments.reverse()
    return alignments


def forced_align_text(corrected_text: str, aligned_tokens: list[dict]) -> list[dict]:
    """Mapeia cada palavra do texto corrigido a tempos de início/fim via Levenshtein.

    Args:
        corrected_text: Texto corrigido a alinhar.
        aligned_tokens: Tokens alinhados no tempo (saída de process_audio_with_wav2vec2).

    Returns:
        Lista de dicts com 'word', 'start' e 'end' para cada palavra.
    """
    recognized_seq: list[str] = []
    idx_map: list[int] = []
    for t_i, token in enumerate(aligned_tokens):
        clean = _CHAR_PATTERN.sub("", token["token_text"])
        for c in clean:
            recognized_seq.append(c)
            idx_map.append(t_i)

    ref_seq: list[str] = [
        c for c in corrected_text.lower()
        if c.isalpha() or c.isdigit() or c in "çáéíóúàãõ"
    ]

    alignment_ops = levenshtein_alignment(ref_seq, recognized_seq)

    words = corrected_text.split()
    ref_word_map: list[int] = []
    for w_i, word in enumerate(words):
        clean_w = "".join(
            c for c in word.lower() if c.isalpha() or c.isdigit() or c in "çáéíóúàãõ"
        )
        ref_word_map.extend([w_i] * len(clean_w))

    word_token_ranges: dict[int, list[int]] = defaultdict(list)
    for i, j, op in alignment_ops:
        if op in ("match", "sub") and i is not None and j is not None:
            if i < len(ref_word_map):
                word_token_ranges[ref_word_map[i]].append(idx_map[j])

    aligned_words: list[dict] = []
    for w_i, word in enumerate(words):
        token_indices = word_token_ranges.get(w_i, [])
        if not token_indices:
            aligned_words.append({"word": word, "start": 0.0, "end": 0.0})
        else:
            aligned_words.append({
                "word": word,
                "start": min(aligned_tokens[t]["start_time"] for t in token_indices),
                "end": max(aligned_tokens[t]["end_time"] for t in token_indices),
            })

    return aligned_words


def process_aligned_words_to_aws_format(aligned_words: list[dict]) -> dict:
    """Converte palavras alinhadas para o formato de saída com speakers alternados.

    Args:
        aligned_words: Lista de dicts com 'word', 'start' e 'end'.

    Returns:
        Dicionário no formato {'transcricaoAudio': {'speakers': [...]}}.
    """
    aws_transcription: dict = {"transcricaoAudio": {"speakers": []}}
    current_speaker = 1
    speaker_names = {1: "spk_0", 2: "spk_1"}

    for entry in aligned_words:
        words = re.split(r"(%\$%)", entry["word"])
        for word in words:
            if word == "%$%":
                current_speaker = 2 if current_speaker == 1 else 1
                continue
            if word.strip():
                aws_transcription["transcricaoAudio"]["speakers"].append({
                    "name": speaker_names[current_speaker],
                    "startTime": entry["start"],
                    "endTime": entry["end"],
                    "text": word.strip(),
                })

    return aws_transcription
