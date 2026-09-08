variable "project_name" {
  description = "Prefixo usado em todos os recursos AWS criados por este Terraform (cluster ECS, ALB, SQS, IAM roles, etc.). Use apenas letras minúsculas, números e hífens."
  type        = string
  default     = "transcribe"

  validation {
    condition     = length(var.project_name) <= 20 && can(regex("^[a-z0-9-]+$", var.project_name))
    error_message = "project_name deve ter no máximo 20 caracteres e conter apenas letras minúsculas, números e hífens (limite imposto pelos nomes de ALB e Target Group da AWS)."
  }
}

variable "private_subnet_ids" {
  description = "Subnet ID"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "Subnet ID"
  type        = list(string)
}

variable "region" {
  description = "AWS Region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string

}

variable "raw_bucket_name" {
  description = "Raw S3 Bucket Name"
  type        = string
}

variable "bucket_name" {
  description = "S3 Bucket Name"
  type        = string
}

variable "transcribe_s3_model_path" {
  description = "Transcribe S3 Model Path"
  type        = string
}

variable "transcribe_s3_segmentation_model_path" {
  description = "Transcribe S3 Segmentation Model Path"
  type        = string
}

variable "ecr_repo_name" {
  description = "ECR Repository Name"
  type        = string
}

variable "environment" {
  description = "Environment"
  type        = string
  default     = "dev"
}

variable "audio_bucket_name" {
  description = "Audio Bucket Name"
  type        = string
}

variable "control_room_key_path" {
  description = "Control room key path"
  type        = string
}

variable "transcriptions_key_path" {
  description = "Transcriptions Key Path"
  type        = string
}

variable "vocabularies_key_path" {
  description = "Vocabularies Key Path"
  type        = string
}

variable "vocabulary_name" {
  description = "Vocabulary name from vocabularies key path"
  type        = string
}

variable "elastic_search_uri" {
  description = "URL from your Elastic Search instance"
  type        = string
}

variable "transcriptions_table_name" {
  description = "DynamoDB table name for Transcribe API"
  type        = string
  default     = "TranscriptionsTable"
}

variable "domain_name" {
  description = "Elastic Search domain name"
  type        = string
}

variable "index_name" {
  description = "Elastic Search Index name for transcriptions storaging."
  type        = string
}

variable "hf_token" {
  description = "HF token used to access huggingface models"
  type        = string
}

# Primeiro estagio da ingestao (ingestao_gateway.tf). Estavam comentadas enquanto a
# lambda de movimentacao era provisionada pelo Terraform da API .NET; agora que ela
# pertence a este modulo, voltaram a ser necessarias.
variable "gateway_bucket" {
  description = <<-EOT
    Bucket onde o audio chega antes de ser organizado. A lambda de movimentacao le daqui
    e escreve em audio_bucket_name, sob control_room_key_path.
  EOT
  type        = string
}

variable "gateway_bucket_control_room_key_path" {
  description = <<-EOT
    Prefixo, dentro do bucket gateway, que a notificacao do S3 observa para disparar a
    movimentacao. Somente objetos .wav sob este prefixo entram no pipeline.
  EOT
  type        = string
}

variable "transcribe_bedrock_model" {
  description = "Baseline Bedrock model_id (env TRANSCRIBE_BEDROCK_MODEL). Prefer inference profile (us.*)."
  type        = string
  # us. = inference profile (on-demand). Sem us. o Maverick falha no Converse.
  default = "us.meta.llama4-maverick-17b-instruct-v1:0"
}

variable "llm_max_tokens_beautify" {
  description = "maxTokens Bedrock na etapa beautify (env ECS)."
  type        = number
  default     = 8000
}

variable "llm_max_tokens_entities" {
  description = "maxTokens Bedrock na etapa entities (env ECS)."
  type        = number
  default     = 4000
}

variable "llm_max_tokens_subject" {
  description = "maxTokens Bedrock na etapa subject (env ECS)."
  type        = number
  default     = 2000
}

variable "llm_max_tokens_summary" {
  description = "maxTokens Bedrock na etapa summary (env ECS)."
  type        = number
  default     = 4000
}

variable "llm_step_models_json" {
  description = <<-EOT
    Valor *inicial* do SSM /<project_name>/<env>/llm_step_models (JSON string).
    Após o create, o value é ignore_changes — troca operacional via put-parameter.
    Chaves: beautify, entities, subject, summary, fallback.
    "{}" (default) = todas as etapas usam TRANSCRIBE_BEDROCK_MODEL / env LLM_MODEL_*.
  EOT
  type        = string
  default     = "{}"
}

variable "llm_model_beautify" {
  description = "Override de modelo para beautify (env ECS). Vazio = baseline."
  type        = string
  default     = ""
}

variable "llm_model_entities" {
  description = "Override de modelo para entities (env ECS). Vazio = baseline."
  type        = string
  default     = ""
}

