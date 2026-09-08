"""Resolução de modelo por etapa: SSM > env > baseline."""
from unittest.mock import patch

import pytest

from app.services import llm_model_config as cfg


@pytest.fixture(autouse=True)
def _clear_cache():
    cfg.clear_ssm_cache()
    yield
    cfg.clear_ssm_cache()


@pytest.mark.unit
def test_baseline_when_no_env_no_ssm():
    env = {"TRANSCRIBE_BEDROCK_MODEL": "us.anthropic.claude-sonnet-4-6"}
    assert cfg.resolve_step_model("beautify", ssm_map={}, env=env) == "us.anthropic.claude-sonnet-4-6"
    assert cfg.resolve_step_model("entities", ssm_map={}, env=env) == "us.anthropic.claude-sonnet-4-6"
    assert cfg.resolve_step_model("fallback", ssm_map={}, env=env) == "us.anthropic.claude-sonnet-4-6"


@pytest.mark.unit
def test_env_overrides_baseline():
    env = {
        "TRANSCRIBE_BEDROCK_MODEL": "us.anthropic.claude-sonnet-4-6",
        "LLM_MODEL_BEAUTIFY": "amazon.nova-2-lite-v1:0",
        "LLM_MODEL_ENTITIES": "amazon.nova-2-lite-v1:0",
    }
    assert cfg.resolve_step_model("beautify", ssm_map={}, env=env) == "amazon.nova-2-lite-v1:0"
    assert cfg.resolve_step_model("summary", ssm_map={}, env=env) == "us.anthropic.claude-sonnet-4-6"


@pytest.mark.unit
def test_ssm_overrides_env():
    env = {
        "TRANSCRIBE_BEDROCK_MODEL": "us.anthropic.claude-sonnet-4-6",
        "LLM_MODEL_BEAUTIFY": "from-env",
    }
    ssm = {"beautify": "amazon.nova-2-lite-v1:0", "fallback": "us.anthropic.claude-sonnet-4-6"}
    assert cfg.resolve_step_model("beautify", ssm_map=ssm, env=env) == "amazon.nova-2-lite-v1:0"
    assert cfg.resolve_step_model("fallback", ssm_map=ssm, env=env) == "us.anthropic.claude-sonnet-4-6"


@pytest.mark.unit
def test_empty_ssm_key_falls_through():
    env = {"TRANSCRIBE_BEDROCK_MODEL": "baseline", "LLM_MODEL_SUBJECT": "from-env"}
    ssm = {"subject": ""}  # vazio não conta
    # resolve_step_model trata "" do map: mapping.get devolve "" → strip → falsy
    assert cfg.resolve_step_model("subject", ssm_map=ssm, env=env) == "from-env"


@pytest.mark.unit
def test_parse_ssm_json_filters_unknown_keys():
    raw = '{"beautify":"a","entities":"b","unknown":"x","summary":""}'
    parsed = cfg._parse_ssm_json(raw)
    assert parsed == {"beautify": "a", "entities": "b"}


@pytest.mark.unit
def test_get_ssm_step_models_fail_open():
    with (
        patch.dict("os.environ", {"LLM_STEP_MODELS_SSM_PARAMETER": "/ons-transcribe/dev/llm_step_models"}, clear=False),
        patch("app.services.llm_model_config._fetch_ssm_map", side_effect=RuntimeError("denied")),
    ):
        assert cfg.get_ssm_step_models(force_refresh=True) == {}


@pytest.mark.unit
def test_max_tokens_defaults_and_env():
    assert cfg.resolve_max_tokens("beautify", env={}) == 8000
    assert cfg.resolve_max_tokens("entities", env={"LLM_MAX_TOKENS_ENTITIES": "1234"}) == 1234


@pytest.mark.unit
def test_unknown_step_raises():
    with pytest.raises(ValueError):
        cfg.resolve_step_model("nope", ssm_map={}, env={})
