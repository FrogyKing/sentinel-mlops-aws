"""Inference boundary: shared preprocessing, bounded input, metadata-only logs."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mangum import Mangum
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("sentinel.predictions")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: dict[str, float] = Field(min_length=1, max_length=100)

    @field_validator("features")
    @classmethod
    def finite_features(cls, values: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(v) for v in values.values()):
            raise ValueError("Features must be finite")
        return values


def download_model(directory: Path) -> None:
    """Only load trusted deployment artifacts with an independently pinned digest."""
    bucket = os.getenv("SENTINEL_ARTIFACT_BUCKET")
    if not bucket:
        return
    import boto3

    key = os.environ["SENTINEL_MODEL_KEY"]
    digest = os.environ["SENTINEL_MODEL_SHA256"]
    directory.mkdir(parents=True, exist_ok=True)
    pending = directory / "model.download"
    boto3.client("s3").download_file(bucket, key, str(pending))
    if hashlib.sha256(pending.read_bytes()).hexdigest() != digest:
        pending.unlink(missing_ok=True)
        raise ValueError("Model checksum mismatch")
    pending.replace(directory / "model.joblib")


def create_app(artifact_dir: Path | None = None) -> FastAPI:
    directory = artifact_dir or Path(os.getenv("SENTINEL_ARTIFACT_DIR", "artifacts/demo"))

    @asynccontextmanager
    async def lifespan(application: FastAPI):  # type: ignore[no-untyped-def]
        if getattr(application.state, "loaded", False):
            yield
            return
        download_model(directory)
        path = directory / "model.joblib"
        if not os.getenv("SENTINEL_ARTIFACT_BUCKET") and (directory / "registry.json").exists():
            from sentinel.registry import Registry

            state = Registry(directory).state()
            if state["champion"]:
                path = Path(state["models"][state["champion"]]["artifact_path"])
        application.state.model = joblib.load(path) if path.exists() else None
        application.state.loaded = True
        yield

    application = FastAPI(title="Sentinel Inference", version="0.1.0", lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Any, error: RequestValidationError) -> JSONResponse:
        # Never echo raw payment inputs or nonstandard JSON constants in validation errors.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                    for item in error.errors()
                ]
            },
        )

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    def ready() -> dict[str, str]:
        model = getattr(application.state, "model", None)
        if model is None:
            raise HTTPException(503, "No deployed model")
        return {"status": "ready", "model_version": str(model["version"])}

    @application.post("/predict")
    def predict(request: PredictionRequest) -> dict[str, Any]:
        started = time.perf_counter()
        model = getattr(application.state, "model", None)
        if model is None:
            raise HTTPException(503, "No deployed model")
        features = model["features"]
        if set(request.features) != set(features):
            raise HTTPException(422, f"Expected features: {features}")
        frame = pd.DataFrame([{name: request.features[name] for name in features}])
        request_id = uuid.uuid4().hex
        try:
            probability = float(model["pipeline"].predict_proba(frame)[0, 1])
        except (ValueError, OverflowError) as exc:
            logger.warning(json.dumps({"event": "prediction_error", "request_id": request_id}))
            raise HTTPException(422, "Features outside supported model domain") from exc
        result = {
            "request_id": request_id,
            "model_version": str(model["version"]),
            "probability": probability,
            "threshold": float(model["threshold"]),
            "is_fraud": probability >= float(model["threshold"]),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        logger.info(json.dumps({"event": "prediction", **result}))
        return result

    return application


app = create_app()
handler = Mangum(app)
