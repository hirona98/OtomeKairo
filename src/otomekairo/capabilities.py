from __future__ import annotations

from copy import deepcopy
from typing import Any


# server が正本として持つ capability manifest。
CAPABILITY_MANIFESTS: dict[str, dict[str, Any]] = {
    "vision.capture": {
        "id": "vision.capture",
        "version": "1",
        "kind": "observation",
        "decision_description": "現在の画面状態を観測する",
        "when_to_use": [
            "ユーザーが画面内容について質問した",
            "判断に現在の画面状態が必要",
        ],
        "do_not_use_when": [
            "ユーザーが画面観測を拒否している",
            "現在の判断に画面情報が不要",
        ],
        "required_permissions": ["observe_desktop"],
        "timeout_ms": 5000,
        "risk_level": "low",
    },
}


def capability_manifests() -> dict[str, dict[str, Any]]:
    # 呼び出し側が静的定義を変更しないよう複製する。
    return deepcopy(CAPABILITY_MANIFESTS)
