import unittest
import uuid
from unittest.mock import Mock

from typesched.model import Target
from typesched.ui import Gio, Gtk, TypeSchedWindow


@unittest.skipUnless(Gtk.init_check()[0], "GTK display required")
class ProcedureEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Gtk.Application(
            application_id=f"io.github.typesched.Test{uuid.uuid4().hex}",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        cls.app.register(None)

    def setUp(self):
        self.app.jobs = []
        self.app.add_job = Mock()
        self.window = TypeSchedWindow(self.app)

    def tearDown(self):
        self.window.destroy()

    def test_edit_switch_reorder_remove_and_schedule(self):
        first = Target(10, 20, 30, 40)
        second = Target(500, 600, 30, 40)
        self.window.set_target(first)
        self.window.message_view.get_buffer().set_text("first")
        self.window.enter_check.set_active(False)
        self.window.step_wait.set_value(2.5)
        self.window._add_step()
        self.assertIsNone(self.window.current_target)
        self.assertEqual(self.window.get_message(), "")
        self.window.set_target(second)
        self.window.message_view.get_buffer().set_text("second")
        self.window.step_combo.set_active(0)
        self.assertEqual(self.window.get_message(), "first")
        self.assertEqual(self.window.current_target, first)
        self.assertFalse(self.window.enter_check.get_active())
        self.assertEqual(self.window.step_wait.get_value(), 2.5)
        self.window._move_step(1)
        self.window._schedule_clicked(None)
        steps = self.app.add_job.call_args.kwargs["steps"]
        self.assertEqual([step.message for step in steps], ["second", "first"])
        self.assertEqual([step.target for step in steps], [second, first])
        self.window._remove_step()
        self.assertEqual(len(self.window.draft_steps), 1)
        self.assertEqual(self.window.get_message(), "second")
        self.window.clear_message()
        self.assertEqual(len(self.window.draft_steps), 1)
        self.assertIsNone(self.window.current_target)
        self.assertEqual(self.window.get_message(), "")

    def test_add_step_keeps_unfinished_step_and_text(self):
        self.window.message_view.get_buffer().set_text("Keep this message")
        self.window.step_wait.set_value(2.5)
        self.window._add_step()
        self.assertEqual(len(self.window.draft_steps), 1)
        self.assertEqual(self.window.step_index, 0)
        self.assertEqual(self.window.get_message(), "Keep this message")
        self.assertEqual(self.window.step_wait.get_value(), 2.5)
        self.assertIn("step 1", self.window.feedback_label.get_text())

    def test_missing_target_preserves_current_editor_and_all_steps(self):
        first = Target(10, 20, 30, 40)
        second = Target(500, 600, 30, 40)
        self.window.set_target(first)
        self.window.message_view.get_buffer().set_text("First message")
        self.window._add_step()
        self.window.set_target(second)
        self.window.message_view.get_buffer().set_text("Second message")
        self.window.step_wait.set_value(3.5)
        # Reproduce an incomplete earlier step from the previous editor behavior.
        self.window.draft_steps[0].target = None
        self.window._refresh_steps()
        self.window._schedule_clicked(None)
        self.app.add_job.assert_not_called()
        self.assertEqual(self.window.step_index, 1)
        self.assertEqual(self.window.step_combo.get_active(), 1)
        self.assertEqual(self.window.get_message(), "Second message")
        self.assertEqual(self.window.current_target, second)
        self.assertEqual(self.window.step_wait.get_value(), 3.5)
        self.assertEqual([step.message for step in self.window.draft_steps],
                         ["First message", "Second message"])
        self.assertIn("Step 1", self.window.feedback_label.get_text())
        self.window.step_combo.set_active(0)
        self.assertEqual(self.window.get_message(), "First message")
        self.window.set_target(first)
        self.window._schedule_clicked(None)
        steps = self.app.add_job.call_args.kwargs["steps"]
        self.assertEqual([step.message for step in steps], ["First message", "Second message"])
        self.assertEqual([step.target for step in steps], [first, second])


if __name__ == "__main__":
    unittest.main()
