variable "region" {
  description = "AWS Region"
  type        = string
}

variable "environment" {
  description = "Environment (dev | dsv | hmg | prd)"
  type        = string
}

variable "audio_bucket_name" {
  description = "Bucket S3 de origem dos áudios de qualidade (onde audio_quality_key_paths são resolvidos). Distinto de metrics_bucket_name."
  type        = string
}

variable "private_subnet_ids" {
  description = "IDs das subnets privadas para o Lambda"
  type        = list(string)
}

variable "security_group_id" {
  description = "ID do security group do ALB, reutilizado pelo Lambda para acesso à API"
  type        = string
}

variable "alb_dns_name" {
  description = "DNS name do ALB da API ONSTranscribe (sem protocolo)"
  type        = string
}

variable "transcribe_layer_arn" {
  description = "ARN do Lambda Layer transcribe_layer (compartilhado com os demais Lambdas do projeto)"
  type        = string
}

variable "transcriptions_key_path" {
  description = "Prefixo S3 das transcrições geradas pela API"
  type        = string
}

variable "metrics_bucket_name" {
  description = "Bucket S3 dedicado à solução de métricas (groundtruths, transcrições de teste e métricas)"
  type        = string
}

variable "metrics_groundtruth_key_path" {
  description = "Prefixo S3 dos .txt de groundtruth de transcrição"
  type        = string
}

variable "metrics_metadata_groundtruth_key_path" {
  description = "Prefixo S3 do xlsx de groundtruth de metadados. Vazio = métricas de metadados desabilitadas."
  type        = string
}

variable "metrics_audio_quality_key_paths" {
  description = "Prefixos S3 dos áudios de qualidade, separados por vírgula (um prefixo por localidade, ex.: LOC1, LOC2)"
  type        = string
}

variable "metrics_s3_prefix" {
  description = "Prefixo S3 para JSONs completos de métricas versionados ({prefix}/{model_version}/{test_id}/{run_id}.json)"
  type        = string
}

variable "metrics_athena_s3_prefix" {
  description = "Prefixo S3 para dados NDJSON particionados consumidos pelo Athena"
  type        = string
}

variable "metrics_athena_database_name" {
  description = "Nome do banco Glue/Athena para métricas de transcrição"
  type        = string
  default     = "metricas_transcricao"
}

variable "metrics_model_version" {
  description = "Versão padrão do modelo (pode ser sobrescrita no payload do evento)"
  type        = string
}

variable "metrics_test_id" {
  description = "Identificador padrão do teste (pode ser sobrescrito no payload do evento)"
  type        = string
}

variable "metrics_max_audio_count" {
  description = "Número máximo de áudios no Set B por execução (amostragem estratificada por nomeag)"
  type        = number
  default     = 200
}

variable "processing_mode" {
  description = <<-EOT
    Modo de processamento do Lambda de métricas.
    integrated  : métricas de metadados calculadas para os áudios na interseção de GT transcrição ∩ GT metadados.
    independent : Set A (transcrição) e Set B (amostra estratificada de metadados) processados de forma independente.
  EOT
  type        = string
  default     = "integrated"
  validation {
    condition     = contains(["integrated", "independent"], var.processing_mode)
    error_message = "processing_mode deve ser 'integrated' ou 'independent'."
  }
}

variable "metrics_deploy_lf_principal" {
  description = <<-EOT
    ARN IAM estável do principal que executa o Terraform.
    NÃO usar o ARN de sessão STS (assumed-role/.../SESSION) — Lake Formation rejeita.
    Ex. SSO: arn:aws:iam::ACCOUNT:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_NOME_HASH
    Ex. IAM: arn:aws:iam::ACCOUNT:user/usuario-deploy
  EOT
  type        = string
}

variable "metrics_reader_lf_principals" {
  description = <<-EOT
    ARNs IAM estáveis dos principais que devem ter acesso de leitura (SELECT/DESCRIBE)
    às tabelas de métricas via Lake Formation.

    Use isto quando um principal enxerga apenas as colunas de partição (ex.: só duas
    colunas no Athena): assim que o Lake Formation passa a ter grants explícitos para
    um principal, ele deixa de herdar o IAM_ALLOWED_PRINCIPALS e precisa de um grant
    nominal de SELECT/DESCRIBE para ver todas as colunas.

    NÃO usar o ARN de sessão STS (assumed-role/.../SESSION) — Lake Formation rejeita.
    Ex. SSO: arn:aws:iam::ACCOUNT:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_NOME_HASH
  EOT
  type        = list(string)
  default     = []
}
