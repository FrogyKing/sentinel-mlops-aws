# Cost review and authorization

No AWS resources were deployed during repository implementation. AWS credentials and a selected region were absent. The region below is a proposed deployment choice, not an observed account setting.

Planning region: `us-east-1`. Target below USD 15; hard planning ceiling USD 20. These are spending goals, not enforceable account caps. Recheck current rates, taxes, existing account usage and free-tier eligibility before authorizing.

Illustrative monthly demonstration: 10,000 requests, 1 GB Lambda, 500 ms mean execution (5,000 GB-seconds), 1 GB S3 artifacts, 1 GB ECR images, 100 MB logs, two standard CloudWatch alarms or fewer, 30 short workflow executions. Estimate USD 2–5 without relying on a free tier; retain USD 10 contingency for builds, cold starts, retries, network egress and rate uncertainty. Stop and re-estimate if measured usage differs. At 30 seconds instead of 0.5 seconds, Lambda compute alone is about USD 5 for 10,000 requests.

Reference rates used for planning: Lambda requests USD 0.20/million; x86 duration about USD 0.0000166667/GB-second; HTTP API approximately USD 1/million; S3 Standard first-tier storage approximately USD 0.023/GB-month; ECR private storage approximately USD 0.10/GB-month. Alarm, log, S3 request and Step Functions transition charges also apply. These inputs are approximations, not a live AWS quote.

Primary pricing sources reviewed 2026-10-22:

- [Lambda pricing](https://aws.amazon.com/lambda/pricing/)
- [API Gateway pricing](https://aws.amazon.com/api-gateway/pricing/)
- [S3 pricing](https://aws.amazon.com/s3/pricing/)
- [ECR pricing](https://aws.amazon.com/ecr/pricing/)
- [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)
- [Step Functions pricing](https://aws.amazon.com/step-functions/pricing/)
- [AWS Budgets pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/)

Recurring resources: S3 including retained noncurrent versions; ECR including tagged historical releases; CloudWatch logs (7-day retention), metrics and alarms; state bucket. Requests and workflow transitions are usage billed. No NAT, VPC endpoint, EC2, SageMaker endpoint, ALB, database, GPU or Kubernetes is provisioned.

The Terraform budget alerts at 50% actual / 100% forecast of USD 15 apply account-wide so untagged charges are visible. Alerts are delayed and do not stop spending. Confirm email/SNS subscriptions. API IAM authentication, 1 request/sec gateway throttle, burst 2, Lambda concurrency 2, job concurrency 1 and bounded timeouts reduce exposure; they do not impose a dollar ceiling. Disable EventBridge, set Lambda concurrency to zero and destroy project resources when the demo ends.

Run `make cost-check` offline. Before deployment, inspect `aws sts get-caller-identity`, `aws configure list`, current billing and account quotas. Supply an explicit account, budget email and separately approved encrypted state bucket. Obtain spending authorization before any bootstrap resource is created.
