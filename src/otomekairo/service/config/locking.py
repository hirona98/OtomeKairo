from functools import wraps
from typing import Any, Callable


def serialize_config_update(method: Callable[..., Any]) -> Callable[..., Any]:
    """認証・最新設定の取得から保存と派生状態の更新までを直列化する。"""
    @wraps(method)
    def serialized(self, *args, **kwargs):
        # runtime/audioのlockより先に取得する。設定更新同士の委譲には再入を許す。
        with self._config_update_lock:
            return method(self, *args, **kwargs)

    return serialized