variable "llm_model_subject" {
  description = "Override de modelo para subject (env ECS). Vazio = baseline."
  type        = string
  default     = ""
}

variable "llm_model_summary" {
  description = "Override de modelo para summary (env ECS). Vazio = baseline."
  type        = string
  default     = ""
}

variable "llm_fallback_model" {
  description = "Modelo robusto para fallback de JSON (env ECS). Vazio = baseline."
  type        = string
  default     = ""
}

variable "openai_api_key" {
  description = "OpenAI API Key used in subject identification pipeline"
  type        = string
}

variable "deploy_metricas_solution" {
  description = <<-EOT
    Liga/desliga o deploy da solução de testes de qualidade (módulo metricas_transcricao:
    Lambda calcular_metricas_transcricao, layer, IAM, Glue/Athena e Lake Formation).
    false (padrão): a stack principal sobe sem a solução de métricas.
    true: requer o layer ../lambdas/layers/metricas_layer.zip já gerado e o deploy_lf_principal
    informado; o primeiro apply das tabelas Glue/Lake Formation deve ser feito por um LF admin.
  EOT
  type        = bool
  default     = false
}

variable "metrics_bucket_name" {
  description = "Bucket S3 dedicado à solução de métricas (groundtruths, transcrições de teste e métricas). Distinto de bucket_name."
  type        = string
}

variable "metrics_groundtruth_key_path" {
  description = "Prefixo S3 dos arquivos groundtruth (.txt) de transcrição para cálculo de métricas"
  type        = string
}

variable "metrics_metadata_groundtruth_key_path" {
  description = "Prefixo S3 do xlsx de groundtruth de metadados dos áudios (ex: testes/base-verdade/metadados). Deixe vazio para desabilitar métricas de metadados."
  type        = string
}

variable "metrics_deploy_lf_principal" {
  description = <<-EOT
    ARN IAM estável do principal que executa o Terraform, usado para conceder
    permissões Lake Formation ALTER nas tabelas de métricas.

    NÃO use data.aws_caller_identity.current.arn: ele retorna o ARN da sessão
    STS assumida (arn:aws:sts::…:assumed-role/…), rejeitado pelo Lake Formation.
    Informe sempre o ARN IAM da role ou usuário subjacente:

      SSO / assumed-role:
        arn:aws:iam::ACCOUNT_ID:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_NOME_HASH
      IAM user direto:
        arn:aws:iam::ACCOUNT_ID:user/NOME_DO_USUARIO
  EOT
  type        = string
}

variable "metrics_audio_quality_key_paths" {
  description = "Prefixos S3 dos áudios de qualidade, separados por vírgula"
  type        = string
  default     = "testes/qualidade/audios/LOC1/,testes/qualidade/audios/LOC2/"
}

variable "metrics_s3_prefix" {
  description = "Prefixo S3 para JSONs completos de métricas versionados ({prefix}/{model_version}/{test_id}/{run_id}.json)"
  type        = string
}

variable "metrics_athena_s3_prefix" {
  description = "Prefixo S3 para dados particionados consumidos pelo Athena"
  type        = string
}

variable "metrics_athena_database_name" {
  description = "Nome do banco de dados Glue/Athena para métricas de transcrição"
  type        = string
  default     = "metricas_transcricao"
}

variable "metrics_model_version" {
  description = "Versão padrão do modelo no Lambda de métricas (pode ser sobrescrita no payload do evento)"
  type        = string
  default     = "whisper-v1"
}

variable "metrics_test_id" {
  description = "Identificador padrão do teste no Lambda de métricas (pode ser sobrescrito no payload do evento)"
  type        = string
  default     = "baseline"
}

variable "metrics_max_audio_count" {
  description = "Número máximo de áudios processados por execução do Lambda de métricas (amostragem estratificada por nomeag)"
  type        = number
  default     = 200
}

variable "processing_mode" {
  description = <<-EOT
    Modo de processamento do Lambda de métricas.
    integrated  : métricas de metadados limitadas ao escopo dos áudios com GT de transcrição.
    independent : Set A (todos os GTs de transcrição) e Set B (amostra estratificada de metadados) processados independentemente.
  EOT
  type        = string
  default     = "integrated"
  validation {
    condition     = contains(["integrated", "independent"], var.processing_mode)
    error_message = "processing_mode deve ser 'integrated' ou 'independent'."
  }
}

# ---- Localidades / gravadores (lambda de movimentação de .wav) ----
variable "recorders_list" {
  description = "Lista de gravadores/localidades no formato 'CODIGO|Nome Completo', separados por underscore. Usado pela Lambda para rotear áudios por localidade. Exemplo: 'ABC|Cidade A_XYZ|Cidade B'"
  type        = string
  default     = "LOC1|Localidade 1_LOC2|Localidade 2"
}

