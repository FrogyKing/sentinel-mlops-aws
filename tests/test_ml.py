from pathlib import Path

import joblib
import pandas as pd
import pytest

from sentinel.data import demo_data, validate_data
from sentinel.monitoring import join_outcomes, monitor
from sentinel.orchestration import run_workflow
from sentinel.registry import Registry
from sentinel.training import train


def test_validation_rejects_invalid_data() -> None:
    frame = demo_data(200)
    frame.loc[0, "v1"] = float("inf")
    with pytest.raises(ValueError, match="nonfinite"):
        validate_data(frame)


def test_training_predictions_and_reproducibility(tmp_path: Path) -> None:
    first = train(tmp_path / "a")
    second = train(tmp_path / "b")
    assert first["version"] == second["version"]
    assert first["metrics"]["pr_auc"] == second["metrics"]["pr_auc"]
    assert first["metrics"]["pr_auc"] > 0.2
    bundle = joblib.load(tmp_path / "a" / "model.joblib")
    sample = demo_data(100)[bundle["features"]]
    probabilities = bundle["pipeline"].predict_proba(sample)[:, 1]
    assert ((probabilities >= 0) & (probabilities <= 1)).all()
    assert sum(first["split_rows"]) == 2500


def test_drift_quality_and_label_gate(tmp_path: Path) -> None:
    reference = demo_data(500).drop(columns=["target", "time"])
    assert not monitor(reference, reference)["drift_detected"]
    shifted = reference.copy()
    shifted["v1"] += 5
    assert monitor(reference, shifted)["drift_detected"]
    assert monitor(reference, shifted.drop(columns=["v1"]))["quality_issues"]
    reference.to_csv(tmp_path / "reference.csv", index=False)
    result = run_workflow(tmp_path, shifted)
    assert result["status"] == "awaiting_ground_truth"
    assert not result["retrained"]


def test_registry_approval_regression_and_rollback(tmp_path: Path) -> None:
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"test registry artifact")
    registry = Registry(tmp_path)
    metrics = {
        "pr_auc": 0.8,
        "recall": 0.8,
        "false_positive_rate": 0.01,
        "business_cost": 5.0,
        "evaluation_dataset_sha256": "test-cohort",
    }
    registry.register("a", metrics, str(artifact))
    with pytest.raises(PermissionError):
        registry.promote("a")
    registry.promote("a", approved=True)
    registry.register("bad", {**metrics, "recall": 0.5, "business_cost": 3}, str(artifact))
    with pytest.raises(ValueError, match="regresses"):
        registry.promote("bad", approved=True)
    registry.register("b", {**metrics, "business_cost": 4}, str(artifact))
    registry.promote("b", approved=True)
    assert Registry(tmp_path).state()["champion"] == "b"
    registry.rollback(approved=True)
    assert Registry(tmp_path).state()["champion"] == "a"
    assert registry.state()["history"][-1]["action"] == "rolled_back"


def test_training_rejects_single_class_partition(tmp_path: Path) -> None:
    frame = demo_data(500)
    frame.loc[400:, "target"] = 0
    with pytest.raises(ValueError, match="partition"):
        train(tmp_path, frame)


def test_monitoring_delayed_labels() -> None:
    frame = demo_data(200)
    frame["probability"] = frame.target * 0.8 + 0.1
    report = monitor(frame.drop(columns=["probability"]), frame)
    assert report["performance"]["pr_auc"] == pytest.approx(1.0)
    assert isinstance(pd.DataFrame(report["features"]).shape, tuple)


def test_registry_rejects_incomparable_cohorts_and_custom_policy(tmp_path: Path) -> None:
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"test")
    registry = Registry(tmp_path, {"min_recall": 0.9})
    metrics = {
        "pr_auc": 0.8,
        "recall": 0.95,
        "false_positive_rate": 0.01,
        "business_cost": 5.0,
        "evaluation_dataset_sha256": "cohort-a",
    }
    registry.register("a", metrics, str(artifact))
    registry.promote("a", approved=True)
    registry.register(
        "b", {**metrics, "business_cost": 4, "evaluation_dataset_sha256": "cohort-b"}, str(artifact)
    )
    with pytest.raises(ValueError, match="same labeled cohort"):
        registry.promote("b", approved=True)
    registry.register("c", {**metrics, "business_cost": 4, "recall": 0.85}, str(artifact))
    with pytest.raises(ValueError, match="minimum quality"):
        registry.promote("c", approved=True)


def test_model_artifacts_are_immutable_across_training_runs(tmp_path: Path) -> None:
    first = train(tmp_path, demo_data(seed=10))
    artifact = Path(first["artifact_path"])
    original_bytes = artifact.read_bytes()
    train(tmp_path, demo_data(seed=11))
    assert artifact.read_bytes() == original_bytes
    assert Registry(tmp_path).state()["models"][first["version"]]["artifact_path"] == str(artifact)


def test_shared_cohort_evidence_is_immutable_and_drives_policy(tmp_path: Path) -> None:
    artifact = tmp_path / "model.joblib"
    artifact.write_bytes(b"registry test")
    registry = Registry(tmp_path)
    original = {
        "pr_auc": 0.7,
        "recall": 0.8,
        "false_positive_rate": 0.01,
        "business_cost": 5.0,
        "evaluation_dataset_sha256": "old",
    }
    registry.register("a", original, str(artifact))
    registry.promote("a", approved=True)
    registry.register("b", {**original, "evaluation_dataset_sha256": "other"}, str(artifact))
    registry.record_evaluation("a", "shared", original)
    registry.record_evaluation("b", "shared", {**original, "business_cost": 4.0})
    with pytest.raises(ValueError, match="immutable"):
        registry.record_evaluation("a", "shared", original)
    registry.promote("b", approved=True, cohort="shared")
    assert registry.state()["champion"] == "b"
    assert registry.state()["models"]["b"]["metrics"]["evaluation_dataset_sha256"] == "other"


def test_labeled_workflow_trains_but_requires_approval(tmp_path: Path) -> None:
    summary = train(tmp_path)
    registry = Registry(tmp_path)
    registry.promote(summary["version"], approved=True)
    shifted = demo_data(seed=123)
    shifted["v1"] += 3
    result = run_workflow(tmp_path, shifted)
    assert result["retrained"]
    assert not result["promoted"]
    assert result["status"] == "awaiting_approval"
    assert result["holdout_rows"] == 500
    assert result["candidate"]["synthetic"] is True
    assert registry.state()["champion"] == summary["version"]
    assert len(registry.state()["evaluations"]) == 2


def test_delayed_outcome_join_uses_actual_threshold_and_coverage() -> None:
    predictions = pd.DataFrame(
        {
            "request_id": ["a", "b", "c"],
            "model_version": ["v1", "v1", "v1"],
            "probability": [0.3, 0.1, 0.9],
            "threshold": [0.2, 0.2, 0.2],
        }
    )
    outcomes = pd.DataFrame({"request_id": ["a", "b"], "target": [1, 0]})
    report = join_outcomes(predictions, outcomes)
    assert report["label_coverage"] == pytest.approx(2 / 3)
    assert report["models"]["v1"]["metrics"]["recall"] == 1
    with pytest.raises(ValueError, match="unique"):
        join_outcomes(predictions, pd.concat([outcomes, outcomes]))
    with pytest.raises(ValueError, match="missing from prediction"):
        join_outcomes(predictions, pd.DataFrame({"request_id": ["absent"], "target": [1]}))
    with pytest.raises(ValueError, match="binary"):
        join_outcomes(predictions, pd.DataFrame({"request_id": ["a"], "target": [2]}))
