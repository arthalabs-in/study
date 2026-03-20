"""
Pomodoro Timer — focus timer for the Study TUI.
Configurable work/break intervals. Tracks completed pomodoros and focus time.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Callable


class PomodoroState(Enum):
    IDLE = "idle"
    WORKING = "working"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"


class PomodoroTimer:
    """Pomodoro focus timer with configurable intervals."""

    def __init__(
        self,
        work_mins: int = 25,
        short_break_mins: int = 5,
        long_break_mins: int = 15,
        long_break_after: int = 4,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.work_mins = work_mins
        self.short_break_mins = short_break_mins
        self.long_break_mins = long_break_mins
        self.long_break_after = long_break_after
        self.on_status = on_status

        self._state = PomodoroState.IDLE
        self._start_time: float = 0
        self._duration_secs: int = 0
        self._completed: int = 0
        self._total_focus_secs: float = 0
        self._task: asyncio.Task | None = None

    # ── Controls ───────────────────────────────────────────────────

    def start(self, work_mins: int | None = None) -> dict:
        """Start a new pomodoro work session."""
        if self._state == PomodoroState.WORKING:
            return self.status()

        duration = (work_mins or self.work_mins) * 60
        self._state = PomodoroState.WORKING
        self._start_time = time.time()
        self._duration_secs = duration

        self._emit(f"🍅 Pomodoro started! {duration // 60} min focus session.")
        return self.status()

    def stop(self) -> dict:
        """Stop the current timer."""
        if self._state == PomodoroState.IDLE:
            return {"status": "idle", "message": "No timer running."}

        elapsed = time.time() - self._start_time
        if self._state == PomodoroState.WORKING:
            self._total_focus_secs += elapsed

        self._state = PomodoroState.IDLE
        self._emit("⏹ Timer stopped.")
        return {
            "status": "stopped",
            "elapsed_mins": round(elapsed / 60, 1),
            "completed_pomodoros": self._completed,
        }

    def skip(self) -> dict:
        """Skip current phase (work→break or break→work)."""
        if self._state == PomodoroState.IDLE:
            return {"status": "idle", "message": "No timer running."}

        if self._state == PomodoroState.WORKING:
            elapsed = time.time() - self._start_time
            self._total_focus_secs += elapsed
            self._completed += 1
            return self._start_break()
        else:
            return self.start()

    def status(self) -> dict:
        """Get current timer status."""
        if self._state == PomodoroState.IDLE:
            return {
                "status": "idle",
                "completed_pomodoros": self._completed,
                "total_focus_mins": round(self._total_focus_secs / 60, 1),
            }

        elapsed = time.time() - self._start_time
        remaining = max(0, self._duration_secs - elapsed)
        remaining_mins = int(remaining // 60)
        remaining_secs = int(remaining % 60)

        # Check if timer has naturally expired
        if remaining <= 0:
            if self._state == PomodoroState.WORKING:
                self._total_focus_secs += self._duration_secs
                self._completed += 1
                self._emit(f"✅ Pomodoro #{self._completed} complete! Time for a break.")
                return self._start_break()
            else:
                self._emit("☕ Break's over! Ready for the next pomodoro.")
                self._state = PomodoroState.IDLE
                return self.status()

        return {
            "status": self._state.value,
            "remaining": f"{remaining_mins:02d}:{remaining_secs:02d}",
            "remaining_mins": round(remaining / 60, 1),
            "elapsed_mins": round(elapsed / 60, 1),
            "completed_pomodoros": self._completed,
            "total_focus_mins": round(self._total_focus_secs / 60, 1),
        }

    def _start_break(self) -> dict:
        """Transition to a break."""
        if self._completed % self.long_break_after == 0:
            self._state = PomodoroState.LONG_BREAK
            duration = self.long_break_mins
            self._emit(f"🎉 {self._completed} pomodoros done! Take a {duration} min long break.")
        else:
            self._state = PomodoroState.SHORT_BREAK
            duration = self.short_break_mins
            self._emit(f"☕ Take a {duration} min break. ({self._completed} pomodoros done)")

        self._start_time = time.time()
        self._duration_secs = duration * 60
        return self.status()

    def _emit(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)
