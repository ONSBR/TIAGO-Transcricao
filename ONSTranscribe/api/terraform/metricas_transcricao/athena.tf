resource "aws_glue_catalog_database" "metricas_transcricao" {
  name = var.metrics_athena_database_name
}

resource "aws_glue_catalog_table" "metricas_por_audio" {
  name          = "metricas_por_audio"
  database_name = aws_glue_catalog_database.metricas_transcricao.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"       = "TRUE"
    "classification" = "json"
  }

  partition_keys {
    name = "model_version"
    type = "string"
  }

  partition_keys {
    name = "test_id"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${var.metrics_bucket_name}/${var.metrics_athena_s3_prefix}/metricas_por_audio/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters            = { "serialization.format" = "1" }
    }

    # --- Identificação ---
    columns {
      name = "run_id"
      type = "string"
    }
    columns {
      name = "execucao"
      type = "string"
    }
    columns {
      name = "arquivo"
      type = "string"
    }
    columns {
      name = "audio_key"
      type = "string"
    }

    # --- Métricas de transcrição ---
    columns {
      name = "wer"
      type = "double"
    }
    columns {
      name = "mer"
      type = "double"
    }
    columns {
      name = "wil"
      type = "double"
    }
    columns {
      name = "wip"
      type = "double"
    }
    columns {
      name = "cer"
      type = "double"
    }

    # --- Assuntos (retrocompatível) ---
    columns {
      name = "assuntos_predicao"
      type = "array<string>"
    }
    columns {
      name = "assuntos_referencia"
      type = "array<string>"
    }

    # --- Indicador de groundtruth de metadados ---
    columns {
      name = "tem_gt_metadados"
      type = "boolean"
    }

    # --- Métricas de metadados: assunto ---
    columns {
      name = "assunto_precision"
      type = "double"
    }
    columns {
      name = "assunto_recall"
      type = "double"
    }
    columns {
      name = "assunto_f1"
      type = "double"
    }

    # --- Métricas de metadados: nome_do_operador_ONS ---
    columns {
      name = "nome_oper_ons_pred"
      type = "string"
    }
    columns {
      name = "nome_oper_ons_ref"
      type = "string"
    }
    columns {
      name = "nome_oper_ons_precision"
      type = "double"
    }
    columns {
      name = "nome_oper_ons_recall"
      type = "double"
    }
    columns {
      name = "nome_oper_ons_f1"
      type = "double"
    }

    # --- Métricas de metadados: nome_do_operador_do_agente ---
    columns {
      name = "nome_oper_agente_pred"
      type = "string"
    }
    columns {
      name = "nome_oper_agente_ref"
      type = "string"
    }
    columns {
      name = "nome_oper_agente_precision"
      type = "double"
    }
    columns {
      name = "nome_oper_agente_recall"
      type = "double"
    }
    columns {
      name = "nome_oper_agente_f1"
      type = "double"
    }

    # --- Métricas de metadados: nome_do_agente ---
    columns {
      name = "nome_agente_pred"
      type = "string"
    }
    columns {
      name = "nome_agente_ref"
      type = "string"
    }
    columns {
      name = "nome_agente_precision"
      type = "double"
    }
    columns {
      name = "nome_agente_recall"
      type = "double"
    }
    columns {
      name = "nome_agente_f1"
      type = "double"
    }

    # --- Métricas de metadados: instalacao_ou_usina_envolvida ---
    columns {
      name = "instalacao_pred"
      type = "string"
    }
    columns {
      name = "instalacao_ref"
      type = "string"
    }
    columns {
      name = "instalacao_precision"
      type = "double"
    }
    columns {
      name = "instalacao_recall"
      type = "double"
    }
    columns {
      name = "instalacao_f1"
      type = "double"
    }

    # --- Métricas de metadados: equipamento_envolvido ---
    columns {
      name = "equipamento_pred"
      type = "string"
    }
    columns {
      name = "equipamento_ref"
      type = "string"
    }
    columns {
      name = "equipamento_precision"
      type = "double"
    }
    columns {
      name = "equipamento_recall"
      type = "double"
    }
    columns {
      name = "equipamento_f1"
      type = "double"
    }

    # --- Métricas de metadados: numero_do_SGI ---
    columns {
      name = "nsgi_pred"
      type = "string"
    }
    columns {
      name = "nsgi_ref"
      type = "string"
    }
    columns {
      name = "nsgi_precision"
      type = "double"
    }
    columns {
      name = "nsgi_recall"
      type = "double"
    }
    columns {
      name = "nsgi_f1"
      type = "double"
    }

    # --- Macro-média de todos os campos de metadados ---
    columns {
      name = "precision_metadados"
      type = "double"
    }
    columns {
      name = "recall_metadados"
      type = "double"
    }
    columns {
      name = "f1_metadados"
      type = "double"
    }

    # --- Uso/custo LLM estimado (catálogo llm_pricing.py na Lambda) ---
    columns {
      name = "llm_total_input_tokens"
      type = "int"
    }
    columns {
      name = "llm_total_output_tokens"
      type = "int"
    }
    columns {
      name = "llm_estimated_cost_usd"
      type = "double"
    }
    columns {
      name = "llm_pricing_incomplete"
      type = "boolean"
    }
    columns {
      name = "llm_fallback_used"
      type = "boolean"
    }
  }
}

