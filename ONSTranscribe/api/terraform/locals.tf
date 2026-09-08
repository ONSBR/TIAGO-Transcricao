locals {
  default_tags = {
    CreatedBy = "Terraform"
    Ambiente  = var.environment
    Solution  = "Transcribe"
  }
}
