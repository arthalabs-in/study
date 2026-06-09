from __future__ import annotations

from src.pomodoro import PomodoroState, PomodoroTimer


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def test_start_stop_and_idle_status(monkeypatch) -> None:
    clock = FakeClock()
    emitted: list[str] = []
    monkeypatch.setattr('src.pomodoro.time.time', clock.time)

    timer = PomodoroTimer(work_mins=10, on_status=emitted.append)

    idle = timer.status()
    assert idle == {'status': 'idle', 'completed_pomodoros': 0, 'total_focus_mins': 0.0}

    working = timer.start()
    assert working['status'] == 'working'
    assert working['remaining'] == '10:00'
    assert emitted[-1] == '🍅 Pomodoro started! 10 min focus session.'

    clock.now += 150
    stopped = timer.stop()
    assert stopped == {'status': 'stopped', 'elapsed_mins': 2.5, 'completed_pomodoros': 0}
    assert emitted[-1] == '⏹ Timer stopped.'
    assert timer.status()['total_focus_mins'] == 2.5


def test_start_while_working_returns_current_status(monkeypatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr('src.pomodoro.time.time', clock.time)
    timer = PomodoroTimer(work_mins=5)

    timer.start()
    clock.now += 30

    status = timer.start(work_mins=20)
    assert status['status'] == 'working'
    assert status['remaining'] == '04:30'


def test_skip_from_work_transitions_to_short_break(monkeypatch) -> None:
    clock = FakeClock()
    emitted: list[str] = []
    monkeypatch.setattr('src.pomodoro.time.time', clock.time)
    timer = PomodoroTimer(work_mins=25, short_break_mins=3, long_break_after=4, on_status=emitted.append)

    timer.start()
    clock.now += 120
    status = timer.skip()

    assert timer._state == PomodoroState.SHORT_BREAK
    assert status['status'] == 'short_break'
    assert status['remaining'] == '03:00'
    assert status['completed_pomodoros'] == 1
    assert timer._total_focus_secs == 120
    assert emitted[-1] == '☕ Take a 3 min break. (1 pomodoros done)'


def test_skip_from_break_restarts_work(monkeypatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr('src.pomodoro.time.time', clock.time)
    timer = PomodoroTimer(work_mins=12, short_break_mins=1)

    timer._state = PomodoroState.SHORT_BREAK
    timer._start_time = clock.time()
    timer._duration_secs = 60

    status = timer.skip()
    assert status['status'] == 'working'
    assert status['remaining'] == '12:00'


def test_status_expiry_enters_long_break(monkeypatch) -> None:
    clock = FakeClock(now=5_000.0)
    emitted: list[str] = []
    monkeypatch.setattr('src.pomodoro.time.time', clock.time)
    timer = PomodoroTimer(work_mins=1, long_break_mins=9, long_break_after=2, on_status=emitted.append)

    timer._state = PomodoroState.WORKING
    timer._start_time = clock.time() - 61
    timer._duration_secs = 60
    timer._completed = 1

    status = timer.status()
    assert status['status'] == 'long_break'
    assert status['remaining'] == '09:00'
    assert status['completed_pomodoros'] == 2
    assert emitted[0] == '✅ Pomodoro #2 complete! Time for a break.'
    assert emitted[1] == '🎉 2 pomodoros done! Take a 9 min long break.'


def test_work_completion_event_emits_once_during_repeated_status_checks(monkeypatch) -> None:
    clock = FakeClock(now=5_000.0)
    emitted: list[str] = []
    monkeypatch.setattr('src.pomodoro.time.time', clock.time)
    timer = PomodoroTimer(work_mins=1, short_break_mins=5, on_status=emitted.append)

    timer.start()
    emitted.clear()
    clock.now += 61

    first = timer.status()
    second = timer.status()

    assert first['status'] == 'short_break'
    assert second['status'] == 'short_break'
    assert emitted.count('✅ Pomodoro #1 complete! Time for a break.') == 1


def test_status_expiry_from_break_returns_idle(monkeypatch) -> None:
    clock = FakeClock(now=8_000.0)
    emitted: list[str] = []
    monkeypatch.setattr('src.pomodoro.time.time', clock.time)
    timer = PomodoroTimer(on_status=emitted.append)
    timer._state = PomodoroState.SHORT_BREAK
    timer._start_time = clock.time() - 301
    timer._duration_secs = 300
    timer._completed = 3
    timer._total_focus_secs = 900

    status = timer.status()
    assert status == {'status': 'idle', 'completed_pomodoros': 3, 'total_focus_mins': 15.0}
    assert emitted[-1] == "☕ Break's over! Ready for the next pomodoro."


def test_stop_and_skip_when_idle_are_noops() -> None:
    timer = PomodoroTimer()
    assert timer.stop() == {'status': 'idle', 'message': 'No timer running.'}
    assert timer.skip() == {'status': 'idle', 'message': 'No timer running.'}
