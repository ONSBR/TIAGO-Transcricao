
data "aws_ecr_repository" "fastapi_repo" {
  name = var.ecr_repo_name
}

data "aws_ecr_image" "fastapi_image" {
  repository_name = data.aws_ecr_repository.fastapi_repo.name
  image_tag       = "latest"
}
