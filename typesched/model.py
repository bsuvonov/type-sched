from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import uuid


APP_ID = "io.github.typesched.TypeSched"
APP_NAME = "TypeSched"
STORE_VERSION = 2


def local_now() -> datetime:
    return datetime.now().astimezone()


@dataclass
class Target:
    x: int
    y: int
    width: int
    height: int
    window_id: int | None = None
    window_title: str = ""
    window_class: str = ""
    window_x: int | None = None
    window_y: int | None = None
    window_width: int | None = None
    window_height: int | None = None
    relative_x: float | None = None
    relative_y: float | None = None

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    @property
    def area_label(self) -> str:
        return f"{self.width} × {self.height} at ({self.x}, {self.y})"

    @property
    def display_name(self) -> str:
        if self.window_title:
            return self.window_title
        if self.window_class:
            return self.window_class
        return "Screen position"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Target":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})


@dataclass
class Job:
    message: str
    run_at: str
    target: Target
    press_enter: bool = True
    key_delay_ms: int = 18
    repeat_every_minutes: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = "pending"
    created_at: str = field(default_factory=lambda: local_now().isoformat())
    completed_at: str | None = None
    last_run_at: str | None = None
    run_count: int = 0
    last_error: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.repeat_every_minutes is not None:
            try:
                self.repeat_every_minutes = int(self.repeat_every_minutes)
            except (TypeError, ValueError):
                self.repeat_every_minutes = None
            else:
                if self.repeat_every_minutes <= 0:
                    self.repeat_every_minutes = None

    @property
    def scheduled_time(self) -> datetime:
        value = datetime.fromisoformat(self.run_at)
        return value if value.tzinfo else value.astimezone()

    @property
    def is_recurring(self) -> bool:
        return self.repeat_every_minutes is not None

    @property
    def repeat_label(self) -> str:
        minutes = self.repeat_every_minutes
        if minutes is None:
            return "Does not repeat"
        if minutes % 1440 == 0:
            count, unit = minutes // 1440, "day"
        elif minutes % 60 == 0:
            count, unit = minutes // 60, "hour"
        else:
            count, unit = minutes, "minute"
        suffix = "" if count == 1 else "s"
        return f"Every {count} {unit}{suffix}"

    def next_run_after(self, value: datetime) -> datetime | None:
        if self.repeat_every_minutes is None:
            return None
        after = value if value.tzinfo else value.astimezone()
        scheduled = self.scheduled_time
        interval = timedelta(minutes=self.repeat_every_minutes)
        if scheduled > after:
            return scheduled
        elapsed_seconds = (after - scheduled).total_seconds()
        steps = int(elapsed_seconds // interval.total_seconds()) + 1
        return scheduled + interval * steps

    def advance_recurrence(
        self, after: datetime, note: str | None = None
    ) -> datetime:
        if not self.is_recurring:
            raise ValueError("Cannot advance a non-recurring job")
        cutoff = max(after if after.tzinfo else after.astimezone(), self.scheduled_time)
        next_run = self.next_run_after(cutoff)
        assert next_run is not None
        self.run_at = next_run.isoformat()
        self.state = "pending"
        self.completed_at = None
        self.error = note
        return next_run

    def finish_attempt(
        self, completed_at: datetime, failure: str | None = None
    ) -> datetime | None:
        finished = completed_at if completed_at.tzinfo else completed_at.astimezone()
        self.completed_at = finished.isoformat()
        self.last_run_at = finished.isoformat()
        if failure:
            self.last_error = failure
            if self.is_recurring:
                return self.advance_recurrence(
                    finished, f"Last attempt failed: {failure}"
                )
            self.state = "failed"
            self.error = failure
            return None

        self.run_count += 1
        self.last_error = None
        self.error = None
        if self.is_recurring:
            return self.advance_recurrence(finished)
        self.state = "sent"
        return None

    def to_dict(self) -> dict:
        value = asdict(self)
        value["target"] = self.target.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: dict) -> "Job":
        data = dict(value)
        data["target"] = Target.from_dict(data["target"])
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            config_root = Path(
                os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            )
            path = config_root / "type-sched" / "jobs.json"
        self.path = path
        self.load_warning: str | None = None

    def load(self) -> list[Job]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            version = int(payload.get("version", 1))
            if version > STORE_VERSION:
                raise ValueError(
                    f"job file version {version} is newer than supported version {STORE_VERSION}"
                )
            return [Job.from_dict(item) for item in payload.get("jobs", [])]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.load_warning = f"Could not read saved jobs: {exc}"
            return []

    def save(self, jobs: list[Job]) -> None:
        if self.load_warning:
            raise OSError(
                "refusing to overwrite a job file that could not be read; "
                "move or repair it first"
            )
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "version": STORE_VERSION,
            "jobs": [job.to_dict() for job in jobs],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="jobs-", suffix=".tmp", dir=self.path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
