"""Export compact local execution evidence without datasets or private paths."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    evidence = ROOT / "docs" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for mode, folder in (("synthetic", "delivery-demo"), ("public", "public")):
        report = json.loads((ROOT / "artifacts" / folder / "evaluation.json").read_text())
        report.pop("artifact_path", None)
        (evidence / f"{mode}-evaluation.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    demo = json.loads((ROOT / "artifacts/delivery-demo/demo.json").read_text())
    compact = {
        "scope": "Actual local execution on synthetic data; no AWS operations",
        "normal_drift_detected": demo["normal"]["drift_detected"],
        "unlabeled_status": demo["unlabeled_drift"]["status"],
        "drift_detected": demo["unlabeled_drift"]["monitoring"]["drift_detected"],
        "candidate_retrained": demo["retraining"]["retrained"],
        "default_policy_deployment": demo["deployment"],
        "recovery_status": demo["recovery_exercise"]["status"],
        "recovery_policy_override": demo["recovery_exercise"]["policy_override"],
        "recovery_failure_trigger": demo["recovery_exercise"]["failure_trigger"],
    }
    (evidence / "lifecycle.json").write_text(json.dumps(compact, indent=2) + "\n")
    print("Exported public, synthetic and lifecycle execution evidence.")


if __name__ == "__main__":
    main()
