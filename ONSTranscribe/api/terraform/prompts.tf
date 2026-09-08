locals {
  prompt_dir = "${path.module}/prompts"

  # Usa o prompt real do ambiente (prompts/<nome>.txt, fora do controle de versao)
  # quando ele existir; caso contrario cai no exemplo versionado. Isso permite que o
  # repositorio seja validado e aplicado sem configuracao previa, e que cada ambiente
  # sobrescreva os prompts com o seu proprio conteudo de dominio.
  prompt = {
    for nome in [
      "run_beautify",
      "run_beautify_system",
      "summary",
      "key_entities",
      "technical_glossary",
      "get_audio_subject",
      "installation_disambiguation",
    ] : nome => fileexists("${local.prompt_dir}/${nome}.txt") ? file("${local.prompt_dir}/${nome}.txt") : file("${local.prompt_dir}/${nome}.example.txt")
  }
}

resource "aws_bedrockagent_prompt" "run_beautify_prompt" {
  name            = "transcribe_run_beautify_prompt_${var.environment}"
  description     = "Prompt used on run_beautify() function from ONSTranscribe."
  default_variant = "Variant1"

  variant {
    name     = "Variant1"
    model_id = var.transcribe_bedrock_model

    inference_configuration {
      text {
        temperature = 0.8
      }
    }

    template_type = "TEXT"
    template_configuration {
      text {
        text = local.prompt["run_beautify"]
      }
    }
  }
}


resource "aws_bedrockagent_prompt" "run_beautify_system_prompt" {
  name            = "transcribe_run_beautify_system_prompt_${var.environment}"
  description     = "System prompt used on run_beautify() function from ONSTranscribe."
  default_variant = "Variant1"

  variant {
    name     = "Variant1"
    model_id = var.transcribe_bedrock_model

    inference_configuration {
      text {
        temperature = 0.8
      }
    }

    template_type = "TEXT"
    template_configuration {
      text {
        text = local.prompt["run_beautify_system"]
      }
    }
  }
}

resource "aws_bedrockagent_prompt" "summary_prompt" {
  name            = "transcribe_summary_prompt_${var.environment}"
  description     = "System prompt used for generating transcription summarys on ONSTranscribe."
  default_variant = "Variant1"

  variant {
    name     = "Variant1"
    model_id = var.transcribe_bedrock_model

    inference_configuration {
      text {
        temperature = 0.8
      }
    }

    template_type = "TEXT"
    template_configuration {
      text {
        text = local.prompt["summary"]
      }
    }
  }
}

resource "aws_bedrockagent_prompt" "key_entities_prompt" {
  name            = "transcribe_get_key_entities_prompt_${var.environment}"
  description     = "Prompt para a captação de Entidades-chave das transcrições."
  default_variant = "Variant1"

  variant {
    name     = "Variant1"
    model_id = var.transcribe_bedrock_model

    inference_configuration {
      text {
        temperature = 0.8
      }
    }

    template_type = "TEXT"
    template_configuration {
      text {
        text = local.prompt["key_entities"]
      }
    }
  }
}

resource "aws_bedrockagent_prompt" "technical_glossary_prompt" {
  name            = "transcribe_technical_glossary_prompt_${var.environment}"
  description     = "Technical glossary used on run_beautify() function from ONSTranscribe."
  default_variant = "Variant1"

  variant {
    name     = "Variant1"
    model_id = var.transcribe_bedrock_model

    inference_configuration {
      text {
        temperature = 0.8
      }
    }

    template_type = "TEXT"
    template_configuration {
      text {
        text = local.prompt["technical_glossary"]
      }
    }
  }
}

resource "aws_bedrockagent_prompt" "get_audio_subject_prompt" {
  name            = "transcribe_get_audio_subject_prompt_${var.environment}"
  description     = "ONSTranscribe prompt for audio subject extraction"
  default_variant = "Variant1"

  variant {
    name     = "Variant1"
    model_id = var.transcribe_bedrock_model

    inference_configuration {
      text {
        temperature = 0.8
      }
    }

    template_type = "TEXT"
    template_configuration {
      text {
        text = local.prompt["get_audio_subject"]
      }
    }
  }
}

resource "aws_bedrockagent_prompt" "installation_disambiguation_prompt" {
  name            = "transcribe_installation_disambiguation_prompt_${var.environment}"
  description     = "Prompt para desambiguação de Instalação/Usina Envolvida contra a lista válida de instalações."
  default_variant = "Variant1"

  variant {
    name     = "Variant1"
    model_id = var.transcribe_bedrock_model

    inference_configuration {
      text {
        temperature = 0.2
      }
    }

    template_type = "TEXT"
    template_configuration {
      text {
        text = local.prompt["installation_disambiguation"]
      }
    }
  }
}
