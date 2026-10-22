"""Reproducible chronological evaluation with validation-only selection."""

from __future__ import annotations

import hashlib
import importlib
import json
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from sentinel.data import demo_data, validate_data
from sentinel.registry import Registry, write_json


def evaluate(target: Any, probability: Any, threshold: float) -> dict[str, float]:
    prediction = np.asarray(probability) >= threshold
    tn, fp, fn, tp = confusion_matrix(target, prediction, labels=[0, 1]).ravel()
    return {
        "pr_auc": float(average_precision_score(target, probability)),
        "roc_auc": float(roc_auc_score(target, probability)),
        "precision": float(precision_score(target, prediction, zero_division=0)),
        "recall": float(recall_score(target, prediction, zero_division=0)),
        "f1": float(f1_score(target, prediction, zero_division=0)),
        "false_positive_rate": float(fp / max(1, tn + fp)),
        "business_cost": float((5 * fp + 100 * fn) / len(prediction)),
        "false_positives": float(fp),
        "false_negatives": float(fn),
        "true_positives": float(tp),
    }


def select_threshold(target: Any, probability: Any) -> float:
    """Minimize assumed 5-unit false-block / 100-unit missed-fraud cost."""
    thresholds = np.linspace(0.01, 0.99, 99)
    return float(
        min(thresholds, key=lambda t: evaluate(target, probability, float(t))["business_cost"])
    )


def train(output_dir: Path, frame: pd.DataFrame | None = None, seed: int = 42) -> dict[str, Any]:
    synthetic = frame is None or bool(frame.attrs.get("synthetic", False))
    data = demo_data(seed=seed) if frame is None else frame.copy()
    validate_data(data)
    if len(data) < 100:
        raise ValueError("Training requires at least 100 rows")
    # Exact duplicates cannot leak between partitions. No preprocessing sees future rows.
    data = (
        data.drop_duplicates().sort_values("time", kind="stable")
        if "time" in data
        else data.drop_duplicates()
    )
    features = [str(c) for c in data.columns if c not in {"target", "time"}]
    if not features:
        raise ValueError("No usable features")
    a, b = int(len(data) * 0.6), int(len(data) * 0.8)
    partitions = [data.iloc[:a], data.iloc[a:b], data.iloc[b:]]
    for partition in partitions:
        if set(partition.target.unique()) != {0, 1}:
            raise ValueError("Every chronological partition needs both classes")
    training, validation, test = partitions
    models = {
        "baseline": Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(class_weight="balanced", random_state=seed, max_iter=500),
                ),
            ]
        ),
        "challenger": Pipeline(
            [
                (
                    "model",
                    HistGradientBoostingClassifier(
                        class_weight="balanced",
                        max_iter=100,
                        max_leaf_nodes=15,
                        l2_regularization=1.0,
                        random_state=seed,
                    ),
                )
            ]
        ),
    }
    validation_results: dict[str, Any] = {}
    thresholds: dict[str, float] = {}
    for name, model in models.items():
        model.fit(training[features], training.target)
        probabilities = model.predict_proba(validation[features])[:, 1]
        thresholds[name] = select_threshold(validation.target, probabilities)
        validation_results[name] = evaluate(validation.target, probabilities, thresholds[name])
    selected = min(validation_results, key=lambda name: validation_results[name]["business_cost"])
    pipeline = models[selected]
    threshold = thresholds[selected]
    probabilities = pipeline.predict_proba(test[features])[:, 1]
    metrics: dict[str, Any] = evaluate(test.target, probabilities, threshold)
    started = time.perf_counter()
    for _ in range(30):
        pipeline.predict_proba(test[features].iloc[:1])
    metrics["latency_ms_per_row"] = (time.perf_counter() - started) * 1000 / 30
    test_results = {
        name: evaluate(test.target, model.predict_proba(test[features])[:, 1], thresholds[name])
        for name, model in models.items()
    }
    test_digest = hashlib.sha256(
        pd.util.hash_pandas_object(test, index=False).to_numpy().tobytes()
    ).hexdigest()
    metrics["evaluation_dataset_sha256"] = test_digest
    digest = hashlib.sha256(
        pd.util.hash_pandas_object(data, index=False).to_numpy().tobytes()
    ).hexdigest()
    configuration = json.dumps(pipeline.get_params(deep=True), sort_keys=True, default=repr)
    source_digest = hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest()
    version = hashlib.sha256(
        f"{digest}:{seed}:{selected}:{sklearn.__version__}:{configuration}:{source_digest}".encode()
    ).hexdigest()[:16]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "pipeline": pipeline,
        "features": features,
        "threshold": threshold,
        "version": version,
    }
    artifact_path = output_dir / "models" / f"{version}.joblib"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if not artifact_path.exists():
        joblib.dump(bundle, artifact_path)
    if not (output_dir / "model.joblib").exists():
        joblib.dump(bundle, output_dir / "model.joblib")
    training[features].to_csv(output_dir / "reference.csv", index=False)
    summary = {
        "version": version,
        "selected_model": selected,
        "synthetic": synthetic,
        "dataset_sha256": digest,
        "seed": seed,
        "features": features,
        "threshold": threshold,
        "split_rows": [len(p) for p in partitions],
        "selection": "Lowest validation business cost; test held out",
        "cost_assumptions": {"false_positive": 5, "false_negative": 100},
        "validation": validation_results,
        "test_models": test_results,
        "metrics": metrics,
        "artifact_path": str(artifact_path.resolve()),
        "sklearn_version": sklearn.__version__,
        "training_source_sha256": source_digest,
    }
    write_json(output_dir / "evaluation.json", summary)
    write_json(output_dir / "training.json", summary)
    registry = Registry(output_dir)
    if version not in registry.state()["models"]:
        registry.register(version, metrics, str(artifact_path))
    return summary


def log_mlflow(output_dir: Path, tracking_uri: str) -> str:
    """Explicit opt-in tracking; no tracking daemon is required for SQLite."""
    mlflow = importlib.import_module("mlflow")
    summary = json.loads((output_dir / "training.json").read_text())
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("sentinel")
    with mlflow.start_run() as run:
        mlflow.log_params(
            {
                "seed": summary["seed"],
                "model": summary["selected_model"],
                "synthetic": summary["synthetic"],
            }
        )
        mlflow.log_metrics(
            {k: v for k, v in summary["metrics"].items() if isinstance(v, (int, float))}
        )
        mlflow.log_artifact(str(output_dir / "evaluation.json"))
        mlflow.log_artifact(summary["artifact_path"])
        return str(run.info.run_id)