resource "aws_glue_catalog_table" "metricas_agregadas" {
  name          = "metricas_agregadas"
  database_name = aws_glue_catalog_database.metricas_transcricao.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"       = "TRUE"
    "classification" = "json"
  }

  partition_keys {
    name = "model_version"
    type = "string"
  }

  partition_keys {
    name = "test_id"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${var.metrics_bucket_name}/${var.metrics_athena_s3_prefix}/metricas_agregadas/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters            = { "serialization.format" = "1" }
    }

    # --- Identificação ---
    columns {
      name = "run_id"
      type = "string"
    }
    columns {
      name = "execucao"
      type = "string"
    }
    columns {
      name = "total_arquivos_processados"
      type = "int"
    }
    columns {
      name = "total_erros"
      type = "int"
    }
    columns {
      name = "total_com_gt_metadados"
      type = "int"
    }

    # --- Médias de transcrição ---
    columns {
      name = "wer_media"
      type = "double"
    }
    columns {
      name = "mer_media"
      type = "double"
    }
    columns {
      name = "wil_media"
      type = "double"
    }
    columns {
      name = "wip_media"
      type = "double"
    }
    columns {
      name = "cer_media"
      type = "double"
    }

    # --- Médias de metadados: assunto ---
    columns {
      name = "assunto_precision_media"
      type = "double"
    }
    columns {
      name = "assunto_recall_media"
      type = "double"
    }
    columns {
      name = "assunto_f1_media"
      type = "double"
    }

    # --- Médias de metadados: nome_do_operador_ONS ---
    columns {
      name = "nome_oper_ons_precision_media"
      type = "double"
    }
    columns {
      name = "nome_oper_ons_recall_media"
      type = "double"
    }
    columns {
      name = "nome_oper_ons_f1_media"
      type = "double"
    }

    # --- Médias de metadados: nome_do_operador_do_agente ---
    columns {
      name = "nome_oper_agente_precision_media"
      type = "double"
    }
    columns {
      name = "nome_oper_agente_recall_media"
      type = "double"
    }
    columns {
      name = "nome_oper_agente_f1_media"
      type = "double"
    }

    # --- Médias de metadados: nome_do_agente ---
    columns {
      name = "nome_agente_precision_media"
      type = "double"
    }
    columns {
      name = "nome_agente_recall_media"
      type = "double"
    }
    columns {
      name = "nome_agente_f1_media"
      type = "double"
    }

    # --- Médias de metadados: instalacao_ou_usina_envolvida ---
    columns {
      name = "instalacao_precision_media"
      type = "double"
    }
    columns {
      name = "instalacao_recall_media"
      type = "double"
    }
    columns {
      name = "instalacao_f1_media"
      type = "double"
    }

    # --- Médias de metadados: equipamento_envolvido ---
    columns {
      name = "equipamento_precision_media"
      type = "double"
    }
    columns {
      name = "equipamento_recall_media"
      type = "double"
    }
    columns {
      name = "equipamento_f1_media"
      type = "double"
    }

    # --- Médias de metadados: numero_do_SGI ---
    columns {
      name = "nsgi_precision_media"
      type = "double"
    }
    columns {
      name = "nsgi_recall_media"
      type = "double"
    }
    columns {
      name = "nsgi_f1_media"
      type = "double"
    }

    # --- Macro-médias de todos os campos de metadados ---
    columns {
      name = "precision_metadados_media"
      type = "double"
    }
    columns {
      name = "recall_metadados_media"
      type = "double"
    }
    columns {
      name = "f1_metadados_media"
      type = "double"
    }

    # --- Custo LLM estimado no run (catálogo llm_pricing.py) ---
    columns {
      name = "total_com_llm_usage"
      type = "int"
    }
    columns {
      name = "llm_estimated_cost_usd_total"
      type = "double"
    }
    columns {
      name = "llm_estimated_cost_usd_media"
      type = "double"
    }
    columns {
      name = "llm_total_input_tokens_media"
      type = "double"
    }
    columns {
      name = "llm_total_output_tokens_media"
      type = "double"
    }
  }
}

resource "aws_athena_workgroup" "metricas_transcricao" {
  name        = "metricas-transcricao"
  description = "Workgroup para consultas de métricas de qualidade de transcrição"

  configuration {
    result_configuration {
      output_location = "s3://${var.metrics_bucket_name}/${var.metrics_athena_s3_prefix}/query-results/"
    }
  }

  tags = local.default_tags
}

