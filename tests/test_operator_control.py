import tempfile
import unittest
from pathlib import Path

from scripts.operator_control import request_pause, workload, paused


class OperatorControlTests(unittest.TestCase):
    def test_pause_finishes_current_attempt_and_resume_keeps_saved_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            saved = directory / "attempt.json"
            with workload(directory) as check:
                self.assertFalse(paused(check))
                request_pause(directory)
                # A pause is observed at the attempt boundary, not a cancellation.
                saved.write_text('{"state":"completed"}', encoding="utf-8")
                self.assertTrue(paused(check))
                with self.assertRaises(ValueError):
                    with workload(directory):
                        self.fail("A second workload acquired the active lock")
                self.assertTrue(check())
            self.assertFalse((directory / "analysis.lock").exists())
            with workload(directory) as check:
                self.assertFalse(paused(check))
                self.assertEqual(saved.read_text(encoding="utf-8"), '{"state":"completed"}')

    def test_failure_releases_workload_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(KeyboardInterrupt):
                with workload(directory):
                    raise KeyboardInterrupt()
            with workload(directory) as check:
                self.assertFalse(check())
