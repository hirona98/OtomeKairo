from __future__ import annotations

import uuid
from typing import Any

from otomekairo.memory.utils import visual_daily_digest_id, visual_daily_group_id


# 視覚記憶の閉じた参照フィールドだけを書き換え、本文や意味上のtopicは保つ。
VISUAL_REFERENCE_FIELDS = {
    "visual_observation_id", "representative_visual_observation_id",
    "digest_id", "daily_digest_id", "duplicate_group_id", "support_group_refs",
    "promoted_memory_unit_ids", "target_memory_unit_id",
    "candidate_groups", "evidence_groups", "memory_unit_ids",
}


def build_visual_clone_reference_map(
    *, observations: list[dict[str, Any]], digests: list[dict[str, Any]], target_memory_set_id: str,
) -> dict[str, str]:
    references = {
        record["visual_observation_id"]: f"visual_observation:{uuid.uuid4().hex}"
        for record in observations
    }
    for digest in digests:
        references[digest["digest_id"]] = visual_daily_digest_id(
            memory_set_id=target_memory_set_id, local_date=digest["local_date"])
        for index, group in enumerate(digest["group_summaries"], start=1):
            references[group["duplicate_group_id"]] = visual_daily_group_id(
                memory_set_id=target_memory_set_id, local_date=digest["local_date"], index=index)
    return references


def clone_visual_references(value: Any, references: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [clone_visual_references(item, references) for item in value]
    if isinstance(value, dict):
        return {
            key: _clone_reference(item, references) if key in VISUAL_REFERENCE_FIELDS
            else clone_visual_references(item, references)
            for key, item in value.items()
        }
    return value


def _clone_reference(value: Any, references: dict[str, str]) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [_clone_reference(item, references) for item in value]
    if isinstance(value, dict):
        return {key: _clone_reference(item, references) for key, item in value.items()}
    if not isinstance(value, str) or value not in references:
        raise ValueError("視覚記憶の複製元参照がありません。")
    return references[value]
