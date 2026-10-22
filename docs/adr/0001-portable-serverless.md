# ADR 0001: Portable ML with a serverless AWS edge

Status: accepted for demonstration; cloud execution unverified.

The portable sklearn pipeline is trained and serialized once, including preprocessing and feature order. FastAPI serves the same bundle locally and through Mangum in a Lambda container, preventing separate training/serving transformations. S3 persists immutable model bytes; the deployment pins their independent SHA256. Terraform state stores cloud version/alias configuration; the portable registry records candidate approval history. These are separate systems: local promotion never silently changes cloud traffic.

Lambda plus IAM-authenticated API Gateway avoids always-on servers and load balancers. Cold starts, large dependency images, artifact caching and concurrency limits require real AWS measurements before production claims. Dependencies should be split into an inference-only image when build measurements justify it. The dashboard remains a local operator tool; public Streamlit hosting is outside this budget design.

MLflow is optional local/ephemeral tracking with durable SQLite metadata and exported artifacts; no continuously running server is required. SQLite has single-writer and sharing limitations; moving to a managed tracking backend requires an explicit cost and concurrency decision. DVC can point to the same artifact bucket, but S3 content-addressed model storage and dataset fingerprints already provide a minimal portable lineage baseline.

Batch monitoring is cheaper than a dedicated stream processor and respects delayed labels. Statistical feature drift triggers investigation, not proof of concept drift or automatic promotion. Supervised retraining requires complete labels, validation quality gates and independent holdout evaluation. Step Functions records monitor/retrain transitions; EventBridge remains disabled until real batch inputs exist. No production transaction contents are logged.

Chronological partitioning precedes preprocessing; thresholds use validation data and tests remain held out. Champion–challenger rules prevent quality regression and require approval. Lambda aliases support real weighted routing at no load balancer cost; the operator retains a known-good published version and explicitly removes weights on rollback. CloudWatch error alarms notify operators; automated rollback is intentionally not claimed.

This design does not use Kubernetes, NAT gateways, managed databases or always-on MLflow. It trades high-throughput training, multiwriter registry transactions, low cold-start latency and automatic cloud promotion for low demonstration cost, portability and auditable manual control.
