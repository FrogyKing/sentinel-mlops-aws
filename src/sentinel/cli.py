"""Local lifecycle commands; no implicit cloud deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("train", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, default=Path("artifacts/demo"))
        if name == "train":
            command.add_argument("--data", type=Path)
            command.add_argument("--seed", type=int, default=42)
            command.add_argument("--tracking-uri")
            command.add_argument("--export-model", type=Path, help="Export the selected candidate")
    acquire = commands.add_parser("acquire")
    acquire.add_argument("--output", type=Path, default=Path("data/raw/creditcard.csv"))
    monitoring = commands.add_parser("monitor")
    monitoring.add_argument("--reference", type=Path, required=True)
    monitoring.add_argument("--current", type=Path, required=True)
    monitoring.add_argument("--output", type=Path, default=Path("artifacts/monitor.json"))
    monitoring.add_argument("--evidently", action="store_true")
    outcomes = commands.add_parser("outcomes", help="Join delayed labels to prediction metadata")
    outcomes.add_argument("--predictions", type=Path, required=True)
    outcomes.add_argument("--labels", type=Path, required=True)
    outcomes.add_argument("--output", type=Path, default=Path("artifacts/performance.json"))
    registry = commands.add_parser("registry")
    registry.add_argument("action", choices=["state", "promote", "rollback"])
    registry.add_argument("--root", type=Path, default=Path("artifacts/demo"))
    registry.add_argument("--version")
    registry.add_argument("--approve", action="store_true")
    registry.add_argument("--policy", type=Path, help="JSON promotion policy overrides")
    registry.add_argument("--cohort", help="Shared evaluation cohort fingerprint")
    args = parser.parse_args()
    result: Any
    if args.command == "train":
        from sentinel.training import log_mlflow, train

        frame = pd.read_csv(args.data) if args.data else None
        result = train(args.output, frame=frame, seed=args.seed)
        if args.export_model:
            import shutil

            args.export_model.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(result["artifact_path"], args.export_model)
        if args.tracking_uri:
            result["mlflow_run_id"] = log_mlflow(args.output, args.tracking_uri)
    elif args.command == "demo":
        import joblib

        from sentinel.data import demo_data
        from sentinel.orchestration import demo

        result = demo(args.output)
        model = joblib.load(args.output / "model.joblib")
        probabilities = model["pipeline"].predict_proba(demo_data()[model["features"]].tail(100))
        with (args.output / "predictions.jsonl").open("w", encoding="utf-8") as stream:
            for index, probability in enumerate(probabilities[:, 1]):
                stream.write(
                    json.dumps(
                        {
                            "request_id": f"synthetic-demo-{index:04d}",
                            "model_version": model["version"],
                            "probability": float(probability),
                            "threshold": float(model["threshold"]),
                            "is_fraud": bool(probability >= model["threshold"]),
                            "evidence_type": "synthetic local batch inference",
                        }
                    )
                    + "\n"
                )
    elif args.command == "acquire":
        from sentinel.data import acquire_public

        result = acquire_public(args.output)
    elif args.command == "monitor":
        from sentinel.monitoring import evidently_report, monitor
        from sentinel.registry import write_json

        reference, current = pd.read_csv(args.reference), pd.read_csv(args.current)
        result = monitor(reference, current)
        write_json(args.output, result)
        if args.evidently:
            evidently_report(reference, current).save_html(str(args.output.with_suffix(".html")))
    elif args.command == "outcomes":
        from sentinel.monitoring import join_outcomes
        from sentinel.registry import write_json

        predictions = pd.read_json(args.predictions, lines=True)
        result = join_outcomes(predictions, pd.read_csv(args.labels))
        write_json(args.output, result)
    else:
        from sentinel.registry import Registry

        policy = json.loads(args.policy.read_text()) if args.policy else None
        store = Registry(args.root, policy=policy)
        if args.action == "promote":
            if not args.version:
                parser.error("promote requires --version")
            result = store.promote(args.version, approved=args.approve, cohort=args.cohort)
        elif args.action == "rollback":
            result = store.rollback(approved=args.approve)
        else:
            result = store.state()
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
