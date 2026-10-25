from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from otomekairo.http_server import OtomeKairoHandler
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import ServiceError
from otomekairo.store.file_store import FileStore


STAMP = "2026-10-25T10:00:00+09:00"


@pytest.fixture
def service(tmp_path):
    # 設定境界だけを実DBで検証し、音声モデルやbackground workerは起動しない。
    instance = OtomeKairoService.__new__(OtomeKairoService)
    instance._config_update_lock = threading.RLock()
    instance._runtime_state_lock = threading.RLock()
    instance.store = FileStore(tmp_path)
    instance._now_iso = lambda: STAMP
    return instance


@pytest.mark.parametrize("second_operation", ["reissue", "settings", "connect"])
def test_overlapping_updates_keep_both_changes(service, monkeypatch, second_operation):
    token = service.acquire_console_access_token()["console_access_token"]
    connecting = threading.Event()
    release = threading.Event()
    attempted = threading.Event()
    completed = threading.Event()

    def held_time():
        if threading.current_thread().name == "connecting-client":
            connecting.set()
            assert release.wait(3)
        return STAMP

    monkeypatch.setattr(service, "_now_iso", held_time)

    def first():
        threading.current_thread().name = "connecting-client"
        return service.connect_console_client(token, "console:first")

    def second():
        attempted.set()
        if second_operation == "reissue":
            result = service.reissue_console_access_token(token)
        elif second_operation == "settings":
            result = service.patch_current(token, {"thinking_speech_level": 3})
        else:
            result = service.connect_console_client(token, "console:second")
        completed.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        assert connecting.wait(3)
        b = pool.submit(second)
        assert attempted.wait(3)
        try:
            assert not completed.wait(0.05)
        finally:
            release.set()
        a.result(timeout=3)
        result = b.result(timeout=3)
    state = service.store.read_state()
    assert "console:first" in state["console_client_settings"]
    if second_operation == "reissue":
        assert state["console_access_token"] == result["console_access_token"] != token
        with pytest.raises(ServiceError) as caught:
            service.get_config(token)
        assert caught.value.error_code == "invalid_token"
    elif second_operation == "settings":
        assert state["thinking_speech_level"] == 3
    else:
        assert "console:second" in state["console_client_settings"]


def test_waiting_update_authenticates_after_token_reissue(service, monkeypatch):
    token = service.acquire_console_access_token()["console_access_token"]
    saving = threading.Event()
    release = threading.Event()
    attempted = threading.Event()
    original_write = service.store.write_state

    def held_write(state):
        saving.set()
        assert release.wait(3)
        original_write(state)

    monkeypatch.setattr(service.store, "write_state", held_write)

    def connect():
        attempted.set()
        return service.connect_console_client(token, "console:stale")

    with ThreadPoolExecutor(max_workers=2) as pool:
        issuing = pool.submit(service.reissue_console_access_token, token)
        assert saving.wait(3)
        connecting = pool.submit(connect)
        assert attempted.wait(3)
        release.set()
        replacement = issuing.result(timeout=3)["console_access_token"]
        with pytest.raises(ServiceError) as caught:
            connecting.result(timeout=3)
    assert caught.value.error_code == "invalid_token"
    state = service.store.read_state()
    assert state["console_access_token"] == replacement
    assert "console:stale" not in state["console_client_settings"]


def test_web_ui_and_bootstrap_share_one_initial_token(service):
    handler = OtomeKairoHandler.__new__(OtomeKairoHandler)
    handler.server = SimpleNamespace(service=service)
    barrier = threading.Barrier(8)

    def acquire(index):
        barrier.wait(timeout=3)
        if index % 2:
            return handler._web_ui_console_token()
        return service.acquire_console_access_token()["console_access_token"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(acquire, range(8)))
    assert len(set(tokens)) == 1
    assert service.store.read_state()["console_access_token"] == tokens[0]


def test_validation_failure_releases_the_update_lock_without_saving(service):
    token = service.acquire_console_access_token()["console_access_token"]
    before = service.store.read_state()
    with pytest.raises(ServiceError):
        service.patch_current(token, {"thinking_speech_level": -1})
    assert service.store.read_state() == before
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(service.connect_console_client, token, "console:valid").result(timeout=3)
    assert "console:valid" in service.store.read_state()["console_client_settings"]
