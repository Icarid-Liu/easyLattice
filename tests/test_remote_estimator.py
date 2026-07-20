import json
import unittest
from unittest.mock import patch

from app.remote_estimator import RemoteEstimatorClient, RemoteEstimatorConfig


class RemoteEstimatorTests(unittest.TestCase):
    def test_nested_nonfinite_job_metadata_is_sanitized(self):
        client = RemoteEstimatorClient(
            RemoteEstimatorConfig(
                base_url="https://estimator.invalid",
                timeout_seconds=5,
                poll_interval_seconds=0,
            )
        )
        submitted = {"job_id": "job-1"}
        completed = {
            "status": "succeeded",
            "result": {
                "ok": True,
                "bits": 143.25,
                "diagnostics": {
                    "nan": float("nan"),
                    "nested": [float("inf"), {"value": float("-inf"), "keep": 2}],
                },
            },
        }

        with patch.object(client, "post_json", return_value=submitted), patch.object(
            client,
            "get_json",
            return_value=completed,
        ):
            result = client.estimate({"problem": "lwe"})

        self.assertEqual(result["bits"], 143.25)
        self.assertIsNone(result["diagnostics"]["nan"])
        self.assertEqual(
            result["diagnostics"]["nested"],
            [None, {"value": None, "keep": 2}],
        )
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
