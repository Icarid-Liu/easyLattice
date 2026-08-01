import unittest
from threading import Event
from unittest.mock import patch

from app.config import AppConfig
from app.estimator_tasks import EstimatorTask, run_estimator_task
from app.job_progress import progress_reporting


class EstimatorTaskTests(unittest.TestCase):
    def test_task_validates_model_and_mode(self):
        with self.assertRaises(ValueError):
            EstimatorTask("unknown", "classical", "usvp")
        with self.assertRaises(ValueError):
            EstimatorTask("matzov", "unknown", "usvp")

    def test_pre_requested_cancellation_does_not_prepare_estimator(self):
        cancel = Event()
        cancel.set()
        events = []
        with patch("app.estimator_tasks.run_estimator") as runner:
            with progress_reporting(events.append):
                result = run_estimator_task(
                    {"problem": "lwe", "n": 512, "q": 257},
                    EstimatorTask("matzov", "classical", "usvp"),
                    AppConfig(),
                    "enhanced",
                    cancel,
                )

        self.assertEqual(result["code"], "attack_cancelled")
        runner.assert_not_called()
        self.assertEqual(events[-1].stage, "estimator_cancelled")
        self.assertTrue(events[-1].cancelled)
        self.assertEqual(events[-1].attack, "usvp")

    def test_task_adds_selector_and_reports_completion(self):
        events = []
        with patch(
            "app.estimator_tasks.run_estimator",
            return_value={"ok": True, "estimator_commit": "abc123"},
        ) as runner:
            with progress_reporting(events.append):
                result = run_estimator_task(
                    {"problem": "lwe", "n": 512, "q": 257},
                    EstimatorTask("adps16", "quantum", "dual_hybrid"),
                    AppConfig(),
                    "enhanced",
                )

        self.assertEqual(result["task"]["model"], "adps16")
        self.assertEqual(events[-1].stage, "estimator_attack_completed")
        self.assertEqual(events[-1].completed, 1)
        self.assertEqual(events[-1].total, 1)
        self.assertEqual(runner.call_args.kwargs["cancel_event"], None)
        self.assertEqual(
            runner.call_args.args[0]["attack"],
            "dual_hybrid",
        )


if __name__ == "__main__":
    unittest.main()
