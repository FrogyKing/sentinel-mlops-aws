output "api_url" { value = aws_apigatewayv2_api.inference.api_endpoint }
output "artifact_bucket" { value = aws_s3_bucket.artifacts.id }
output "repository_url" { value = aws_ecr_repository.inference.repository_url }
output "function_version" { value = aws_lambda_function.inference.version }
output "alias_arn" { value = aws_lambda_alias.live.arn }
