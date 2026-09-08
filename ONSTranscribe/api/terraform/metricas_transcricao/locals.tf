data "aws_caller_identity" "current" {}

locals {
  default_tags = {
    CreatedBy = "Terraform"
    Ambiente  = var.environment
    Solution  = "Transcricao"
  }
}
