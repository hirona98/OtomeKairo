from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from otomekairo.event_stream import EventStreamRegistry
from otomekairo.llm import LLMClient
from otomekairo.log_stream import LogStreamRegistry
from otomekairo.memory import MemoryConsolidator
from otomekairo.recall import RecallBuilder
from otomekairo.service_common import ServiceError, debug_log
from otomekairo.service_config import ServiceConfigMixin
from otomekairo.service_memory import ServiceMemoryMixin
from otomekairo.service_input import ServiceInputMixin
from otomekairo.service_spontaneous import ServiceSpontaneousMixin
from otomekairo.store import FileStore


# サービス
class OtomeKairoService(
    ServiceSpontaneousMixin,
    ServiceConfigMixin,
    ServiceInputMixin,
    ServiceMemoryMixin,
):
    def __init__(self, root_dir: Path) -> None:
        # 依存関係
        debug_log("Service", f"initializing root_dir={root_dir}")
        self.store = FileStore(root_dir)
        self.llm = LLMClient()
        self.recall = RecallBuilder(store=self.store, llm=self.llm)
        self.memory = MemoryConsolidator(store=self.store, llm=self.llm)
        self._runtime_state_lock = threading.RLock()
        self._wake_execution_lock = threading.Lock()
        self._desktop_watch_execution_lock = threading.Lock()
        self._pending_intent_candidates: list[dict[str, Any]] = []
        self._wake_runtime_state: dict[str, Any] = {
            "last_wake_at": None,
            "last_spontaneous_at": None,
            "cooldown_until": None,
            "reply_history_by_dedupe": {},
        }
        self._desktop_watch_runtime_state: dict[str, Any] = {
            "last_watch_at": None,
        }
        self._background_wake_stop_event: threading.Event | None = None
        self._background_wake_thread: threading.Thread | None = None
        self._background_desktop_watch_stop_event: threading.Event | None = None
        self._background_desktop_watch_thread: threading.Thread | None = None
        self._background_memory_postprocess_stop_event: threading.Event | None = None
        self._background_memory_postprocess_thread: threading.Thread | None = None
        self._memory_postprocess_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._memory_postprocess_runtime_state: dict[str, Any] = {
            "current_cycle_id": None,
        }
        self._event_stream_registry = EventStreamRegistry()
        self._log_stream_registry = LogStreamRegistry()
        self._vision_capture_lock = threading.RLock()
        self._pending_vision_capture_requests: dict[str, dict[str, Any]] = {}
        self._stream_event_lock = threading.Lock()
        self._next_stream_event_value = 1
        debug_log("Service", "initialized")

    def start_background_wake_scheduler(self) -> None:
        # 既存
        with self._runtime_state_lock:
            if self._background_wake_thread is not None and self._background_wake_thread.is_alive():
                debug_log("Wake", "background scheduler already running")
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._background_wake_loop,
                args=(stop_event,),
                name="otomekairo-background-wake",
                daemon=True,
            )
            self._background_wake_stop_event = stop_event
            self._background_wake_thread = thread

        # 開始
        thread.start()
        debug_log("Wake", f"background scheduler started thread={thread.name}")

    def stop_background_wake_scheduler(self) -> None:
        # スナップショット
        with self._runtime_state_lock:
            stop_event = self._background_wake_stop_event
            thread = self._background_wake_thread
            self._background_wake_stop_event = None
            self._background_wake_thread = None

        # 停止
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        debug_log("Wake", "background scheduler stopped")

    def start_background_desktop_watch(self) -> None:
        # 既存
        with self._runtime_state_lock:
            if self._background_desktop_watch_thread is not None and self._background_desktop_watch_thread.is_alive():
                debug_log("DesktopWatch", "background scheduler already running")
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._background_desktop_watch_loop,
                args=(stop_event,),
                name="otomekairo-background-desktop-watch",
                daemon=True,
            )
            self._background_desktop_watch_stop_event = stop_event
            self._background_desktop_watch_thread = thread

        # 開始
        thread.start()
        debug_log("DesktopWatch", f"background scheduler started thread={thread.name}")

    def stop_background_desktop_watch(self) -> None:
        # スナップショット
        with self._runtime_state_lock:
            stop_event = self._background_desktop_watch_stop_event
            thread = self._background_desktop_watch_thread
            self._background_desktop_watch_stop_event = None
            self._background_desktop_watch_thread = None

        # 停止
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        debug_log("DesktopWatch", "background scheduler stopped")
