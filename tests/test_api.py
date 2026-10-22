"""Exercise actual model inference and readiness through the HTTP boundary."""

from pathlib import Path

import joblib
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.data import demo_data
from sentinel.training import train


@pytest.fixture(scope="module")
def trained_artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("api-model")
    train(root)
    return root


def test_readiness_requires_model(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        response = client.post("/predict", json={"features": {"amount": 10.0}})
        assert response.status_code == 503


def test_prediction_uses_real_model(trained_artifacts: Path) -> None:
    frame = demo_data()
    artifact = joblib.load(trained_artifacts / "model.joblib")
    features = frame[artifact["features"]].iloc[0].to_dict()
    with TestClient(create_app(trained_artifacts)) as client:
        assert client.get("/readyz").status_code == 200
        response = client.post("/predict", json={"features": features})
        assert response.status_code == 200, response.text
        prediction = response.json()
        assert 0.0 <= prediction["probability"] <= 1.0
        expected = float(artifact["pipeline"].predict_proba(pd.DataFrame([features]))[0, 1])
        assert prediction["probability"] == pytest.approx(expected)
        assert prediction["is_fraud"] == (expected >= artifact["threshold"])
        assert isinstance(prediction["is_fraud"], bool)
        assert prediction["model_version"]
        assert prediction["request_id"]
        repeated = client.post("/predict", json={"features": features}).json()
        assert prediction["probability"] == repeated["probability"]
        assert prediction["request_id"] != repeated["request_id"]


def test_schema_rejects_missing_extra_and_non_numeric_features(trained_artifacts: Path) -> None:
    artifact = joblib.load(trained_artifacts / "model.joblib")
    features = demo_data()[artifact["features"]].iloc[0].to_dict()
    invalid_payloads = [
        {key: value for key, value in features.items() if key != "amount"},
        {**features, "card_number": 1234.0},
        {**features, "amount": "not-a-number"},
    ]
    with TestClient(create_app(trained_artifacts)) as client:
        for invalid in invalid_payloads:
            response = client.post("/predict", json={"features": invalid})
            assert response.status_code == 422, response.text
        response = client.post(
            "/predict",
            content='{"features":{"amount":NaN}}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422


def test_prediction_logs_do_not_expose_feature_values(
    trained_artifacts: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="sentinel.predictions")
    artifact = joblib.load(trained_artifacts / "model.joblib")
    features = demo_data()[artifact["features"]].iloc[0].to_dict()
    features["amount"] = 938275.123456789
    with TestClient(create_app(trained_artifacts)) as client:
        response = client.post("/predict", json={"features": features})
        assert response.status_code == 200
    assert "938275.123456789" not in caplog.text
    assert '"event": "prediction"' in caplog.text
