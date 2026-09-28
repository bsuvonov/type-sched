from datetime import timedelta
from threading import Event
import unittest
from unittest.mock import Mock, patch

from typesched.automation import AutomationError, ProcedureCancelled, X11Automator
from typesched.model import Job, Step, Target, local_now
from typesched.ui import TypeSchedApplication
from tests.test_scheduler import FakeApplication, recurring_job


class ProcedureTests(unittest.TestCase):
    def setUp(self):
        self.automator = X11Automator(binary="xdotool")
        self.steps = [
            Step(Target(10, 20, 30, 40), "", False),
            Step(Target(200, 300, 30, 40, window_id=42), "first", True, 2),
            Step(Target(500, 600, 30, 40, window_id=43), "second", True, 3),
            Step(Target(800, 900, 30, 40), "", False),
        ]

    def test_actions_and_waits_execute_in_order(self):
        timeline = []
        cancel = Mock(spec=Event)
        cancel.wait.side_effect = lambda delay: timeline.append(("wait", delay)) or False
        cancel.is_set.return_value = False
        with patch.object(self.automator, "send_message") as send:
            send.side_effect = lambda target, message, **kwargs: timeline.append(
                ("send", target, message, kwargs["press_enter"])
            )
            self.automator.run_steps(self.steps, cancel=cancel)
        expected = []
        for step in self.steps:
            expected.extend([("wait", step.wait_seconds),
                             ("send", step.target, step.message, step.press_enter)])
        self.assertEqual(timeline, expected)

    def test_failure_stops_remaining_steps(self):
        with patch.object(self.automator, "send_message",
                          side_effect=[None, AutomationError("window missing")]) as send:
            cancel = Mock(spec=Event)
            cancel.wait.return_value = cancel.is_set.return_value = False
            with self.assertRaisesRegex(AutomationError, "Step 2 of 4 failed: window missing"):
                self.automator.run_steps(self.steps, cancel=cancel)
        self.assertEqual(send.call_count, 2)

    def test_cancel_during_wait_does_not_start_next_step(self):
        cancel = Mock(spec=Event)
        cancel.wait.side_effect = [False, True]
        cancel.is_set.return_value = False
        with patch.object(self.automator, "send_message") as send:
            with self.assertRaises(ProcedureCancelled):
                self.automator.run_steps(self.steps, cancel=cancel)
        self.assertEqual(send.call_count, 1)

    def test_cancellation_after_action_stops_procedure(self):
        cancel = Event()
        with patch.object(self.automator, "send_message", side_effect=lambda *a, **k: cancel.set()) as send:
            with self.assertRaises(ProcedureCancelled):
                self.automator.run_steps(self.steps, cancel=cancel)
        self.assertEqual(send.call_count, 1)

    def test_scheduling_copies_every_step(self):
        app = FakeApplication(recurring_job())
        app.jobs = []
        app.automator = self.automator
        TypeSchedApplication.add_job(
            app, "", local_now() + timedelta(minutes=5), self.steps[0].target,
            False, 15, steps=self.steps,
        )
        self.assertEqual(app.jobs[0].steps, self.steps)
        self.steps[1].target.x = 999
        self.steps[1].message = "changed"
        self.assertEqual(app.jobs[0].steps[1].target.x, 200)
        self.assertEqual(app.jobs[0].steps[1].message, "first")

    def test_stopped_recurring_procedure_does_not_repeat(self):
        job = Job("", local_now().isoformat(), self.steps[0].target,
                  steps=self.steps, repeat_every_minutes=15, state="sending")
        app = FakeApplication(job)
        TypeSchedApplication._finish_job(app, job.id, "Stopped", cancelled=True)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.run_count, 0)

    def test_worker_runs_all_steps_as_one_job(self):
        job = Job("", local_now().isoformat(), self.steps[0].target, steps=self.steps)
        app = FakeApplication(job)
        app.automator = Mock()
        app._finish_job = Mock()
        with patch("typesched.ui.GLib.timeout_add", side_effect=lambda delay, fn: fn()), \
             patch("typesched.ui.threading.Thread") as thread, \
             patch("typesched.ui.GLib.idle_add") as idle:
            thread.side_effect = lambda **kwargs: Mock(start=kwargs["target"])
            TypeSchedApplication._start_job(app, job)
        app.automator.run_steps.assert_called_once_with(
            self.steps, key_delay_ms=job.key_delay_ms, cancel=app.cancel_event,
        )
        idle.assert_called_once_with(app._finish_job, job.id, None, False)


if __name__ == "__main__":
    unittest.main()
