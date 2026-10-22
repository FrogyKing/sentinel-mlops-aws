"""Batch drift and quality checks; drift alone is not performance degradation."""

from __future__ import annotations

import importlib
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from sentinel.training import evaluate


def monitor(
    reference: pd.DataFrame, current: pd.DataFrame, threshold: float = 0.5
) -> dict[str, Any]:
    if not 0 <= threshold <= 1:
        raise ValueError("Invalid classification threshold")
    quality: list[str] = []
    features = [str(c) for c in reference.columns if c not in {"target", "time", "probability"}]
    missing = sorted(set(features) - set(current.columns))
    if missing:
        quality.append(f"Missing features: {missing}")
    if reference.empty or current.empty:
        quality.append("Empty monitoring window")
    details: dict[str, Any] = {}
    for feature in features:
        if feature not in current:
            continue
        ref = pd.to_numeric(reference[feature], errors="coerce").to_numpy(dtype=float)
        cur = pd.to_numeric(current[feature], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(cur).all() or not np.isfinite(ref).all():
            quality.append(f"Nonfinite or nonnumeric values: {feature}")
        ref, cur = ref[np.isfinite(ref)], cur[np.isfinite(cur)]
        if min(len(ref), len(cur)) < 30:
            quality.append(f"Insufficient observations: {feature}")
            continue
        statistic, pvalue = ks_2samp(ref, cur)
        # Bonferroni correct multiple comparisons and require a practical effect size.
        details[feature] = {
            "ks_statistic": float(statistic),
            "p_value": float(pvalue),
            "drifted": bool(pvalue < 0.01 / max(1, len(features)) and statistic > 0.1),
        }
    result: dict[str, Any] = {
        "drift_detected": any(v["drifted"] for v in details.values()),
        "quality_issues": quality,
        "features": details,
        "action": "investigate"
        if quality or any(v["drifted"] for v in details.values())
        else "observe",
        "interpretation": (
            "Distribution shift does not establish concept drift or performance degradation."
        ),
    }
    if {"target", "probability"}.issubset(current.columns):
        labeled = current.dropna(subset=["target", "probability"])
        if (
            not set(labeled.target.unique()).issubset({0, 1})
            or not labeled.probability.between(0, 1).all()
        ):
            quality.append("Invalid observed labels or probabilities")
        elif set(labeled.target.unique()) == {0, 1}:
            result["performance"] = evaluate(labeled.target, labeled.probability, threshold)
    if quality:
        result["action"] = "investigate"
    return result


def evidently_report(reference: pd.DataFrame, current: pd.DataFrame) -> Any:
    """Optional Evidently report using the installed v0.7+ public API."""
    evidently = importlib.import_module("evidently")
    presets = importlib.import_module("evidently.presets")
    report = evidently.Report(metrics=[presets.DataDriftPreset()])
    return report.run(reference_data=reference, current_data=current)


def join_outcomes(predictions: pd.DataFrame, outcomes: pd.DataFrame) -> dict[str, Any]:
    """Join delayed labels one-to-one without logging payment features.

    Outcome windows must be subsets of the retained prediction window. Partial
    label coverage is reported explicitly; metrics may be selection biased.
    """
    predictions = predictions.copy()
    prediction_columns = {"request_id", "model_version", "probability", "threshold"}
    if not prediction_columns.issubset(predictions) or not {"request_id", "target"}.issubset(
        outcomes
    ):
        raise ValueError("Missing prediction metadata or outcome fields")
    for name, frame in (("prediction", predictions), ("outcome", outcomes)):
        if frame.request_id.isna().any() or frame.request_id.duplicated().any():
            raise ValueError(f"{name} request_id must be unique and nonnull")
    if not set(outcomes.target.unique()).issubset({0, 1}):
        raise ValueError("Outcome target must be binary and nonnull")
    if set(outcomes.request_id) - set(predictions.request_id):
        raise ValueError("Outcome request_id missing from prediction window")
    if predictions.model_version.isna().any():
        raise ValueError("Missing model version")
    for column in ("probability", "threshold"):
        predictions[column] = pd.to_numeric(predictions[column], errors="coerce")
        if not predictions[column].between(0, 1).all():
            raise ValueError(f"Invalid {column}")
    joined = predictions[list(prediction_columns)].merge(
        outcomes[["request_id", "target"]], on="request_id", how="left", validate="one_to_one"
    )
    versions: dict[str, Any] = {}
    for version, rows in joined.groupby("model_version"):
        labeled = rows.dropna(subset=["target"])
        entry: dict[str, Any] = {
            "predictions": len(rows),
            "labeled": len(labeled),
            "label_coverage": len(labeled) / len(rows),
        }
        if set(labeled.target.unique()) == {0, 1}:
            # evaluate accepts a scalar threshold; for heterogeneous thresholds
            # preserve score metrics and compute decisions against each logged value.
            metrics = evaluate(labeled.target, labeled.probability, 0.5)
            decisions = labeled.probability >= labeled.threshold
            positives = labeled.target == 1
            tp = int((decisions & positives).sum())
            fp = int((decisions & ~positives).sum())
            fn = int((~decisions & positives).sum())
            tn = int((~decisions & ~positives).sum())
            metrics.update(
                {
                    "precision": tp / max(1, tp + fp),
                    "recall": tp / max(1, tp + fn),
                    "f1": 2 * tp / max(1, 2 * tp + fp + fn),
                    "false_positive_rate": fp / max(1, fp + tn),
                    "business_cost": (5 * fp + 100 * fn) / len(labeled),
                    "false_positives": float(fp),
                    "false_negatives": float(fn),
                    "true_positives": float(tp),
                }
            )
            entry["metrics"] = metrics
        else:
            entry["status"] = "awaiting_both_label_classes"
        versions[str(version)] = entry
    return {
        "predictions": len(joined),
        "labeled": len(outcomes),
        "label_coverage": len(outcomes) / max(1, len(joined)),
        "models": versions,
        "limitation": (
            "Observed-label selection bias can invalidate population performance estimates."
        ),
    }
