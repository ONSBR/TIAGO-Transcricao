# Toggle operacional de enqueue (Lambda → SQS).
# Valor inicial vem de var.transcription_processing_enabled; depois ignore_changes
# permite ligar/desligar via CLI/console sem apply:
#   aws ssm put-parameter --name <name> --value false --overwrite --type String
# ECS min/max continua só em var.ecs_* / var.transcription_processing_enabled no AS.

resource "aws_ssm_parameter" "transcription_processing_enabled" {
  name        = "/${var.project_name}/${var.environment}/transcription_processing_enabled"
  description = "Se false, Lambda só registra PAUSED no OpenSearch (sem enqueue SQS). Não controla ECS."
  type        = "String"
  value       = var.transcription_processing_enabled ? "true" : "false"
  tier        = "Standard"
  tags        = local.default_tags

  lifecycle {
    ignore_changes = [value]
  }
}

# Modelos Bedrock por etapa LLM (beautify/entities/subject/summary/fallback).
# Valor inicial = var.llm_step_models_json (default "{}"); depois ignore_changes
# permite troca on-the-fly sem apply:
#   aws ssm put-parameter --name <name> --value '{"beautify":"amazon.nova-2-lite-v1:0",...}' --overwrite --type String
# Resolução na API: SSM (TTL) > env LLM_MODEL_* > TRANSCRIBE_BEDROCK_MODEL.

resource "aws_ssm_parameter" "llm_step_models" {
  name        = "/${var.project_name}/${var.environment}/llm_step_models"
  description = "JSON de model_id Bedrock por etapa LLM (beautify, entities, subject, summary, fallback). {} = usar env/default."
  type        = "String"
  value       = var.llm_step_models_json
  tier        = "Standard"
  tags        = local.default_tags

  lifecycle {
    ignore_changes = [value]
  }
}
