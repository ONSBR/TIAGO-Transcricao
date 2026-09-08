"""Regressão: generate_summary deve parsear JSON do LLM de forma robusta.

Em HOM, ~5% das respostas de summary vinham como cerca markdown vazia
(```python ou ```) e o parse falhava com Expecting value col1; o código
devolvia a string bruta em vez de reintentar ou erro claro.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.llm_service import (
    _extract_json_text,
    _parse_llm_structured,
    generate_summary,
)


@pytest.mark.unit
def test_extract_json_text_pure_json():
    assert _extract_json_text('{"resumo": "ok"}') == '{"resumo": "ok"}'


@pytest.mark.unit
def test_parse_llm_structured_python_dict_single_quotes():
    """Maverick/Llama costuma devolver dict Python em vez de JSON RFC."""
    raw = "{'resumo': 'A conversa trata da Usina de Campos Novos.'}"
    assert _parse_llm_structured(raw) == {
        "resumo": "A conversa trata da Usina de Campos Novos."
    }


@pytest.mark.unit
def test_parse_llm_structured_json_rfc():
    assert _parse_llm_structured('{"resumo": "ok"}') == {"resumo": "ok"}


@pytest.mark.unit
def test_parse_llm_structured_rejects_garbage():
    with pytest.raises(json.JSONDecodeError):
        _parse_llm_structured("```python")


@pytest.mark.unit
def test_extract_json_text_json_fence():
    raw = '```json\n{"resumo": "ok"}\n```'
    assert json.loads(_extract_json_text(raw)) == {"resumo": "ok"}


@pytest.mark.unit
def test_extract_json_text_python_fence_with_body():
    """Modelo às vezes rotula a cerca como python; o body ainda é JSON."""
    raw = '```python\n{"resumo": "ok"}\n```'
    assert json.loads(_extract_json_text(raw)) == {"resumo": "ok"}


@pytest.mark.unit
def test_extract_json_text_fence_only_is_empty_or_non_json():
    """Cerca sem corpo não vira JSON válido (retry cobre o caso no summary)."""
    for raw in ("```python", "```", "```json\n```"):
        text = _extract_json_text(raw)
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)


def _chat_response(content: str):
    return SimpleNamespace(message=SimpleNamespace(content=content))


def _mock_llm(side_effect=None, return_value=None):
    """BedrockConverse é Pydantic: mocka via _get_llm, não o singleton global."""
    mock = MagicMock()
    if side_effect is not None:
        mock.chat.side_effect = side_effect
    else:
        mock.chat.return_value = return_value
    return mock


@pytest.mark.unit
def test_generate_summary_retries_after_fence_only_then_returns_dict():
    """Primeira resposta só cerca vazia; segunda com JSON → dict (caminho de recuperação)."""
    calls = [
        _chat_response("```python"),
        _chat_response('{"resumo": "ligação operacional entre A e B"}'),
    ]
    mock_llm = _mock_llm(side_effect=calls)
    with (
        patch("app.services.llm_service._fetch_prompt", return_value="system"),
        patch("app.services.llm_service._get_llm", return_value=mock_llm),
        patch("app.services.llm_service.resolve_step_model", return_value="test-model"),
        patch("app.services.llm_service.resolve_max_tokens", return_value=4000),
    ):
        result = generate_summary("transcrição de teste")

    assert mock_llm.chat.call_count == 2
    assert isinstance(result, dict)
    assert result["resumo"].startswith("ligação")


@pytest.mark.unit
def test_generate_summary_clear_error_when_all_attempts_fail():
    """Duas cercas vazias → mensagem de erro legível, não a string da cerca."""
    mock_llm = _mock_llm(
        side_effect=[_chat_response("```python"), _chat_response("```")]
    )
    with (
        patch("app.services.llm_service._fetch_prompt", return_value="system"),
        patch("app.services.llm_service._get_llm", return_value=mock_llm),
        # fallback == summary → não entra no caminho de modelo robusto
        patch("app.services.llm_service.resolve_step_model", return_value="test-model"),
        patch("app.services.llm_service.resolve_max_tokens", return_value=4000),
    ):
        result = generate_summary("transcrição de teste")

    assert isinstance(result, str)
    assert "```" not in result
    assert "não é JSON válido" in result or "Não foi possível" in result


@pytest.mark.unit
def test_generate_summary_happy_path_single_call():
    mock_llm = _mock_llm(return_value=_chat_response('{"resumo": "ok"}'))
    with (
        patch("app.services.llm_service._fetch_prompt", return_value="system"),
        patch("app.services.llm_service._get_llm", return_value=mock_llm),
        patch("app.services.llm_service.resolve_step_model", return_value="test-model"),
        patch("app.services.llm_service.resolve_max_tokens", return_value=4000),
    ):
        result = generate_summary("x")

    assert mock_llm.chat.call_count == 1
    assert result == {"resumo": "ok"}


@pytest.mark.unit
def test_generate_summary_accepts_python_dict_without_fallback():
    """Regressão Maverick: aspas simples não devem forçar modelo fallback."""
    mock_llm = _mock_llm(
        return_value=_chat_response(
            "{'resumo': 'Ligação entre Andriel e Aroldo sobre o CAG.'}"
        )
    )
    with (
        patch("app.services.llm_service._fetch_prompt", return_value="system"),
        patch("app.services.llm_service._get_llm", return_value=mock_llm),
        patch(
            "app.services.llm_service.resolve_step_model",
            side_effect=lambda step: (
                "maverick" if step == "summary" else "sonnet-fallback"
            ),
        ),
        patch("app.services.llm_service.resolve_max_tokens", return_value=4000),
        patch("app.services.llm_service.log_llm_fallback") as log_fb,
    ):
        result = generate_summary("transcrição")

    assert mock_llm.chat.call_count == 1
    assert isinstance(result, dict)
    assert "Andriel" in result["resumo"]
    log_fb.assert_not_called()
