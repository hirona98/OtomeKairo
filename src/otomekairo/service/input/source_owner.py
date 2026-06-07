from __future__ import annotations

from typing import Any


# source_owner は観測対象の所有者境界を LLM へ渡すための短い構造タグ。
SELF_VISUAL_SOURCE_KINDS = {"camera"}
USER_ENVIRONMENT_VISUAL_SOURCE_KINDS = {"desktop", "virtual"}


def visual_source_owner(source_kind: Any) -> str | None:
    if not isinstance(source_kind, str):
        return None
    normalized = source_kind.strip()
    if normalized in SELF_VISUAL_SOURCE_KINDS:
        return "self"
    if normalized in USER_ENVIRONMENT_VISUAL_SOURCE_KINDS:
        return "user_environment"
    return None


def first_visual_source_owner(*payloads: dict[str, Any] | None) -> str | None:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        owner = payload.get("source_owner")
        if isinstance(owner, str) and owner.strip():
            return owner.strip()
        owner = visual_source_owner(payload.get("source_kind"))
        if owner is not None:
            return owner
    return None
