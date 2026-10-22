"""Explicitly authorized AWS lifecycle commands; never run automatically on push."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TF = ROOT / "infrastructure" / "terraform"


def smoke_check(url: str, region: str, features: list[str]) -> None:
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    if not re.fullmatch(r"https://[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com", url):
        raise ValueError("Smoke checks require a project AWS HTTPS API endpoint")
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials unavailable for signed smoke checks")
    for path, body in [
        ("/readyz", None),
        ("/predict", json.dumps({"features": dict.fromkeys(features, 0.0)})),
    ]:
        request = AWSRequest(
            method="POST" if body else "GET",
            url=url + path,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        SigV4Auth(credentials.get_frozen_credentials(), "execute-api", region).add_auth(request)
        signed = request.prepare()
        with urllib.request.urlopen(  # nosec B310
            urllib.request.Request(
                signed.url, data=body.encode() if body else None, headers=dict(signed.headers)
            ),
            timeout=45,
        ) as response:
            result = json.load(response)
        if path == "/readyz" and result.get("status") != "ready":
            raise RuntimeError("Readiness check failed")
        if path == "/predict" and not 0 <= result.get("probability", -1) <= 1:
            raise RuntimeError("Prediction smoke check failed")


def run(*args: str, cwd: Path = ROOT, capture: bool = False) -> str:
    executable = shutil.which(args[0])
    if executable is None:
        raise RuntimeError(f"Required executable unavailable: {args[0]}")
    # Commands are argument lists; user text is never passed through a shell.
    result = subprocess.run(  # nosec B603
        [executable, *args[1:]], cwd=cwd, check=True, text=True, capture_output=capture
    )
    return result.stdout.strip() if capture else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["cost-check", "deploy", "destroy"])
    parser.add_argument("--account")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--budget-email")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--state-bucket")
    parser.add_argument("--approve-cost", action="store_true")
    parser.add_argument("--approve-deployment", action="store_true")
    parser.add_argument("--approve-destroy", action="store_true")
    args = parser.parse_args()
    if args.command == "cost-check":
        print(
            json.dumps(
                {
                    "region": args.region,
                    "planning_ceiling_usd": 20,
                    "demo_target_usd": 15,
                    "estimated_demo_usd": "2-5",
                    "assumptions": (
                        "10k requests; 1GB Lambda 0.5s; 1GB S3/ECR; 0.1GB logs; no free tier"
                    ),
                    "warning": (
                        "Estimate and budget alerts are not a cap. See docs/runbooks/costs.md."
                    ),
                },
                indent=2,
            )
        )
        return
    if not args.account or not args.state_bucket or not args.budget_email:
        parser.error("--account, --state-bucket and --budget-email are mandatory")
    if args.command == "deploy" and not args.approve_cost:
        parser.error(
            "Deployment creates billable infrastructure; explicit --approve-cost is required"
        )
    if args.command == "deploy" and not args.approve_deployment:
        parser.error("An evaluated model must be explicitly approved with --approve-deployment")
    if args.command == "destroy" and not args.approve_destroy:
        parser.error("Review project-owned resources before explicit --approve-destroy")
    if args.command == "deploy":
        if not args.model or not args.model.is_file():
            parser.error("--model must name an evaluated, trusted local model.joblib")
        import joblib

        bundle = joblib.load(args.model)
        if not {"pipeline", "features", "threshold", "version"}.issubset(bundle):
            parser.error("Model bundle lacks required serving metadata")
        run("docker", "version", "--format", "{{.Server.Version}}", capture=True)
        run("terraform", "version", capture=True)
        tag = run("git", "rev-parse", "HEAD", capture=True)
        repository = f"{args.account}.dkr.ecr.{args.region}.amazonaws.com/sentinel-mlops"
        image = f"{repository}:{tag}"
        run("docker", "build", "--platform", "linux/amd64", "-t", image, ".")
    identity = json.loads(
        run("aws", "sts", "get-caller-identity", "--output", "json", capture=True)
    )
    if identity["Account"] != args.account:
        parser.error("Active AWS account does not match the explicitly selected account")
    previous_alias = None
    if args.command == "deploy":
        try:
            previous_alias = json.loads(
                run(
                    "aws",
                    "lambda",
                    "get-alias",
                    "--function-name",
                    "sentinel-mlops",
                    "--name",
                    "live",
                    "--region",
                    args.region,
                    "--output",
                    "json",
                    capture=True,
                )
            )
        except subprocess.CalledProcessError as exc:
            if "ResourceNotFoundException" not in (exc.stderr or ""):
                raise
    # No keys are printed or written. Terraform uses the caller's credential chain.
    env_values = {
        "account_id": args.account,
        "region": args.region,
        "budget_email": args.budget_email,
    }
    for key, value in env_values.items():
        os.environ[f"TF_VAR_{key}"] = value
    os.environ.setdefault("TF_VAR_image_uri", "bootstrap@sha256:" + "0" * 64)
    os.environ.setdefault("TF_VAR_model_sha256", "0" * 64)
    run(
        "terraform",
        "init",
        "-reconfigure",
        f"-backend-config=bucket={args.state_bucket}",
        "-backend-config=key=sentinel-mlops/terraform.tfstate",
        f"-backend-config=region={args.region}",
        "-backend-config=encrypt=true",
        "-backend-config=use_lockfile=true",
        cwd=TF,
    )
    if args.command == "destroy":
        # Persist the exact reviewed plan, then refuse any resource outside our addresses/tags.
        run("terraform", "plan", "-destroy", "-out=destroy.tfplan", cwd=TF)
        plan = json.loads(run("terraform", "show", "-json", "destroy.tfplan", cwd=TF, capture=True))
        owned_api_ids = {
            item["change"]["before"]["id"]
            for item in plan.get("resource_changes", [])
            if item["address"] == "aws_apigatewayv2_api.inference"
            and item["change"].get("before", {}).get("tags_all", {}).get("Project")
            == "sentinel-mlops"
        }
        allowed_addresses: set[str] = set()
        for source in TF.glob("*.tf"):
            allowed_addresses.update(
                f"{kind}.{name}"
                for kind, name in re.findall(
                    r'resource\s+"([a-z0-9_]+)"\s+"([a-z0-9_]+)"', source.read_text()
                )
            )
        for change in plan.get("resource_changes", []):
            before = change["change"].get("before") or {}
            if change["address"] not in allowed_addresses:
                raise RuntimeError("Refusing to destroy resources outside this root module")
            tags = before.get("tags_all", {})
            if "Project" in tags and tags["Project"] != "sentinel-mlops":
                raise RuntimeError("Project ownership mismatch")
            name = before.get("name", before.get("bucket", before.get("function_name", "")))
            if tags.get("Project") != "sentinel-mlops":
                parent_values = [
                    before.get(field, "")
                    for field in (
                        "bucket",
                        "repository",
                        "role",
                        "function_name",
                        "topic_arn",
                        "rule",
                    )
                ]
                owned_parent = any(
                    str(value).startswith("sentinel-mlops") or ":sentinel-mlops" in str(value)
                    for value in parent_values
                )
                owned_api = before.get("api_id") in owned_api_ids
                if not (str(name).startswith("sentinel-mlops") or owned_parent or owned_api):
                    raise RuntimeError(f"Cannot establish ownership for {change['address']}")
        run("terraform", "apply", "destroy.tfplan", cwd=TF)
        print(
            "Destroy applied. Nonempty S3/ECR resources require reviewed cleanup. "
            "No unrelated data is deleted."
        )
        return
    if not args.model or not args.model.is_file():
        parser.error("--model must name an evaluated, trusted local model.joblib")
    digest = hashlib.sha256(args.model.read_bytes()).hexdigest()
    os.environ["TF_VAR_model_sha256"] = digest
    os.environ["TF_VAR_model_key"] = f"models/{digest}/model.joblib"
    run(
        "terraform",
        "apply",
        "-auto-approve",
        "-target=aws_ecr_repository.inference",
        "-target=aws_s3_bucket.artifacts",
        "-target=aws_s3_bucket_versioning.artifacts",
        "-target=aws_s3_bucket_public_access_block.artifacts",
        "-target=aws_s3_bucket_server_side_encryption_configuration.artifacts",
        "-target=aws_s3_bucket_policy.tls",
        "-target=aws_budgets_budget.demo",
        cwd=TF,
    )
    repository = f"{args.account}.dkr.ecr.{args.region}.amazonaws.com/sentinel-mlops"
    password = run("aws", "ecr", "get-login-password", "--region", args.region, capture=True)
    docker_executable = shutil.which("docker")
    if docker_executable is None:
        raise RuntimeError("Docker is required")
    # ECR password is passed on stdin, never in the process arguments or logs.
    subprocess.run(  # nosec B603
        [
            docker_executable,
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            repository.split("/")[0],
        ],
        input=password,
        text=True,
        check=True,
        capture_output=True,
    )
    run("docker", "push", image)
    image_digest = run(
        "aws",
        "ecr",
        "describe-images",
        "--repository-name",
        "sentinel-mlops",
        "--image-ids",
        f"imageTag={tag}",
        "--query",
        "imageDetails[0].imageDigest",
        "--output",
        "text",
        "--region",
        args.region,
        capture=True,
    )
    os.environ["TF_VAR_image_uri"] = f"{repository}@{image_digest}"
    bucket = f"sentinel-mlops-{args.account}-{args.region}"
    run(
        "aws",
        "s3",
        "cp",
        str(args.model),
        f"s3://{bucket}/{os.environ['TF_VAR_model_key']}",
        "--sse",
        "AES256",
        "--region",
        args.region,
    )
    run("terraform", "plan", "-out=deploy.tfplan", cwd=TF)
    run("terraform", "apply", "deploy.tfplan", cwd=TF)
    run("terraform", "output", cwd=TF)
    api_url = run("terraform", "output", "-raw", "api_url", cwd=TF, capture=True)
    try:
        smoke_check(api_url, args.region, bundle["features"])
    except Exception:
        if previous_alias:
            run(
                "aws",
                "lambda",
                "update-alias",
                "--function-name",
                "sentinel-mlops",
                "--name",
                "live",
                "--function-version",
                previous_alias["FunctionVersion"],
                "--routing-config",
                "AdditionalVersionWeights={}",
                "--region",
                args.region,
            )
        else:
            run(
                "aws",
                "lambda",
                "put-function-concurrency",
                "--function-name",
                "sentinel-mlops",
                "--reserved-concurrent-executions",
                "0",
                "--region",
                args.region,
            )
        raise RuntimeError(
            "Deployment checks failed; restored prior version or disabled initial inference. "
            "Inspect logs and reconcile Terraform."
        ) from None
    print("Infrastructure applied; signed readiness and real prediction smoke checks passed.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Operation failed ({exc.returncode}); inspect state before retrying.")