variable "location_codes" {
  description = "Códigos das localidades separados por vírgula. Usado pela Lambda de movimentação para filtrar áudios por origem. Deve conter os mesmos códigos de recorders_list. Exemplo: 'ABC,XYZ,DEF'"
  type        = string
  default     = "LOC1,LOC2,LOC3,LOC4"
}

variable "installations_list_s3_key" {
  description = "S3 key (path) para o JSON da lista de instalações válidas gerado pelo Lambda de sincronização da base de instalações"
  type        = string
  default     = "installations/installations.json"
}

variable "installation_match_threshold_high" {
  description = "Score mínimo (0-100) para aceitar automaticamente o match de instalação sem desambiguação Claude"
  type        = number
  default     = 90
}

variable "installation_match_threshold_low" {
  description = "Score mínimo (0-100) abaixo do qual o metadado de instalação é deixado em branco"
  type        = number
  default     = 60
}

variable "athena_database" {
  description = "Nome do banco de dados no AWS Glue/Athena contendo a tabela de instalações consultada pelo Athena (ex.: exemplo_base, com a tabela exemplo_base.instalacoes)"
  type        = string
  default     = "exemplo_base"
}

variable "athena_s3_output" {
  description = "S3 URI para output das queries Athena (ex: s3://meu-bucket/athena-results/)"
  type        = string
}

# ---- GPU (ECS Managed Instances capacity provider GPU-<project_name>-<env>) ----
variable "gpu_instance_type" {
  description = "Tipo EC2 permitido no capacity provider Managed Instances (ex: g4dn.xlarge)"
  type        = string
  default     = "g4dn.xlarge"
}

variable "gpu_root_volume_size_gb" {
  description = "Tamanho do volume de dados (GiB) das instâncias gerenciadas. 30 GiB estoura no deploy da imagem GPU; default 80."
  type        = number
  default     = 80
}

variable "gpu_scale_in_after_seconds" {
  description = "Segundos ociosos antes do Managed Instances otimizar (scale-in) a infraestrutura"
  type        = number
  default     = 120
}

variable "gpu_ecs_ami_id" {
  description = <<-EOT
    AMI ID da imagem ECS-optimized com suporte a GPU para a região utilizada.
    Obter via: aws ssm get-parameters --names /aws/service/ecs/optimized-ami/amazon-linux-2/gpu/recommended --query 'Parameters[0].Value'
    Não é usada pelo capacity provider ECS Managed Instances (a AMI é escolhida pela AWS);
    só informe se optar por um capacity provider baseado em ASG/launch template próprio.
  EOT
  type        = string
  default     = ""
}

# ---- Processamento de transcrição (pause de custo em ambiente sem carga) ----
variable "transcription_processing_enabled" {
  description = <<-EOT
    Dois efeitos no apply (não confundir com o flip runtime):
    1) Valor *inicial* do SSM /<project_name>/<env>/transcription_processing_enabled
       (lifecycle ignore_changes no value — depois do create, o ops liga/desliga o
       enqueue via put-parameter, sem apply).
    2) Autoscaling ECS: se false → min=max=0; se true → usa ecs_min/max_capacity.

    Runtime do enqueue (Lambda): lê só o SSM a cada invocação. SNS/.wav no S3
    permanecem. Requer alcance de SSM a partir da Lambda em VPC (NAT ou endpoint).
  EOT
  type        = bool
  default     = true
}

variable "ecs_min_capacity" {
  description = "Piso do Application Auto Scaling do service GPU (ignorado se transcription_processing_enabled=false no apply → 0)"
  type        = number
  default     = 1

  validation {
    condition     = var.ecs_min_capacity >= 0
    error_message = "ecs_min_capacity deve ser >= 0."
  }
}

variable "ecs_max_capacity" {
  description = "Teto do Application Auto Scaling do service GPU (ignorado se transcription_processing_enabled=false no apply → 0)"
  type        = number
  default     = 5

  validation {
    condition     = var.ecs_max_capacity >= 0 && var.ecs_max_capacity >= var.ecs_min_capacity
    error_message = "ecs_max_capacity deve ser >= ecs_min_capacity e >= 0."
  }
}

variable "ecs_infrastructure_role_name" {
  description = "IAM role de infraestrutura do ECS Managed Instances"
  type        = string
  default     = "ecsInfrastructureRoleForManagedInstances"
}

variable "ecs_instance_profile_name" {
  description = "Instance profile aplicado às instâncias gerenciadas do provider GPU"
  type        = string
  default     = "ecsInstanceRole"
}

# variable "api_workers_max_concurrency" {
#   description = "max number of workers for transcribing audios in the API concurrently (if 2, then there will be 2 workers max working concurrently in the API)"
#   type = string
#   default = "2"
# }
# variable "api_workers_sqs_max_messages" {
#   description = "max number of sqs messages sent to the api concurrently"
#   type = string
#   default = "2"
# }
