import contextvars
import threading
import unittest
from dataclasses import FrozenInstanceError

from app.job_progress import ProgressEvent, progress_reporting, report_progress


class JobProgressTests(unittest.TestCase):
    def test_report_progress_is_noop_without_reporter(self):
        report_progress("candidate_search")

    def test_reporter_receives_structured_events_in_order_and_resets(self):
        events = []

        with progress_reporting(events.append):
            report_progress("candidate_search")
            report_progress("estimator_running", "standard", "01234567")

        report_progress("finalizing")

        self.assertEqual(
            [event.stage for event in events],
            ["candidate_search", "estimator_running"],
        )
        self.assertEqual(events[1].estimator_profile, "standard")
        self.assertEqual(events[1].estimator_commit, "01234567")

    def test_progress_event_is_frozen(self):
        event = ProgressEvent("candidate_search")

        with self.assertRaises(FrozenInstanceError):
            event.stage = "finalizing"

    def test_reporter_resets_after_exception(self):
        events = []

        with self.assertRaisesRegex(RuntimeError, "job failed"):
            with progress_reporting(events.append):
                report_progress("candidate_search")
                raise RuntimeError("job failed")

        report_progress("finalizing")

        self.assertEqual([event.stage for event in events], ["candidate_search"])

    def test_copied_context_reporter_does_not_leak_to_parent_context(self):
        parent_events = []
        copied_events = []

        with progress_reporting(parent_events.append):
            copied_context = contextvars.copy_context()

            def report_in_copied_context():
                with progress_reporting(copied_events.append):
                    report_progress("estimator_running", "enhanced", "89abcdef")

            copied_context.run(report_in_copied_context)
            report_progress("candidate_search")

        self.assertEqual(
            [event.stage for event in parent_events],
            ["candidate_search"],
        )
        self.assertEqual(
            copied_events,
            [ProgressEvent("estimator_running", "enhanced", "89abcdef")],
        )

    def test_new_thread_has_no_reporter_without_context_copy(self):
        events = []

        with progress_reporting(events.append):
            thread = threading.Thread(
                target=report_progress,
                args=("candidate_search",),
            )
            thread.start()
            thread.join()

        self.assertEqual(events, [])

    def test_copied_context_can_be_run_in_another_thread(self):
        events = []

        with progress_reporting(events.append):
            copied_context = contextvars.copy_context()
            thread = threading.Thread(
                target=copied_context.run,
                args=(report_progress, "estimator_running", "standard", "01234567"),
            )
            thread.start()
            thread.join()

        self.assertEqual(
            events,
            [ProgressEvent("estimator_running", "standard", "01234567")],
        )


if __name__ == "__main__":
    unittest.main()
