"""Cloud adapter checks never contact AWS or create infrastructure."""

from unittest.mock import Mock, patch

import pytest

from sentinel.cloud_jobs import handler


def test_invalid_action_and_unowned_prefix_fail_before_aws() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        handler({"action": "deploy"}, None)
    with pytest.raises(ValueError, match="job-owned"):
        handler({"action": "monitor", "bucket": "demo", "output_prefix": "other"}, None)


def test_batch_limits_dataset_size_before_download() -> None:
    client = Mock()
    client.head_object.return_value = {"ContentLength": 201 * 1024 * 1024}
    event = {
        "action": "monitor",
        "bucket": "demo",
        "output_prefix": "jobs/test",
        "reference_key": "datasets/reference.csv",
        "current_key": "datasets/current.csv",
    }
    with patch("sentinel.cloud_jobs.boto3.client", return_value=client):
        with pytest.raises(ValueError, match="200 MiB"):
            handler(event, None)
    client.download_file.assert_not_called()
