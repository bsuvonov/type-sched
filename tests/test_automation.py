from subprocess import CompletedProcess
import unittest
from unittest.mock import call, patch

from typesched.automation import (
    AutomationError,
    LINE_BREAK_SETTLE_SECONDS,
    MODIFIER_SETTLE_SECONDS,
    SEND_SETTLE_SECONDS,
    TEXT_SETTLE_SECONDS,
    WindowGeometry,
    X11Automator,
)
from typesched.model import Target


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs.get("input")))
        command = arguments[1]
        if command == "getmouselocation":
            output = "X=150\nY=225\nSCREEN=0\nWINDOW=99\n"
        elif command == "getwindowgeometry":
            output = "WINDOW=99\nX=100\nY=100\nWIDTH=400\nHEIGHT=500\nSCREEN=0\n"
        elif command == "getwindowname":
            output = "Chat with Ada\n"
        elif command == "getwindowclassname":
            output = "ChatApp\n"
        else:
            output = ""
        return CompletedProcess(arguments, 0, output, "")


class AutomationTests(unittest.TestCase):
    def test_attach_window_tracks_relative_center(self):
        runner = FakeRunner()
        automator = X11Automator(binary="/usr/bin/xdotool", runner=runner)
        target = automator.attach_window(Target(130, 200, 40, 50))

        self.assertEqual(target.window_id, 99)
        self.assertEqual(target.window_title, "Chat with Ada")
        self.assertEqual(target.window_class, "ChatApp")
        self.assertAlmostEqual(target.relative_x, 0.125)
        self.assertAlmostEqual(target.relative_y, 0.25)

    def test_resolve_point_follows_resized_window(self):
        automator = X11Automator(binary="/usr/bin/xdotool")
        target = Target(
            130,
            200,
            40,
            50,
            window_id=99,
            relative_x=0.25,
            relative_y=0.8,
        )
        with patch.object(
            automator, "window_geometry", return_value=WindowGeometry(20, 40, 800, 600)
        ):
            self.assertEqual(automator.resolve_point(target), (220, 520))

    def test_multiline_message_uses_shift_enter_then_enter(self):
        runner = FakeRunner()
        automator = X11Automator(binary="/usr/bin/xdotool", runner=runner)
        target = Target(10, 20, 100, 40)
        with patch.object(automator, "screen_is_locked", return_value=False), patch(
            "typesched.automation.time.sleep"
        ) as sleep:
            automator.send_message(target, "first\nsecond", key_delay_ms=12)

        commands = [call[0][1] for call in runner.calls]
        typed = [call[1] for call in runner.calls if call[0][1] == "type"]
        key_calls = [call[0] for call in runner.calls if call[0][1] == "key"]
        self.assertEqual(commands[:2], ["mousemove", "type"])
        self.assertEqual(typed, ["first", "second"])
        self.assertIn("shift+Return", key_calls[0])
        self.assertIn("Return", key_calls[-1])
        self.assertIn("keyup", commands)
        self.assertEqual(
            sleep.call_args_list,
            [
                call(0.12),
                call(LINE_BREAK_SETTLE_SECONDS),
                call(TEXT_SETTLE_SECONDS),
                call(MODIFIER_SETTLE_SECONDS),
                call(SEND_SETTLE_SECONDS),
            ],
        )

    def test_final_enter_releases_shift_first(self):
        runner = FakeRunner()
        automator = X11Automator(binary="/usr/bin/xdotool", runner=runner)
        target = Target(10, 20, 100, 40)
        with patch.object(automator, "screen_is_locked", return_value=False), patch(
            "typesched.automation.time.sleep"
        ):
            automator.send_message(target, "hello", press_enter=True)

        commands = [call[0][1:] for call in runner.calls]
        self.assertEqual(
            commands[-2:],
            [
                ["keyup", "Shift_L", "Shift_R"],
                ["key", "--clearmodifiers", "Return"],
            ],
        )

    def test_empty_message_only_clicks(self):
        runner = FakeRunner()
        automator = X11Automator(binary="/usr/bin/xdotool", runner=runner)
        target = Target(10, 20, 100, 40)
        with patch.object(automator, "screen_is_locked", return_value=False), patch(
            "typesched.automation.time.sleep"
        ):
            automator.send_message(target, "", press_enter=True)

        commands = [call[0][1] for call in runner.calls]
        self.assertEqual(commands, ["mousemove"])

    def test_command_failure_becomes_automation_error(self):
        def failed(arguments, **_kwargs):
            return CompletedProcess(arguments, 1, "", "bad window")

        automator = X11Automator(binary="/usr/bin/xdotool", runner=failed)
        with self.assertRaisesRegex(AutomationError, "bad window"):
            automator.window_geometry(123)


if __name__ == "__main__":
    unittest.main()
