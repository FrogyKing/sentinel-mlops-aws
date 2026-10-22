resource "aws_cloudwatch_log_group" "jobs" {
  name              = "/aws/lambda/${local.name}-jobs"
  retention_in_days = 7
}
resource "aws_cloudwatch_metric_alarm" "job_errors" {
  alarm_name          = "${local.name}-job-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.jobs.function_name }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_iam_role" "jobs" {
  name               = "${local.name}-jobs"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "jobs" {
  role = aws_iam_role.jobs.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.jobs.arn}:*" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.artifacts.arn}/datasets/*"] },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.artifacts.arn}/jobs/*"] }
  ] })
}
resource "aws_lambda_function" "jobs" {
  function_name                  = "${local.name}-jobs"
  role                           = aws_iam_role.jobs.arn
  package_type                   = "Image"
  image_uri                      = var.image_uri
  timeout                        = 120
  memory_size                    = 2048
  reserved_concurrent_executions = 1
  image_config { command = ["sentinel.cloud_jobs.handler"] }
  environment { variables = { SENTINEL_ARTIFACT_DIR = "/tmp/sentinel" } }
  depends_on = [aws_iam_role_policy.jobs, aws_cloudwatch_log_group.jobs]
}
resource "aws_iam_role" "workflow" {
  name               = "${local.name}-workflow"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "workflow" {
  role   = aws_iam_role.workflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.jobs.arn }] })
}
resource "aws_sfn_state_machine" "investigation" {
  name     = "${local.name}-investigation"
  role_arn = aws_iam_role.workflow.arn
  definition = jsonencode({
    Comment = "Investigate drift; retrain labeled data; candidates always require separate promotion approval."
    StartAt = "Monitor", TimeoutSeconds = 300,
    States = {
      Monitor = {
        Type       = "Task", Resource = aws_lambda_function.jobs.arn,
        Parameters = { action = "monitor", "bucket.$" = "$.bucket", "reference_key.$" = "$.reference_key", "current_key.$" = "$.current_key", "labels_available.$" = "$.labels_available", "output_prefix.$" = "$.output_prefix" },
        ResultPath = "$.monitor", Next = "LabeledRetrainingRequired",
        Catch      = [{ ErrorEquals = ["States.ALL"], Next = "Failed", ResultPath = "$.error" }]
      },
      LabeledRetrainingRequired = {
        Type = "Choice", Choices = [{ And = [{ Variable = "$.monitor.retraining_required", BooleanEquals = true }, { Variable = "$.monitor.labels_available", BooleanEquals = true }], Next = "Retrain" }], Default = "InvestigationRecorded"
      },
      Retrain = {
        Type       = "Task", Resource = aws_lambda_function.jobs.arn,
        Parameters = { action = "retrain", "bucket.$" = "$.bucket", "reference_key.$" = "$.reference_key", "current_key.$" = "$.current_key", "labels_available.$" = "$.labels_available", "output_prefix.$" = "$.output_prefix" },
        ResultPath = "$.candidate", Next = "ApprovalRequired",
        Catch      = [{ ErrorEquals = ["States.ALL"], Next = "Failed", ResultPath = "$.error" }]
      },
      InvestigationRecorded = { Type = "Succeed" }, ApprovalRequired = { Type = "Succeed" }, Failed = { Type = "Fail", Cause = "Batch monitoring or training failed; inspect execution and CloudWatch logs." }
    }
  })
}
resource "aws_cloudwatch_event_rule" "monitor" {
  name                = "${local.name}-daily"
  description         = "Enable only after uploading valid reference/current labeled or unlabeled batches."
  schedule_expression = "rate(1 day)"
  state               = "DISABLED"
}
resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "scheduler" {
  role   = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.investigation.arn }] })
}
resource "aws_cloudwatch_event_target" "monitor" {
  rule     = aws_cloudwatch_event_rule.monitor.name
  arn      = aws_sfn_state_machine.investigation.arn
  role_arn = aws_iam_role.scheduler.arn
  input    = jsonencode({ bucket = aws_s3_bucket.artifacts.id, reference_key = "datasets/reference.csv", current_key = "datasets/current.csv", labels_available = false, output_prefix = "jobs/scheduled" })
}
