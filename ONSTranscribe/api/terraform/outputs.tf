output "ecs_cluster_id" {
  description = "ECS Cluster ID"
  value       = aws_ecs_cluster.fastapi_cluster.id
}

output "ecs_service_id" {
  description = "ECS Service ID"
  value       = aws_ecs_service.fastapi_service.id
}

output "repository_url" {
  value = data.aws_ecr_repository.fastapi_repo.repository_url
}

output "account_id" {
  description = "AWS account ID"
  value       = data.aws_caller_identity.current.account_id
  sensitive   = true
}

output "llm_step_models_ssm_parameter" {
  description = "Nome do parâmetro SSM com JSON de modelos por etapa LLM"
  value       = aws_ssm_parameter.llm_step_models.name
}

output "transcription_processing_enabled_ssm_parameter" {
  description = "SSM path do toggle de enqueue (put-parameter --overwrite para ligar/desligar sem apply)"
  value       = aws_ssm_parameter.transcription_processing_enabled.name
}