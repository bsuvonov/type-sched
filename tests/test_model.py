from datetime import datetime, timedelta
import json
from pathlib import Path
import stat
import tempfile
import unittest

from typesched.model import Job, JobStore, Target, local_now


class TargetTests(unittest.TestCase):
    def test_center_and_round_trip(self):
        target = Target(x=10, y=20, width=101, height=41, window_id=42)
        self.assertEqual(target.center, (60, 40))
        self.assertEqual(Target.from_dict(target.to_dict()), target)


class JobStoreTests(unittest.TestCase):
    def test_round_trip_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "jobs.json"
            store = JobStore(path)
            job = Job(
                message="hello 世界",
                run_at=(local_now() + timedelta(minutes=2)).isoformat(),
                target=Target(1, 2, 30, 12),
                repeat_every_minutes=120,
            )
            store.save([job])

            loaded = store.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].message, "hello 世界")
            self.assertEqual(loaded[0].target.center, (16, 8))
            self.assertEqual(loaded[0].repeat_every_minutes, 120)
            self.assertEqual(loaded[0].repeat_label, "Every 2 hours")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text())["version"], 2)

    def test_version_one_job_loads_without_recurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            job = Job(
                message="old job",
                run_at=(local_now() + timedelta(minutes=2)).isoformat(),
                target=Target(1, 2, 30, 12),
            ).to_dict()
            for key in ("repeat_every_minutes", "last_run_at", "run_count", "last_error"):
                job.pop(key)
            path.write_text(json.dumps({"version": 1, "jobs": [job]}))

            loaded = JobStore(path).load()
            self.assertEqual(len(loaded), 1)
            self.assertFalse(loaded[0].is_recurring)
            self.assertEqual(loaded[0].run_count, 0)

    def test_invalid_file_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            path.write_text("not json")
            store = JobStore(path)
            self.assertEqual(store.load(), [])
            self.assertIn("Could not read", store.load_warning)

    def test_newer_store_version_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            original = json.dumps({"version": 999, "jobs": []})
            path.write_text(original)
            store = JobStore(path)
            self.assertEqual(store.load(), [])
            self.assertIn("newer than supported", store.load_warning)
            with self.assertRaisesRegex(OSError, "refusing to overwrite"):
                store.save([])
            self.assertEqual(path.read_text(), original)


class RecurrenceTests(unittest.TestCase):
    def test_next_run_preserves_cadence_and_skips_catch_up(self):
        job = Job(
            message="ping",
            run_at="2026-08-24T10:00:00+08:00",
            target=Target(1, 2, 30, 12),
            repeat_every_minutes=15,
        )

        self.assertEqual(
            job.next_run_after(datetime.fromisoformat("2026-08-24T10:00:00+08:00")),
            datetime.fromisoformat("2026-08-24T10:15:00+08:00"),
        )
        self.assertEqual(
            job.next_run_after(datetime.fromisoformat("2026-08-24T10:47:00+08:00")),
            datetime.fromisoformat("2026-08-24T11:00:00+08:00"),
        )

    def test_next_run_keeps_future_first_occurrence(self):
        job = Job(
            message="ping",
            run_at="2026-08-24T10:00:00+08:00",
            target=Target(1, 2, 30, 12),
            repeat_every_minutes=1440,
        )
        self.assertEqual(job.repeat_label, "Every 1 day")
        self.assertEqual(
            job.next_run_after(datetime.fromisoformat("2026-08-24T09:00:00+08:00")),
            job.scheduled_time,
        )

    def test_non_positive_interval_disables_recurrence(self):
        job = Job(
            message="ping",
            run_at="2026-08-24T10:00:00+08:00",
            target=Target(1, 2, 30, 12),
            repeat_every_minutes=0,
        )
        self.assertFalse(job.is_recurring)
        self.assertIsNone(job.next_run_after(local_now()))

        invalid = Job(
            message="ping",
            run_at="2026-08-24T10:00:00+08:00",
            target=Target(1, 2, 30, 12),
            repeat_every_minutes="not-a-number",
        )
        self.assertFalse(invalid.is_recurring)

    def test_advance_is_strictly_after_planned_time_when_clock_moves_back(self):
        job = Job(
            message="ping",
            run_at="2026-08-24T10:00:00+08:00",
            target=Target(1, 2, 30, 12),
            repeat_every_minutes=5,
        )
        next_run = job.advance_recurrence(
            datetime.fromisoformat("2026-08-24T09:55:00+08:00")
        )
        self.assertEqual(
            next_run, datetime.fromisoformat("2026-08-24T10:05:00+08:00")
        )
        self.assertEqual(job.state, "pending")

    def test_recurring_success_advances_and_counts_send(self):
        job = Job(
            message="ping",
            run_at="2026-08-24T10:00:00+08:00",
            target=Target(1, 2, 30, 12),
            repeat_every_minutes=15,
            last_error="old error",
        )
        next_run = job.finish_attempt(
            datetime.fromisoformat("2026-08-24T10:02:00+08:00")
        )
        self.assertEqual(
            next_run, datetime.fromisoformat("2026-08-24T10:15:00+08:00")
        )
        self.assertEqual(job.state, "pending")
        self.assertEqual(job.run_count, 1)
        self.assertIsNone(job.last_error)

    def test_recurring_failure_advances_without_counting_send(self):
        job = Job(
            message="ping",
            run_at="2026-08-24T10:00:00+08:00",
            target=Target(1, 2, 30, 12),
            repeat_every_minutes=15,
        )
        next_run = job.finish_attempt(
            datetime.fromisoformat("2026-08-24T10:02:00+08:00"), "target missing"
        )
        self.assertEqual(
            next_run, datetime.fromisoformat("2026-08-24T10:15:00+08:00")
        )
        self.assertEqual(job.state, "pending")
        self.assertEqual(job.run_count, 0)
        self.assertEqual(job.last_error, "target missing")
        self.assertIn("Last attempt failed", job.error)


if __name__ == "__main__":
    unittest.main()
