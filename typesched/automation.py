from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
import time
from typing import Callable

from .model import Target


class AutomationError(RuntimeError):
    pass


class ScreenLockedError(AutomationError):
    pass


@dataclass(frozen=True)
class WindowGeometry:
    x: int
    y: int
    width: int
    height: int


Runner = Callable[..., subprocess.CompletedProcess[str]]


class X11Automator:
    """Small, shell-free wrapper around xdotool."""

    def __init__(self, binary: str | None = None, runner: Runner = subprocess.run):
        self.binary = binary or shutil.which("xdotool") or ""
        self.runner = runner

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def _run(
        self,
        arguments: list[str],
        *,
        input_text: str | None = None,
        timeout: float = 8,
    ) -> str:
        if not self.available:
            raise AutomationError("xdotool is not installed")
        try:
            result = self.runner(
                [self.binary, *arguments],
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AutomationError(str(exc)) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "xdotool failed").strip()
            raise AutomationError(detail)
        return result.stdout.strip()

    @staticmethod
    def _parse_shell_values(output: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                result[key.strip()] = value.strip()
        return result

    def window_geometry(self, window_id: int) -> WindowGeometry:
        values = self._parse_shell_values(
            self._run(["getwindowgeometry", "--shell", str(window_id)])
        )
        try:
            return WindowGeometry(
                x=int(values["X"]),
                y=int(values["Y"]),
                width=int(values["WIDTH"]),
                height=int(values["HEIGHT"]),
            )
        except (KeyError, ValueError) as exc:
            raise AutomationError("Could not read the target window geometry") from exc

    def attach_window(self, target: Target) -> Target:
        """Attach the top-level window beneath the center of a selected area."""
        center_x, center_y = target.center
        self._run(["mousemove", "--sync", str(center_x), str(center_y)])
        values = self._parse_shell_values(self._run(["getmouselocation", "--shell"]))
        try:
            window_id = int(values["WINDOW"])
        except (KeyError, ValueError) as exc:
            raise AutomationError("Could not identify the window below the target") from exc

        geometry = self.window_geometry(window_id)
        title = self._run(["getwindowname", str(window_id)])
        window_class = self._run(["getwindowclassname", str(window_id)])

        if geometry.width <= 0 or geometry.height <= 0:
            raise AutomationError("The selected window has invalid dimensions")

        target.window_id = window_id
        target.window_title = title
        target.window_class = window_class
        target.window_x = geometry.x
        target.window_y = geometry.y
        target.window_width = geometry.width
        target.window_height = geometry.height
        target.relative_x = (center_x - geometry.x) / geometry.width
        target.relative_y = (center_y - geometry.y) / geometry.height
        return target

    def resolve_point(self, target: Target) -> tuple[int, int]:
        if target.window_id is None:
            return target.center

        geometry = self.window_geometry(target.window_id)
        relative_x = target.relative_x
        relative_y = target.relative_y
        if relative_x is None or relative_y is None:
            if target.window_x is None or target.window_y is None:
                return target.center
            return (
                geometry.x + (target.center[0] - target.window_x),
                geometry.y + (target.center[1] - target.window_y),
            )

        x = geometry.x + round(relative_x * geometry.width)
        y = geometry.y + round(relative_y * geometry.height)
        x = max(geometry.x + 1, min(x, geometry.x + geometry.width - 2))
        y = max(geometry.y + 1, min(y, geometry.y + geometry.height - 2))
        return x, y

    def screen_is_locked(self) -> bool:
        gdbus = shutil.which("gdbus")
        if not gdbus:
            return False
        endpoints = (
            ("org.xfce.ScreenSaver", "/org/xfce/ScreenSaver"),
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
        )
        for destination, object_path in endpoints:
            try:
                result = subprocess.run(
                    [
                        gdbus,
                        "call",
                        "--session",
                        "--dest",
                        destination,
                        "--object-path",
                        object_path,
                        "--method",
                        f"{destination}.GetActive",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=1,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                return "true" in result.stdout.lower()
        return False

    def send_message(
        self,
        target: Target,
        message: str,
        *,
        press_enter: bool = True,
        key_delay_ms: int = 18,
    ) -> None:
        if self.screen_is_locked():
            raise ScreenLockedError("The screen is locked")

        if target.window_id is not None:
            self._run(["windowactivate", "--sync", str(target.window_id)])
            time.sleep(0.2)

        x, y = self.resolve_point(target)
        self._run(["mousemove", "--sync", str(x), str(y), "click", "1"])
        time.sleep(0.12)

        lines = message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, line in enumerate(lines):
            if line:
                self._run(
                    [
                        "type",
                        "--clearmodifiers",
                        "--delay",
                        str(max(0, key_delay_ms)),
                        "--file",
                        "-",
                    ],
                    input_text=line,
                    timeout=max(8, len(line) * max(1, key_delay_ms) / 1000 + 5),
                )
            if index < len(lines) - 1:
                self._run(["key", "--clearmodifiers", "shift+Return"])

        if press_enter:
            self._run(["key", "--clearmodifiers", "Return"])
