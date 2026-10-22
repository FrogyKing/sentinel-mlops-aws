"""Local single-writer registry with explicit approval and auditable promotion."""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


class Registry:
    """Single-machine registry. Use a conditional-write service for concurrency."""

    def __init__(self, root: Path, policy: dict[str, float] | None = None):
        self.root = Path(root)
        self.path = self.root / "registry.json"
        self.policy = {
            "min_pr_auc": 0.2,
            "min_recall": 0.5,
            "max_fpr": 0.15,
            "max_pr_auc_regression": 0.01,
            "max_recall_regression": 0.02,
        }
        if policy:
            if set(policy) - set(self.policy):
                raise ValueError("Unknown promotion policy setting")
            self.policy.update(policy)
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.policy.values()):
            raise ValueError("Policy values must be finite probabilities")

    def state(self) -> dict[str, Any]:
        if self.path.exists():
            return dict(json.loads(self.path.read_text(encoding="utf-8")))
        return {"models": {}, "champion": None, "previous": None, "history": []}

    def _event(self, state: dict[str, Any], action: str, version: str) -> None:
        state["history"].append(
            {"at": datetime.now(UTC).isoformat(), "action": action, "version": version}
        )
        write_json(self.path, state)

    def register(self, version: str, metrics: dict[str, Any], artifact_path: str) -> dict[str, Any]:
        if not Path(artifact_path).is_file():
            raise ValueError("Model artifact does not exist")
        state = self.state()
        if version in state["models"]:
            raise ValueError("Model versions are immutable")
        state["models"][version] = {
            "metrics": metrics,
            "artifact_path": str(Path(artifact_path).resolve()),
        }
        self._event(state, "registered", version)
        return state

    def record_evaluation(
        self, version: str, cohort: str, metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """Append immutable evaluation evidence without changing registered metrics."""
        state = self.state()
        if version not in state["models"] or not cohort:
            raise ValueError("Unknown model or missing evaluation cohort")
        evaluations = state.setdefault("evaluations", {})
        key = f"{version}:{cohort}"
        if key in evaluations:
            raise ValueError("Evaluation evidence is immutable")
        evaluations[key] = {**metrics, "evaluation_dataset_sha256": cohort}
        self._event(state, "evaluated", version)
        return state

    def promote(
        self, version: str, approved: bool = False, cohort: str | None = None
    ) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Explicit deployment approval is required")
        state = self.state()
        if state["champion"] == version:
            raise ValueError("Candidate is already champion")
        model = state["models"].get(version)
        if model is None:
            raise ValueError("Unknown candidate")
        metrics = (
            state.get("evaluations", {}).get(f"{version}:{cohort}") if cohort else model["metrics"]
        )
        if metrics is None:
            raise ValueError("Missing candidate evaluation on requested cohort")
        if not metrics.get("evaluation_dataset_sha256"):
            raise ValueError("Missing evaluation cohort fingerprint")
        required = ("pr_auc", "recall", "false_positive_rate", "business_cost")
        if any(not math.isfinite(float(metrics.get(k, float("nan")))) for k in required):
            raise ValueError("Missing or invalid evaluation metrics")
        if any(not 0 <= metrics[k] <= 1 for k in required[:3]) or metrics["business_cost"] < 0:
            raise ValueError("Metrics outside valid range")
        if (
            metrics["pr_auc"] < self.policy["min_pr_auc"]
            or metrics["recall"] < self.policy["min_recall"]
            or metrics["false_positive_rate"] > self.policy["max_fpr"]
        ):
            raise ValueError("Candidate fails minimum quality policy")
        champion = state["champion"]
        if champion:
            previous = (
                state.get("evaluations", {}).get(f"{champion}:{cohort}")
                if cohort
                else state["models"][champion]["metrics"]
            )
            if previous is None:
                raise ValueError("Missing champion evaluation on requested cohort")
            cohort = metrics.get("evaluation_dataset_sha256")
            previous_cohort = previous.get("evaluation_dataset_sha256")
            if cohort != previous_cohort:
                raise ValueError(
                    "Evaluate champion and candidate on the same labeled cohort before promotion"
                )
            if (
                metrics["pr_auc"] < previous["pr_auc"] - self.policy["max_pr_auc_regression"]
                or metrics["recall"] < previous["recall"] - self.policy["max_recall_regression"]
            ):
                raise ValueError("Candidate regresses champion quality")
            if metrics["business_cost"] >= previous["business_cost"]:
                raise ValueError("Candidate must improve business cost")
        state["previous"], state["champion"] = champion, version
        self._event(state, "promoted", version)
        return state

    def rollback(self, approved: bool = False) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Explicit rollback approval is required")
        state = self.state()
        if state["previous"] is None:
            raise ValueError("No known-good previous deployment")
        state["champion"], state["previous"] = state["previous"], state["champion"]
        self._event(state, "rolled_back", state["champion"])
        return state
