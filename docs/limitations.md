# Evidence and limitations

The project distinguishes working local functionality, infrastructure definitions and
verified cloud operations. A Terraform file is not evidence that its resources exist.
A synthetic traffic sequence is not evidence of live customer transactions.

## Dataset realism and licensing

The [ULB/Worldline dataset](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud)
contains 284,807 transactions over two days in September 2013, including 492 frauds
(about 0.172%). Its 28 principal-component features hide the original business
variables; `Time` and `Amount` are retained separately. The
[OpenML 42175 metadata](https://www.openml.org/api/v1/json/data/42175)
preserves this description and identifies the original research sources.

The upstream Kaggle listing describes the license as “Database: Open Database,
Contents: Database Contents.” Review the applicable
[Open Database License](https://opendatacommons.org/licenses/odbl/1-0/) and
[Database Contents License](https://opendatacommons.org/licenses/dbcl/1-0/) before
redistribution or commercial reuse. A mirror's generic `Public` label does not replace
upstream terms. Raw records and model binaries are not committed in this repository.

Anonymization limits feature interpretation, merchant/account grouping, realistic drift
simulation and explanations of individual decisions. Two days of historical transactions
cannot establish robustness to seasonal effects, adversarial adaptation or a modern
payment distribution. The original feature transformation is unavailable; its fitting
history cannot be independently audited for leakage. Chronological splitting prevents
new leakage in this pipeline but cannot repair upstream transformations. Public benchmark
evaluation must not be presented as operational fraud-detection performance.

The deterministic demo is generated data with an intentionally simplified fraud
relationship. Its class balance differs from the public benchmark. Its measured metrics
verify that software executes and predictions are real model outputs; they do not
estimate effectiveness on bank transactions. Shifted demo features illustrate a
monitoring test, not a real-world concept-drift diagnosis.

## Operational scope

- Local registry operations use a single-writer file. Multiple writers require a
  conditional-write transactional state store and authenticated approval records.
- Dataset hashes, local DVC metadata and immutable model files implement version
  identity. S3 DVC remote synchronization has not been executed; a central multi-user
  MLflow service is not integrated.
- Histogram gradient boosting supplies the advanced tree model; LightGBM/XGBoost
  are not dependencies. Business cost assumptions are illustrative use-case defaults.
- The strict delayed-label join is implemented with coverage reporting and per-version
  thresholds. Source ingestion and an identity/retention policy must be integrated with
  the real transaction source before performance monitoring is operational.
- Local demonstration approval is explicit program input, not separation of duties.
  GitHub production environment reviewers require repository-side configuration.
- Serialized models must be trusted and version-compatible. SHA-256 verifies integrity
  relative to a trusted pin; it is not a publisher signature.
- Local performance and inference latency are measured on the development machine.
  Load tests, AWS cold starts and weighted traffic behavior need cloud verification.
- AWS infrastructure, budget alerts, SNS delivery, OIDC federation and rollback cannot
  be called verified until deployment and smoke tests succeed in an authorized account.
- A budget alert is not an enforceable spending limit. Delete project-owned resources
  after the demonstration and inspect billing for retained storage or log charges.

Execution evidence belongs in the final verification record and must identify the
dataset mode, commands, observed results and failures. Do not substitute example
numbers for actual results.
