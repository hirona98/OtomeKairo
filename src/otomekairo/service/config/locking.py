from functools import wraps
from typing import Any, Callable

from otomekairo.service.common import ServiceError


def serialize_config_update(method: Callable[..., Any]) -> Callable[..., Any]:
    """認証・最新設定の取得から保存と派生状態の更新までを直列化する。"""
    @wraps(method)
    def serialized(self, *args, **kwargs):
        # runtime/audioのlockより先に取得する。設定更新同士の委譲には再入を許す。
        with self._config_update_lock:
            return method(self, *args, **kwargs)

    return serialized


def guard_memory_set_work(method: Callable[..., Any]) -> Callable[..., Any]:
    """集合の使用開始と削除を直列化し、処理中は集合を保持する。"""
    @wraps(method)
    def guarded(self, *, memory_set_id: str, **kwargs):
        with self._config_update_lock:
            if memory_set_id not in self.store.read_state()["memory_sets"]:
                raise ServiceError(404, "memory_set_not_found", "The requested memory_set_id does not exist.")
            counts = self._memory_set_work_counts
            counts[memory_set_id] = counts.get(memory_set_id, 0) + 1
        try:
            return method(self, memory_set_id=memory_set_id, **kwargs)
        finally:
            with self._config_update_lock:
                counts[memory_set_id] -= 1
                if counts[memory_set_id] == 0:
                    del counts[memory_set_id]

    return guarded
