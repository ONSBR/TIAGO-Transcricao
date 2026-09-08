# =============================================================================
# Módulo de Testes de Qualidade — Lambda calcular_metricas_transcricao
#
# Todos os recursos exclusivos desta solução (Lambda, IAM, Glue, Athena,
# Lake Formation) estão segregados em ./metricas_transcricao/.
#
# Para aplicar somente este módulo:
#   terraform apply -target=module.metricas_transcricao --auto-approve
#
# NOTA DE MIGRAÇÃO: se estes recursos já existem no state com caminhos antigos
# (ex.: aws_lambda_function.calcular_metricas_transcricao), é necessário
# movê-los para os novos endereços antes do apply:
#   terraform state mv aws_lambda_function.calcular_metricas_transcricao \
#     'module.metricas_transcricao[0].aws_lambda_function.calcular_metricas_transcricao'
# =============================================================================

module "metricas_transcricao" {
  source = "./metricas_transcricao"

  # Liga/desliga toda a solução de testes de qualidade (Lambda, IAM, Glue/Athena, Lake Formation).
  count = var.deploy_metricas_solution ? 1 : 0

  # Infraestrutura compartilhada (outputs do root)
  region                  = var.region
  environment             = var.environment
  audio_bucket_name       = var.bucket_name
  private_subnet_ids      = var.private_subnet_ids
  security_group_id       = aws_security_group.alb_sg.id
  alb_dns_name            = aws_lb.fastapi_alb.dns_name
  transcribe_layer_arn    = aws_lambda_layer_version.transcribe_layer.arn
  transcriptions_key_path = var.transcriptions_key_path

  # Paths de groundtruth e qualidade
  metrics_bucket_name                   = var.metrics_bucket_name
  metrics_groundtruth_key_path          = var.metrics_groundtruth_key_path
  metrics_metadata_groundtruth_key_path = var.metrics_metadata_groundtruth_key_path
  metrics_audio_quality_key_paths       = var.metrics_audio_quality_key_paths
  metrics_s3_prefix                     = var.metrics_s3_prefix

  # Athena
  metrics_athena_s3_prefix     = var.metrics_athena_s3_prefix
  metrics_athena_database_name = var.metrics_athena_database_name

  # Parâmetros de execução (sobrescritíveis no payload do evento)
  metrics_model_version   = var.metrics_model_version
  metrics_test_id         = var.metrics_test_id
  metrics_max_audio_count = var.metrics_max_audio_count
  processing_mode         = var.processing_mode

  # Lake Formation — ARN IAM estável do principal de deploy
  metrics_deploy_lf_principal = var.metrics_deploy_lf_principal
}
