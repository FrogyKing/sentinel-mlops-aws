# Independent implementation review

This review examines behavior and failure paths rather than treating infrastructure
definitions as proof of deployment. It covers the integrated implementation;
final check results belong in the execution evidence record.

## Areas examined

- FastAPI readiness, exact feature schema, finite-value validation, direct estimator
  equivalence, deterministic predictions, request identity and feature-log privacy.
- Chronological fitting, validation-only model/threshold selection, untouched test
  evaluation, artifact identity, promotion approval and known-good registry recovery.
- Distribution monitoring versus quality failures and delayed-label gates.
- Lambda batch handlers, Step Functions choice paths, private artifact permissions,
  IAM-authorized inference, Terraform state, ownership-safe teardown and CI gates.
- Dataset provenance, upstream licensing and synthetic/public evidence labeling.

## Findings communicated during implementation

1. Registry versions initially referenced an overwritten serving artifact. Requested
   immutable per-version model paths so historical state can load its original model.
2. Initial promotion policy was hardcoded. Requested configurable quality thresholds,
   finite/range checks and explicit same-cohort evaluation before comparison.
3. Candidate metrics could be logged with the previous serving artifact in MLflow.
   Requested tracking the candidate's immutable artifact path.
4. Monitoring initially evaluated at 0.5 regardless of the deployed threshold. Requested
   an explicit threshold and validation of labeled monitoring inputs.
5. Passing generated records explicitly into training could label them as public data.
   Requested provenance propagation for synthetic retraining evidence.
6. Initial Terraform state definition lacked the S3 backend used by the deploy helper.
   Requested a real S3 backend and a Terraform floor compatible with lockfiles.
7. Destroy validation initially accepted insufficient ownership evidence. Requested
   explicit project resource addresses and fail-closed handling of untagged children.
8. Deployment initially ended after apply without signed inference verification or
   automatic recovery. Requested a smoke-check and rollback path; actual cloud
   execution still requires spending authorization and available credentials.
9. Dashboard monitoring fallback initially referenced a nonexistent demo report key,
   and prediction trends depended on a file that the demo did not populate. Requested
   connection to executed reports and real prediction metadata.
10. A raw JSON `NaN` caused a validation error whose default serialization raised a
    server exception. Added a regression test and requested sanitized error responses
    that omit raw submitted feature values.

## Verification boundary

The initial four API integration tests passed. Strengthening the schema test with raw
`NaN` exposed finding 10. After the sanitized error-handler fix, all four API tests
passed again (34.04 seconds on the review machine under concurrent training load).
The prediction test compares the HTTP result with the actual trained pipeline, and
the log privacy test enables the prediction logger and confirms an event was captured.
Ruff and mypy pass for the API test module. Framework deprecation warnings from the
HTTP test adapter and event-loop initialization remain visible in the test output.

Cloud credentials and deployment authorization are absent during this review. No AWS
resources, canary traffic behavior, alert delivery or real operational rollback are
verified by this document. Synthetic registry recovery and policy rejection are
different evidence from a deployed AWS alias rollback.

Remaining production extensions include transactional multi-writer registry state,
authenticated approver identity, delayed-label source integration, representative load
and cold-start tests, confidence intervals for promotion and longer-term dataset
coverage. These are documented limitations, not placeholder claims of implementation.
