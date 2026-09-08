"""Utilitários de pré-processamento e correção de texto para transcrições.

As correções de domínio (SUBSTITUTIONS e NUMBER_WORDS) são carregadas de
text_corrections.py, que é gitignored e específico de cada ambiente.

Para configurar: copie text_corrections_example.py para text_corrections.py
e personalize as entradas para o seu domínio e modelo ASR.
"""
import re

try:
    from app.utils.text_corrections import SUBSTITUTIONS, NUMBER_WORDS
except ImportError as exc:
    raise ImportError(
        "text_corrections.py não encontrado. "
        "Copie api/app/utils/text_corrections_example.py para "
        "api/app/utils/text_corrections.py e personalize."
    ) from exc

_NUMBER_WORDS_PATTERN = r"\b(?:" + "|".join(NUMBER_WORDS.keys()) + r")\b"


def preprocess_text(text: str) -> str:
    """Remove caracteres especiais, normaliza espaços e converte para minúsculas.

    Args:
        text: Texto bruto da transcrição, podendo conter marcadores %$%.

    Returns:
        Texto pré-processado em minúsculas.
    """
    text = text.replace("\n", " ")
    text = re.sub(r"[^%\$\w\s]", "", text)
    return text.lower()


def _replace_number_word(match: re.Match) -> str:
    word = match.group(0).lower()
    return NUMBER_WORDS.get(word, word)


def _add_space_between_number_and_unit(text: str) -> str:
    return re.sub(r"(\d)([a-zA-Z%°])", r"\1 \2", text)


def correct_common_names(text: str) -> str:
    """Aplica substituições de termos técnicos e converte números por extenso.

    Args:
        text: Texto pré-processado.

    Returns:
        Texto com termos corrigidos e números convertidos.
    """
    for wrong, correct in SUBSTITUTIONS.items():
        text = re.sub(r"\b" + re.escape(wrong) + r"\b", correct, text)

    text = re.sub(_NUMBER_WORDS_PATTERN, _replace_number_word, text, flags=re.IGNORECASE)
    return _add_space_between_number_and_unit(text)
