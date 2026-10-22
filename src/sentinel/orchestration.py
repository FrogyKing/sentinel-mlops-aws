"""Local investigation and labeled retraining; never auto-promote on drift."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from sentinel.data import demo_data
from sentinel.monitoring import monitor
from sentinel.registry import Registry, write_json
from sentinel.training import evaluate, train


def run_workflow(root: Path, current: pd.DataFrame, approved: bool = False) -> dict[str, Any]:
    reference = pd.read_csv(root / "reference.csv")
    report = monitor(reference, current)
    result: dict[str, Any] = {"monitoring": report, "retrained": False, "promoted": False}
    if report["quality_issues"]:
        result["status"] = "blocked_data_quality"
    elif not report["drift_detected"]:
        result["status"] = "observing"
    elif "target" not in current or current.target.isna().any():
        result["status"] = "awaiting_ground_truth"
    else:
        ordered = current.sort_values("time", kind="stable") if "time" in current else current
        boundary = int(len(ordered) * 0.8)
        fitting, holdout = ordered.iloc[:boundary], ordered.iloc[boundary:]
        if set(holdout.target.unique()) != {0, 1}:
            result["status"] = "insufficient_labeled_holdout"
            write_json(root / "workflow.json", result)
            return result
        summary = train(root / "candidate", fitting)
        cohort = hashlib.sha256(
            pd.util.hash_pandas_object(holdout, index=False).to_numpy().tobytes()
        ).hexdigest()
        registry = Registry(root)
        version = summary["version"]
        if version not in registry.state()["models"]:
            registry.register(version, summary["metrics"], summary["artifact_path"])
        for model_version in {version, registry.state()["champion"]} - {None}:
            record = registry.state()["models"][model_version]
            bundle = joblib.load(record["artifact_path"])
            probabilities = bundle["pipeline"].predict_proba(holdout[bundle["features"]])[:, 1]
            metrics = evaluate(holdout.target, probabilities, bundle["threshold"])
            key = f"{model_version}:{cohort}"
            if key not in registry.state().get("evaluations", {}):
                registry.record_evaluation(model_version, cohort, metrics)
        result.update(
            {
                "retrained": True,
                "candidate": summary,
                "status": "awaiting_approval",
                "comparison_cohort": cohort,
                "holdout_rows": len(holdout),
            }
        )
        if approved:
            registry = Registry(root)
            version = summary["version"]
            if version not in registry.state()["models"]:
                registry.register(version, summary["metrics"], summary["artifact_path"])
            try:
                registry.promote(version, approved=True, cohort=cohort)
                result.update({"promoted": True, "status": "promoted"})
            except ValueError as error:
                result.update({"status": "rejected_by_policy", "reason": str(error)})
    write_json(root / "workflow.json", result)
    return result


def demo(root: Path) -> dict[str, Any]:
    """Execute a synthetic local lifecycle; it is not an AWS deployment."""
    summary = train(root)
    registry = Registry(root)
    # Demo explicitly grants local approval, with policy still enforced.
    if registry.state()["champion"] is None:
        registry.promote(summary["version"], approved=True)
    normal = demo_data(seed=123)
    reference = pd.read_csv(root / "reference.csv")
    normal_report = monitor(reference, normal.drop(columns=["time", "target"]))
    shifted = demo_data(seed=123)
    shifted["v1"] += 2.5
    unlabeled = run_workflow(root, shifted.drop(columns=["target"]))
    retraining = run_workflow(root, shifted, approved=False)
    # Exercise promotion and rollback using genuinely trained candidate metrics.
    candidate = retraining.get("candidate")
    deployment: dict[str, Any] = {"status": "not_attempted"}
    if candidate:
        if candidate["version"] not in registry.state()["models"]:
            registry.register(
                candidate["version"], candidate["metrics"], candidate["artifact_path"]
            )
        try:
            registry.promote(
                candidate["version"], approved=True, cohort=retraining["comparison_cohort"]
            )
            registry.rollback(approved=True)
            deployment = {
                "status": "promoted_then_rolled_back",
                "verification_failure": "simulated local verification failure",
            }
        except ValueError as error:
            deployment = {"status": "rejected_by_policy", "reason": str(error)}
    result = {
        "evidence_type": "synthetic local execution",
        "training": summary,
        "normal": normal_report,
        "unlabeled_drift": unlabeled,
        "retraining": retraining,
        "deployment": deployment,
        "registry": registry.state(),
    }
    # Separate recovery fixture: keep the main policy rejection intact. This
    # explicitly looser policy allows actual trained models to exercise rollback.
    if candidate and deployment["status"] == "rejected_by_policy":
        recovery = Registry(
            root / "recovery-exercise", {"max_pr_auc_regression": 0.2, "max_recall_regression": 0.1}
        )
        current_version = summary["version"]
        for model_version in (current_version, candidate["version"]):
            record = registry.state()["models"][model_version]
            if model_version not in recovery.state()["models"]:
                recovery.register(model_version, record["metrics"], record["artifact_path"])
        if recovery.state()["champion"] is None:
            recovery.promote(current_version, approved=True)
        cohort = retraining["comparison_cohort"]
        for model_version in (current_version, candidate["version"]):
            key = f"{model_version}:{cohort}"
            if key not in recovery.state().get("evaluations", {}):
                recovery.record_evaluation(
                    model_version, cohort, registry.state()["evaluations"][key]
                )
        try:
            recovery.promote(candidate["version"], approved=True, cohort=cohort)
            recovery.rollback(approved=True)
            result["recovery_exercise"] = {
                "status": "actual_artifacts_promoted_and_rolled_back",
                "scope": "local synthetic recovery fixture; no traffic or AWS",
                "policy_override": recovery.policy,
                "failure_trigger": "simulated verification failure",
                "registry": recovery.state(),
            }
        except ValueError as error:
            result["recovery_exercise"] = {"status": "rejected_by_policy", "reason": str(error)}
    write_json(root / "demo.json", result)
    return result
