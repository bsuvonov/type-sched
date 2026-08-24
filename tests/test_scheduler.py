from datetime import datetime
import unittest
from unittest.mock import patch

from typesched.model import Job, Target
from typesched.ui import TypeSchedApplication


class FakeWindow:
    def __init__(self, visible=True):
        self.visible = visible
        self.refresh_count = 0

    def get_visible(self):
        return self.visible

    def refresh_jobs(self):
        self.refresh_count += 1

    def hide(self):
        self.visible = False


class FakeApplication:
    def __init__(self, job, persist_result=True, restore=False):
        self.jobs = [job]
        self.active_job_id = job.id
        self.restore_window_after_job = restore
        self.window = FakeWindow()
        self.persist_result = persist_result
        self.notifications = []
        self.show_count = 0
        self.tray_updates = 0

    def _find_job(self, job_id):
        return next((job for job in self.jobs if job.id == job_id), None)

    def _persist(self):
        return self.persist_result

    def _notify(self, title, body, notification_id):
        self.notifications.append((title, body, notification_id))

    def _update_tray(self):
        self.tray_updates += 1

    def show_window(self):
        self.show_count += 1


def recurring_job():
    return Job(
        message="ping",
        run_at="2026-08-24T10:00:00+08:00",
        target=Target(1, 2, 30, 12),
        repeat_every_minutes=15,
        state="sending",
    )


class SchedulerTransitionTests(unittest.TestCase):
    def test_finish_success_keeps_recurring_job_pending_and_restores_window(self):
        job = recurring_job()
        app = FakeApplication(job, restore=True)

        with patch(
            "typesched.ui.local_now",
            return_value=datetime.fromisoformat("2026-08-24T10:02:00+08:00"),
        ):
            TypeSchedApplication._finish_job(app, job.id, None)

        self.assertEqual(job.state, "pending")
        self.assertEqual(job.scheduled_time.hour, 10)
        self.assertEqual(job.scheduled_time.minute, 15)
        self.assertEqual(job.run_count, 1)
        self.assertEqual(app.show_count, 1)
        self.assertIn("Next:", app.notifications[0][1])

    def test_finish_failure_keeps_recurring_job_pending(self):
        job = recurring_job()
        app = FakeApplication(job)

        with patch(
            "typesched.ui.local_now",
            return_value=datetime.fromisoformat("2026-08-24T10:03:00+08:00"),
        ):
            TypeSchedApplication._finish_job(app, job.id, "target missing")

        self.assertEqual(job.state, "pending")
        self.assertEqual(job.run_count, 0)
        self.assertEqual(job.last_error, "target missing")
        self.assertIn("Next attempt:", app.notifications[0][1])

    def test_start_aborts_before_automation_when_sending_state_cannot_be_saved(self):
        job = recurring_job()
        job.state = "pending"
        app = FakeApplication(job, persist_result=False)
        app.active_job_id = None

        TypeSchedApplication._start_job(app, job)

        self.assertEqual(job.state, "pending")
        self.assertIsNone(app.active_job_id)
        self.assertTrue(app.window.visible)
        self.assertEqual(app.window.refresh_count, 1)


if __name__ == "__main__":
    unittest.main()
