variable "athena_table" {
  description = "Nome da tabela de instalações, dentro de athena_database, consultada pela Lambda de sincronização"
  type        = string
  default     = "instalacoes"
}

locals {
  athena_output_path   = trimsuffix(replace(var.athena_s3_output, "s3://", ""), "/") # bucket-s3/path
  athena_output_bucket = split("/", local.athena_output_path)[0]                     # bucket-s3
}

data "archive_file" "sync_installations_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas/sync_installations"
  output_path = "${path.module}/lambdas/sync_installations.zip"
}

resource "aws_lambda_function" "sync_installations" {
  function_name    = "sync_installations"
  role             = aws_iam_role.sync_installations_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.9"
  filename         = "${path.module}/lambdas/sync_installations.zip"
  source_code_hash = data.archive_file.sync_installations_lambda.output_base64sha256
  timeout          = 300

  tags = local.default_tags

  environment {
    variables = {
      ATHENA_DATABASE      = var.athena_database
      ATHENA_TABLE         = var.athena_table
      ATHENA_S3_OUTPUT     = var.athena_s3_output
      INSTALLATIONS_BUCKET = var.bucket_name
      INSTALLATIONS_S3_KEY = var.installations_list_s3_key
    }
  }
}

resource "aws_iam_role" "sync_installations_execution_role" {
  name = "sync-installations-execution-role"

  tags = local.default_tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "sync_installations_policy" {
  name = "sync-installations-policy"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution"
        ]
        Resource = "arn:aws:athena:${var.region}:${data.aws_caller_identity.current.account_id}:workgroup/*"
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartitions"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "arn:aws:s3:::${var.bucket_name}/${var.installations_list_s3_key}"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket"]
        Resource = "arn:aws:s3:::${local.athena_output_bucket}"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "arn:aws:s3:::${local.athena_output_path}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/sync_installations:*"
      },
      {
        Effect   = "Allow"
        Action   = ["lakeformation:GetDataAccess"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "sync_installations_policy_attach" {
  role       = aws_iam_role.sync_installations_execution_role.name
  policy_arn = aws_iam_policy.sync_installations_policy.arn
}

resource "aws_cloudwatch_event_rule" "sync_installations_weekly" {
  name                = "sync-installations-weekly"
  description         = "Sincroniza semanalmente a lista de instalações válidas de ${var.athena_database}.${var.athena_table}"
  schedule_expression = "cron(0 3 ? * MON *)"

  tags = local.default_tags
}

resource "aws_cloudwatch_event_target" "sync_installations_target" {
  rule      = aws_cloudwatch_event_rule.sync_installations_weekly.name
  target_id = "sync_installations"
  arn       = aws_lambda_function.sync_installations.arn
}

resource "aws_lambda_permission" "allow_eventbridge_sync_installations" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sync_installations.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sync_installations_weekly.arn
}

resource "aws_lakeformation_permissions" "sync_installations_db_describe" {
  principal   = aws_iam_role.sync_installations_execution_role.arn
  permissions = ["DESCRIBE"]

  database {
    name = var.athena_database
  }
}

# O grant SELECT/DESCRIBE na tabela de instalações (var.athena_database /
# var.athena_table) é feito pelo admin da conta produtora diretamente ao role
# sync-installations-execution-role (cross-account), por isso não é gerenciado
# aqui. A lambda acessa os dados via esse grant + a permissão
# lakeformation:GetDataAccess na policy do role.
