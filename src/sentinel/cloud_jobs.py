"""Ephemeral S3 batch jobs. Training produces candidates, never promotions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import boto3
import pandas as pd

from sentinel.monitoring import monitor
from sentinel.registry import write_json
from sentinel.training import train


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    action = event["action"]
    if action not in {"monitor", "retrain"}:
        raise ValueError("Unsupported action")
    bucket = event["bucket"]
    prefix = event["output_prefix"].strip("/")
    if not prefix.startswith("jobs/") or ".." in prefix:
        raise ValueError("Output must be a job-owned prefix")
    s3 = boto3.client("s3")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name in ("reference", "current"):
            key = event[f"{name}_key"]
            if not key.startswith("datasets/"):
                raise ValueError("Input keys must belong to datasets/")
            size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
            if size > 200 * 1024 * 1024:
                raise ValueError("Batch input exceeds the 200 MiB bounded demo limit")
            s3.download_file(bucket, key, str(root / f"{name}.csv"))
        reference, current = pd.read_csv(root / "reference.csv"), pd.read_csv(root / "current.csv")
        if action == "monitor":
            report = monitor(reference, current)
            labeled = bool(event.get("labels_available")) and "target" in current
            labeled = labeled and bool(current["target"].notna().all())
            report.update(
                labels_available=labeled,
                retraining_required=bool(report["drift_detected"] and not report["quality_issues"]),
            )
            write_json(root / "report.json", report)
            key = f"{prefix}/monitor.json"
            s3.upload_file(str(root / "report.json"), bucket, key)
            return {**report, "report_key": key}
        if not event.get("labels_available") or "target" not in current:
            raise ValueError("Retraining requires explicit labeled data")
        report = train(root / "candidate", frame=current)
        for path in (root / "candidate").rglob("*"):
            if path.is_file():
                key = f"{prefix}/candidate/{path.relative_to(root / 'candidate').as_posix()}"
                s3.upload_file(str(path), bucket, key)
        return {
            "candidate_version": report["version"],
            "report_key": f"{prefix}/candidate/evaluation.json",
            "requires_approval": True,
        }
