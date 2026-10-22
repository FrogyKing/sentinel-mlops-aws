"""Validated tabular data and explicitly synthetic demonstration data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests


def demo_data(n: int = 2500, seed: int = 42) -> pd.DataFrame:
    """Create reproducible nonlinear fraud-like labels, not real transactions."""
    if n < 100:
        raise ValueError("At least 100 rows are required")
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    amount = rng.lognormal(3.3, 1.1, n)
    signal = 1.8 * v[:, 0] - 1.2 * v[:, 1] + 1.4 * v[:, 0] * v[:, 2]
    signal += 0.5 * np.log1p(amount) + rng.normal(0, 0.45, n)
    target = (signal > np.quantile(signal, 0.95)).astype(int)
    frame = pd.DataFrame(
        {
            "amount": amount,
            "time": np.arange(n),
            "v1": v[:, 0],
            "v2": v[:, 1],
            "v3": v[:, 2],
            "target": target,
        }
    )
    frame.attrs["synthetic"] = True
    return frame


def validate_data(frame: pd.DataFrame, require_target: bool = True) -> None:
    if frame.empty or not frame.columns.is_unique:
        raise ValueError("Data must be nonempty with unique column names")
    if require_target and "target" not in frame:
        raise ValueError("Missing target")
    values = frame.select_dtypes(include="number")
    if len(values.columns) != len(frame.columns):
        raise ValueError("All columns must be numeric")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("Missing and nonfinite values are not allowed")
    if require_target and set(frame["target"].unique()) != {0, 1}:
        raise ValueError("Target must contain both binary classes")
    if "amount" in frame and (frame["amount"] < 0).any():
        raise ValueError("Negative transaction amounts")


def acquire_public(destination: Path) -> dict[str, Any]:
    """Fetch the public TensorFlow tutorial's ULB mirror with a bounded download.

    The mirror may omit Time. In that case preserve published row order using
    an ordinal time column; this is an ordering proxy, not a real timestamp.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(".download")
    url = "https://storage.googleapis.com/download.tensorflow.org/data/creditcard.csv"
    size = 0
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as response:
            response.raise_for_status()
            with pending.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        raise ValueError("Dataset exceeds 200 MiB acquisition bound")
                    stream.write(chunk)
        frame = pd.read_csv(pending)
    finally:
        pending.unlink(missing_ok=True)
    frame.columns = [str(c).lower() for c in frame.columns]
    frame = frame.rename(columns={"class": "target"})
    frame["target"] = frame["target"].astype(int)
    temporal_proxy = "time" not in frame
    if temporal_proxy:
        frame["time"] = np.arange(len(frame))
    frame = frame.astype(float)
    validate_data(frame)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False)
    metadata = {
        "source": "https://www.tensorflow.org/tutorials/structured_data/imbalanced_data",
        "rows": len(frame),
        "fraud_count": int(frame.target.sum()),
        "synthetic": False,
        "temporal_order_proxy": temporal_proxy,
        "download_url": url,
        "license": "Upstream Open Database / Database Contents; review source terms",
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }
    destination.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2))
    return metadata
