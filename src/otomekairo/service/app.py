from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from otomekairo.event_stream import EventStreamRegistry
from otomekairo.evidence import EvidenceResolver
from otomekairo.llm.client import LLMClient
from otomekairo.log_stream import LogStreamRegistry
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.recall.builder import RecallBuilder
from otomekairo.service.capability import ServiceCapabilityMixin
from otomekairo.service.common import ServiceError, configure_debug_log_stream_sink, debug_log
from otomekairo.service.config.mixin import ServiceConfigMixin
from otomekairo.service.memory import ServiceMemoryMixin
from otomekairo.service.input.mixin import ServiceInputMixin
from otomekairo.service.spontaneous.mixin import ServiceSpontaneousMixin
from otomekairo.store.file_store import FileStore


# サービス
class OtomeKairoService(
    ServiceCapabilityMixin,
    ServiceSpontaneousMixin,
    ServiceConfigMixin,
    ServiceInputMixin,
    ServiceMemoryMixin,
):
    def __init__(self, root_dir: Path) -> None:
        # 依存関係
        self._log_stream_registry = LogStreamRegistry()
        configure_debug_log_stream_sink(self._append_debug_log_stream_record)
        debug_log("Service", f"initializing root_dir={root_dir}", level="DEBUG")
        self.store = FileStore(root_dir)
        self.llm = LLMClient()
        self.recall = RecallBuilder(store=self.store, llm=self.llm)
        self.evidence = EvidenceResolver(store=self.store)
        self.memory = MemoryConsolidator(store=self.store, llm=self.llm)
        self._runtime_state_lock = threading.RLock()
        self._wake_execution_lock = threading.Lock()
        self._pending_intent_candidates: list[dict[str, Any]] = []
        self._wake_runtime_state: dict[str, Any] = {
            "last_wake_at": None,
            "last_spontaneous_at": None,
            "cooldown_until": None,
            "initial_delay_until": None,
            "retry_after": None,
            "reply_history_by_dedupe": {},
            "active_user_response_cycle_count": 0,
        }
        self._wake_observation_runtime_state: dict[str, dict[str, Any]] = {}
        self._background_wake_stop_event: threading.Event | None = None
        self._background_wake_thread: threading.Thread | None = None
        self._background_memory_postprocess_stop_event: threading.Event | None = None
        self._background_memory_postprocess_thread: threading.Thread | None = None
        self._memory_postprocess_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._memory_postprocess_runtime_state: dict[str, Any] = {
            "current_cycle_id": None,
        }
        self._event_stream_registry = EventStreamRegistry()
        self._capability_request_lock = threading.RLock()
        self._pending_capability_requests: dict[str, dict[str, Any]] = {}
        self._capability_runtime_state: dict[str, dict[str, Any]] = {}
        self._stream_event_lock = threading.Lock()
        self._next_stream_event_value = 1
        self.recover_capability_runtime_state_after_startup()
        debug_log("Service", "initialized")

    def _append_debug_log_stream_record(self, record: dict[str, Any]) -> None:
        self._log_stream_registry.append_logs([record])

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
        debug_log("Wake", f"background scheduler started thread={thread.name}", level="DEBUG")

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
