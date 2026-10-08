"""Mechanical clock guarantees; real LLM conversations remain a manual smoke."""
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_virtual_time_conversation.py"
spec = importlib.util.spec_from_file_location("virtual_time_verification", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_clock_requires_offset_and_refuses_time_reversal():
    with pytest.raises(ValueError):
        module.VirtualClock(datetime(2026, 10, 3))
    start = datetime.fromisoformat("2026-10-03T00:00:00+00:00")
    clock = module.VirtualClock(start)
    assert clock.now().isoformat() == "2026-10-03T09:00:00+09:00"
    target = start + timedelta(days=30)
    assert clock.advance_to(target) == target
    with pytest.raises(ValueError):
        clock.advance_to(start)
    assert clock.now() == target


def test_application_aliases_use_virtual_time_but_timeout_clock_stays_real():
    # A new process is essential: pytest imports application modules during
    # collection, unlike the dedicated verification launcher.
    code = '''
import sys
from datetime import datetime, timedelta
import os
import time
sys.path.insert(0, 'scripts')
from run_virtual_time_conversation import VirtualClock, installed_clock
os.environ['TZ'] = 'UTC'
time.tzset()
clock = VirtualClock(datetime.fromisoformat('2026-10-03T09:00:00+09:00'))
with installed_clock(clock):
    from otomekairo.memory.utils import now_iso, local_datetime
    from otomekairo.service.input.mixin import local_now as input_now
    from otomekairo.service.visual_daily import local_now as daily_now
    from otomekairo.audio.runtime import local_now as audio_now
    from otomekairo.store.config import now_iso as config_now
    from otomekairo.memory.reflection.consolidator import now_iso as reflection_now
    before = time.monotonic()
    clock.advance_to(clock.now() + timedelta(days=30))
    values = [now_iso(), input_now().isoformat(), daily_now().isoformat(), audio_now().isoformat(), config_now(), reflection_now()]
    assert set(values) == {'2026-11-02T09:00:00+09:00'}
    assert local_datetime(now_iso()).hour == 9
    assert 0 <= time.monotonic() - before < 5
assert os.environ['TZ'] == 'UTC'
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=SCRIPT.parent.parent,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_installation_after_application_import_fails_explicitly():
    import otomekairo.service.input.mixin
    clock = module.VirtualClock(datetime.fromisoformat("2026-10-03T09:00:00+09:00"))
    # The application is already loaded; accepting this would
    # silently leave direct local_now imports on a different clock.
    with pytest.raises(module.VerificationError):
        with module.installed_clock(clock):
            pass


def test_every_forward_move_checks_registered_deadlines():
    from unittest.mock import Mock
    start = datetime.fromisoformat('2026-10-03T09:00:00+09:00')
    due = start + timedelta(days=1)
    runner = module.ConversationVerification.__new__(module.ConversationVerification)
    runner.clock = module.VirtualClock(start)
    waiting = {'run_id': 'autonomous_run:timer', 'status': 'waiting_timer', 'next_run_at': due.isoformat()}
    runner.future_timers = [{'label': 'tomorrow', 'before': dict(waiting), 'due': due}]
    runner.runs = lambda: [waiting]
    instants = []
    def advance(instant, label):
        instants.append(instant)
        runner.clock.advance_to(instant)
        if instant == due:
            waiting['status'] = 'completed'
    runner._move_instant = advance
    runner.wait = lambda predicate, label: predicate() or pytest.fail(label)
    runner.drain = Mock()
    runner.save = Mock()
    runner.proofs = []
    runner.verify_timer_delivery = Mock()
    target = start + timedelta(days=2)
    runner.move(target, 'hourly-or-daily-forward')
    assert instants == [due - timedelta(seconds=1), due, target]
    assert runner.future_timers[0]['verified']
    runner.verify_timer_delivery.assert_called_once()
    runner.move(target + timedelta(hours=1), 'next-forward')
    assert runner.verify_timer_delivery.call_count == 1


def test_long_timer_survives_more_than_two_hundred_later_runs():
    from types import SimpleNamespace
    from unittest.mock import Mock
    start = datetime.fromisoformat('2026-10-03T09:00:00+09:00')
    due = start + timedelta(days=365)
    waiting = {'run_id': 'autonomous_run:year', 'status': 'waiting_timer', 'next_run_at': due.isoformat()}
    records = [{'run_id': f'autonomous_run:later-{i}', 'status': 'completed'} for i in range(250)] + [waiting]
    def read_runs(*, memory_set_id, limit):
        assert memory_set_id == 'memory:test'
        return records if limit is None else records[:limit]
    runner = module.ConversationVerification.__new__(module.ConversationVerification)
    runner.clock = module.VirtualClock(start)
    runner.memory_set = 'memory:test'
    runner.service = SimpleNamespace(store=SimpleNamespace(list_autonomous_runs=read_runs))
    runner.future_timers = [{'label': 'year', 'before': dict(waiting), 'due': due}]
    def advance(instant, label):
        runner.clock.advance_to(instant)
        if instant == due:
            waiting['status'] = 'completed'
    runner._move_instant = advance
    runner.wait = lambda predicate, label: predicate() or pytest.fail(label)
    runner.drain = Mock()
    runner.save = Mock()
    runner.proofs = []
    runner.verify_timer_delivery = Mock()
    runner.move(due + timedelta(days=1), 'past-year')
    assert runner.future_timers[0]['verified']
    runner.verify_timer_delivery.assert_called_once()
