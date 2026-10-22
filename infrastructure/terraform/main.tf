locals {
  name   = "sentinel-mlops"
  bucket = "sentinel-mlops-${var.account_id}-${var.region}"
}
resource "aws_s3_bucket" "artifacts" {
  bucket        = local.bucket
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "retain-audit-with-bounded-version-history"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_ecr_repository" "inference" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_ecr_lifecycle_policy" "inference" {
  repository = aws_ecr_repository.inference.name
  policy     = jsonencode({ rules = [{ rulePriority = 1, description = "Expire untagged builds", selection = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 7 }, action = { type = "expire" } }] })
}
resource "aws_cloudwatch_log_group" "inference" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}
resource "aws_iam_role" "inference" {
  name               = "${local.name}-inference"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "inference" {
  role = aws_iam_role.inference.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.inference.arn}:*" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.artifacts.arn}/models/*" }
  ] })
}
resource "aws_lambda_function" "inference" {
  function_name                  = local.name
  role                           = aws_iam_role.inference.arn
  package_type                   = "Image"
  image_uri                      = var.image_uri
  publish                        = true
  timeout                        = 30
  memory_size                    = 1024
  reserved_concurrent_executions = 2
  architectures                  = ["x86_64"]
  environment {
    variables = {
      SENTINEL_ARTIFACT_BUCKET = aws_s3_bucket.artifacts.id
      SENTINEL_MODEL_KEY       = var.model_key
      SENTINEL_MODEL_SHA256    = var.model_sha256
      SENTINEL_ARTIFACT_DIR    = "/tmp/sentinel"
    }
  }
  depends_on = [aws_cloudwatch_log_group.inference, aws_iam_role_policy.inference]
}
resource "aws_lambda_alias" "live" {
  name             = "live"
  function_name    = aws_lambda_function.inference.function_name
  function_version = coalesce(var.stable_version, aws_lambda_function.inference.version)
  dynamic "routing_config" {
    for_each = var.canary_version == null ? [] : [var.canary_version]
    content { additional_version_weights = { (routing_config.value) = var.canary_weight } }
  }
}
resource "aws_apigatewayv2_api" "inference" {
  name          = local.name
  protocol_type = "HTTP"
}
resource "aws_apigatewayv2_integration" "inference" {
  api_id                 = aws_apigatewayv2_api.inference.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_alias.live.invoke_arn
  payload_format_version = "2.0"
}
resource "aws_apigatewayv2_route" "inference" {
  api_id             = aws_apigatewayv2_api.inference.id
  route_key          = "$default"
  target             = "integrations/${aws_apigatewayv2_integration.inference.id}"
  authorization_type = "AWS_IAM"
}
resource "aws_apigatewayv2_stage" "inference" {
  api_id      = aws_apigatewayv2_api.inference.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 2
    throttling_rate_limit  = 1
  }
}
resource "aws_lambda_permission" "gateway" {
  statement_id  = "AllowProjectGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.inference.function_name
  qualifier     = aws_lambda_alias.live.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.inference.execution_arn}/*/*"
}
resource "aws_sns_topic" "alerts" { name = "${local.name}-alerts" }
resource "aws_sns_topic_subscription" "alerts" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.budget_email
}
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${local.name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.inference.function_name }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_budgets_budget" "demo" {
  name         = local.name
  budget_type  = "COST"
  limit_amount = "15"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
