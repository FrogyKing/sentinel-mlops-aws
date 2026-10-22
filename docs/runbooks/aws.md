# Authorized AWS deployment and recovery

## Preconditions

This delivery intentionally excludes AWS deployment at the user's request. The following
is an operator guide for possible future deployment. No deployment success is claimed.
Docker daemon and AWS credentials were unavailable during initial implementation. The
infrastructure is testable locally with `terraform init -backend=false` and `terraform validate`.

1. Review costs, then obtain explicit authorization for the selected AWS account and region within the USD 20 planning ceiling.
2. Use Python 3.12, uv, Docker with Linux containers, AWS CLI and Terraform >=1.10 (S3 native lockfiles). Check account identity and region; never paste credentials into the repository.
3. Create or select a private, encrypted, versioned S3 state bucket with authorization, scoped access and no public access. State is not stored in Git. The deployment principal requires access only to `sentinel-mlops/terraform.tfstate` and its lock object in that bucket. This state bucket is deliberately outside application destroy.
4. Train, evaluate and explicitly approve a trusted model artifact. A pickle/joblib file is executable code; SHA256 proves integrity against a trusted digest, not safety of an untrusted producer.
5. Configure AWS Budgets permissions and a real alert email; confirm the SNS subscription. Some accounts cannot reserve Lambda concurrency 2 until regional concurrency quotas allow at least 100 unreserved executions. Request quota changes or adapt limits only after reviewing the exposure.

## Create

```
uv run python scripts/cloud.py deploy --approve-cost --approve-deployment --account ACCOUNT_ID --region us-east-1 --budget-email EMAIL --state-bucket APPROVED_STATE_BUCKET --model artifacts/demo/model.joblib
```

The script validates the approved bundle and builds the image before billable operations, verifies STS account, initializes encrypted remote state with S3 lockfiles, creates only bootstrap ECR/S3/budget resources, pushes a commit-tagged image, resolves its immutable digest, uploads the model under a checksum-addressed key and applies a saved Terraform plan. The HTTP API uses IAM authorization; callers require narrowly scoped `execute-api:Invoke` for this API. It performs signed `/readyz` and `/predict` smoke checks. Failed checks restore the previous live alias if one existed, or disable inference concurrency for an initial deployment; reconcile Terraform afterward. If any step fails, inspect state and logs before rerunning; partial resources can incur charges. ECR immutable tags mean reusing a published commit requires using its existing digest rather than pushing it again.

## OIDC and approvals

The manual GitHub workflow requires `authorize_spend=true`, an approved artifact S3 URI and independently reviewed SHA256. Set environment variables `AWS_ACCOUNT_ID`, `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`, `TF_STATE_BUCKET`, `BUDGET_EMAIL`. Restrict the existing IAM OIDC provider to issuer `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com` and subject `repo:FrogyKing/sentinel-mlops-aws:environment:production`. Configure a `production` environment with required reviewers where the GitHub plan supports private repository environment protection. If required reviewers are unavailable, restrict dispatch permissions and use the explicit human approval input; document that this is weaker than an enforced independent review.

Bootstrap the deploy role separately after reviewing a resource-scoped policy for project S3/ECR/Lambda/API Gateway/CloudWatch/Step Functions/EventBridge/SNS/Budgets/IAM resources. Restrict `iam:PassRole` to project role ARNs and services. This repository deliberately does not grant an unreviewed administrator policy or create an account-wide OIDC provider automatically. Deployment cannot run until this account-specific trust and policy are configured. AWS APIs with creation operations that lack resource-level support require tightly bounded condition/tag constraints and review.

## Real canary and rollback

Terraform exposes `stable_version`, `canary_version` and a maximum 10% `canary_weight`. AWS Lambda aliases perform actual probabilistic traffic splitting across published versions; the HTTP API integrates the alias. Keep `stable_version` pinned during a candidate apply, then set `canary_version` to the new published number. Both versions must use the same execution role and compatible runtime configuration. Set a 5% canary, generate bounded signed requests, examine `ExecutedVersion` Lambda metrics and error alarms, compare aggregate latency and prediction statistics, then explicitly approve promotion by updating `stable_version` and clearing `canary_version`. Small traffic volumes can yield imbalanced splits; synthetic local demo routing is not proof of AWS canary behavior.

On a canary alarm, immediately remove weights and restore the previous known-good version with `aws lambda update-alias --function-name sentinel-mlops --name live --function-version PREVIOUS --routing-config AdditionalVersionWeights={}`. Record operator, time, versions, metrics and reason, then reconcile Terraform variables/state. Alarm-driven rollback is an operator action; the alarm notifies and does not automatically change aliases. The deployment script separately restores the previous alias when its signed smoke checks fail. Local control tests do not establish deployed AWS rollback evidence. Before any subsequent canary deployment, pin the current stable version explicitly; unpinned Terraform uses the latest published version. The convenience deploy command suits initial deployment and explicitly approved direct updates; use reviewed Terraform plans and pinned variables for weighted canaries.

## Batch monitoring

Upload non-sensitive reference/current CSVs under `datasets/`. Start the `sentinel-mlops-investigation` Step Functions execution with `bucket`, `reference_key`, `current_key`, `labels_available`, and `output_prefix` under `jobs/`. Monitor runs first; clean drift plus complete labels permits candidate training, and all candidates still require separate human promotion. Failures enter a failed state and Lambda errors are visible in CloudWatch logs. The 120-second, 2 GB job budget is intended for bounded demo batches: full public-dataset runtime has not been measured on Lambda and may time out. Larger training must run on separately approved ephemeral compute. The EventBridge schedule is disabled until batches are configured; its default `labels_available=false` never triggers supervised training. Scheduled reports use a versioned S3 key; manual executions should use unique prefixes.

## Destroy

```
uv run python scripts/cloud.py destroy --approve-destroy --account ACCOUNT_ID --region us-east-1 --budget-email EMAIL --state-bucket APPROVED_STATE_BUCKET
```

The command verifies account, reviews planned resource ownership, and applies only the saved root-module destroy plan. Buckets and ECR deliberately refuse deletion while nonempty. Inspect project bucket tags and exact object/version IDs before removing versioned data, and inspect repository tags/images before explicitly deleting project images. Do not use broad account cleanup or recursive deletion by name prefix. Rerun destroy after approved project-only cleanup. Verify Resource Groups Tagging API, billing, S3/ECR, logs and workflow resources afterward. The externally managed state bucket and deploy role remain and require their own reviewed retention/removal decision.