# ---------------------------------------------------------------------------
# Lake Formation: permissões do principal de deploy
#
# Necessário para que o Terraform possa criar/alterar/remover as tabelas Glue.
# O primeiro apply destes recursos deve ser executado por um Lake Formation
# Data Lake Admin (ex.: root ou admin da conta AWS).
#
# NÃO usar data.aws_caller_identity.current.arn — retorna o ARN de sessão STS,
# que é rejeitado pelo Lake Formation. Informar o ARN IAM estável via deploy_lf_principal.
# ---------------------------------------------------------------------------

resource "aws_lakeformation_permissions" "deploy_lf_table_por_audio" {
  principal                     = var.metrics_deploy_lf_principal
  permissions                   = ["SELECT", "ALTER", "DESCRIBE", "DROP"]
  permissions_with_grant_option = ["SELECT", "ALTER", "DESCRIBE", "DROP"]

  depends_on = [aws_glue_catalog_table.metricas_por_audio]

  table {
    database_name = var.metrics_athena_database_name
    name          = aws_glue_catalog_table.metricas_por_audio.name
  }
}

resource "aws_lakeformation_permissions" "deploy_lf_table_agregadas" {
  principal                     = var.metrics_deploy_lf_principal
  permissions                   = ["SELECT", "ALTER", "DESCRIBE", "DROP"]
  permissions_with_grant_option = ["SELECT", "ALTER", "DESCRIBE", "DROP"]

  depends_on = [aws_glue_catalog_table.metricas_agregadas]

  table {
    database_name = var.metrics_athena_database_name
    name          = aws_glue_catalog_table.metricas_agregadas.name
  }
}

# ---------------------------------------------------------------------------
# Lake Formation: permissões do Lambda de métricas para escrita de partições
# ---------------------------------------------------------------------------

resource "aws_lakeformation_permissions" "calcular_metricas_lf_table_por_audio" {
  principal   = aws_iam_role.calcular_metricas_transcricao_lambda_execution_role.arn
  permissions = ["ALTER", "DESCRIBE"]

  table {
    database_name = var.metrics_athena_database_name
    name          = aws_glue_catalog_table.metricas_por_audio.name
  }
}

resource "aws_lakeformation_permissions" "calcular_metricas_lf_table_agregadas" {
  principal   = aws_iam_role.calcular_metricas_transcricao_lambda_execution_role.arn
  permissions = ["ALTER", "DESCRIBE"]

  table {
    database_name = var.metrics_athena_database_name
    name          = aws_glue_catalog_table.metricas_agregadas.name
  }
}

# ---------------------------------------------------------------------------
# Lake Formation: acesso de leitura para qualquer principal IAM autorizado
#
# Necessário após o Lake Formation assumir o controle das tabelas Glue.
# Sem estes grants, consultas Athena retornam COLUMN_NOT_FOUND.
# ---------------------------------------------------------------------------

resource "aws_lakeformation_permissions" "iam_allowed_database" {
  principal   = "IAM_ALLOWED_PRINCIPALS"
  permissions = ["DESCRIBE"]
  database {
    name = aws_glue_catalog_database.metricas_transcricao.name
  }
}

resource "aws_lakeformation_permissions" "iam_allowed_por_audio" {
  principal   = "IAM_ALLOWED_PRINCIPALS"
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.metricas_transcricao.name
    name          = aws_glue_catalog_table.metricas_por_audio.name
  }
}

resource "aws_lakeformation_permissions" "iam_allowed_agregadas" {
  principal   = "IAM_ALLOWED_PRINCIPALS"
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.metricas_transcricao.name
    name          = aws_glue_catalog_table.metricas_agregadas.name
  }
}

# ---------------------------------------------------------------------------
# Lake Formation: grants nominais de leitura para principais específicos
#
# Necessário quando um principal enxerga apenas as colunas de partição
# no Athena.
# ---------------------------------------------------------------------------

resource "aws_lakeformation_permissions" "reader_lf_por_audio" {
  for_each = toset(var.metrics_reader_lf_principals)

  principal   = each.value
  permissions = ["SELECT", "DESCRIBE"]

  depends_on = [aws_glue_catalog_table.metricas_por_audio]

  table {
    database_name = aws_glue_catalog_database.metricas_transcricao.name
    name          = aws_glue_catalog_table.metricas_por_audio.name
  }
}

resource "aws_lakeformation_permissions" "reader_lf_agregadas" {
  for_each = toset(var.metrics_reader_lf_principals)

  principal   = each.value
  permissions = ["SELECT", "DESCRIBE"]

  depends_on = [aws_glue_catalog_table.metricas_agregadas]

  table {
    database_name = aws_glue_catalog_database.metricas_transcricao.name
    name          = aws_glue_catalog_table.metricas_agregadas.name
  }
}
