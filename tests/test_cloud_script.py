"""Cloud safety control tests; no AWS operations are performed."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import joblib
import pytest

SPEC = importlib.util.spec_from_file_location(
    "cloud_commands", Path(__file__).resolve().parents[1] / "scripts" / "cloud.py"
)
assert SPEC is not None and SPEC.loader is not None
cloud = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud)


@pytest.mark.parametrize("approval", [[], ["--approve-cost"], ["--approve-deployment"]])
def test_deploy_requires_both_approvals(monkeypatch, approval):
    command = Mock()
    monkeypatch.setattr(cloud, "run", command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cloud",
            "deploy",
            "--account",
            "123456789012",
            "--state-bucket",
            "state",
            "--budget-email",
            "test@example.com",
            *approval,
        ],
    )
    with pytest.raises(SystemExit):
        cloud.main()
    command.assert_not_called()


def deploy_args(monkeypatch, tmp_path):
    model = tmp_path / "model.joblib"
    model.write_bytes(b"test artifact, never executed")
    monkeypatch.setattr(
        joblib,
        "load",
        lambda _: {"pipeline": None, "features": ["amount"], "threshold": 0.5, "version": "test"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cloud",
            "deploy",
            "--approve-cost",
            "--approve-deployment",
            "--account",
            "123456789012",
            "--state-bucket",
            "state",
            "--budget-email",
            "test@example.com",
            "--model",
            str(model),
        ],
    )


def test_docker_failure_precedes_any_aws_operation(monkeypatch, tmp_path):
    deploy_args(monkeypatch, tmp_path)
    command = Mock(side_effect=subprocess.CalledProcessError(1, "docker"))
    monkeypatch.setattr(cloud, "run", command)
    with pytest.raises(subprocess.CalledProcessError):
        cloud.main()
    assert len(command.call_args_list) == 1
    assert command.call_args.args[:2] == ("docker", "version")


def test_failed_deployment_restores_known_good_alias(monkeypatch, tmp_path):
    deploy_args(monkeypatch, tmp_path)
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("aws", "sts", "get-caller-identity"):
            return json.dumps({"Account": "123456789012"})
        if args[:3] == ("aws", "lambda", "get-alias"):
            return json.dumps({"FunctionVersion": "7"})
        if args[:3] == ("terraform", "output", "-raw"):
            return "https://example.execute-api.us-east-1.amazonaws.com"
        return "test"

    monkeypatch.setattr(cloud, "run", command)
    monkeypatch.setattr(cloud.shutil, "which", lambda _: "/fake/docker")
    monkeypatch.setattr(cloud.subprocess, "run", Mock())
    monkeypatch.setattr(cloud, "smoke_check", Mock(side_effect=RuntimeError("failed")))
    with pytest.raises(RuntimeError, match="restored prior version"):
        cloud.main()
    rollback = [args for args in calls if args[:3] == ("aws", "lambda", "update-alias")]
    assert len(rollback) == 1
    assert rollback[0][rollback[0].index("--function-version") + 1] == "7"
    assert not any("put-function-concurrency" in args for args in calls)


def test_smoke_refuses_non_aws_or_non_https_urls():
    for url in ("file:///etc/passwd", "http://example.com", "https://example.com"):
        with pytest.raises(ValueError, match="AWS HTTPS"):
            cloud.smoke_check(url, "us-east-1", ["amount"])


@pytest.mark.parametrize(
    "address,before",
    [
        ("aws_iam_role.unrelated", {"name": "production-payments"}),
        ("aws_apigatewayv2_integration.inference", {"api_id": "unrelated"}),
        ("aws_iam_role.inference", {"name": "production-payments"}),
    ],
)
def test_destroy_fails_closed_for_unrelated_state(monkeypatch, address, before):
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("aws", "sts", "get-caller-identity"):
            return json.dumps({"Account": "123456789012"})
        if args[:3] == ("terraform", "show", "-json"):
            return json.dumps(
                {"resource_changes": [{"address": address, "change": {"before": before}}]}
            )
        return ""

    monkeypatch.setattr(cloud, "run", command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cloud",
            "destroy",
            "--approve-destroy",
            "--account",
            "123456789012",
            "--state-bucket",
            "state",
            "--budget-email",
            "test@example.com",
        ],
    )
    with pytest.raises(RuntimeError):
        cloud.main()
    assert not any(args[:2] == ("terraform", "apply") for args in calls)
