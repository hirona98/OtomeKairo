from __future__ import annotations

from datetime import datetime
from typing import Any


REFERENCE_SOURCES = {
    "memory_unit": ("memory_units", "memory_unit_id", "evidence_event_ids"),
    "episode": ("episodes", "episode_id", "linked_event_ids"),
    "entity_registry": ("entity_registry", "entity_ref", "evidence_event_ids"),
}


def merge_event_reference_integrity(*values: dict[str, Any] | None) -> dict[str, Any] | None:
    items = [value for value in values if value is not None]
    if not items:
        return None
    for item in items:
        if (not isinstance(item, dict) or set(item) != {"removed_event_ids", "repaired_at"}
                or not isinstance(item["removed_event_ids"], list)
                or not item["removed_event_ids"]
                or any(not isinstance(value, str) or not value for value in item["removed_event_ids"])
                or not isinstance(item["repaired_at"], str)):
            raise ValueError("event_reference_integrity が不正です。")
    return {
        "removed_event_ids": list(dict.fromkeys(value for item in items for value in item["removed_event_ids"])),
        "repaired_at": max(items, key=lambda item: datetime.fromisoformat(item["repaired_at"]))["repaired_at"],
    }


def preserve_event_reference_integrity(
    record: dict[str, Any], *, reference_key: str, integrity: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = merge_event_reference_integrity(record.get("event_reference_integrity"), integrity)
    if merged is None:
        return record
    removed = set(merged["removed_event_ids"])
    return {
        **record,
        reference_key: [value for value in record.get(reference_key, []) if value not in removed],
        "event_reference_integrity": merged,
    }


def event_reference_context(record: dict[str, Any]) -> dict[str, Any]:
    integrity = merge_event_reference_integrity(record.get("event_reference_integrity"))
    if integrity is None:
        return {}
    return {"event_reference_integrity": {
        "missing_event_count": len(integrity["removed_event_ids"]),
        "repaired_at": integrity["repaired_at"],
    }}


def apply_event_reference_repairs(value: Any, repaired_sources: list[dict[str, Any]]) -> None:
    """取得済みの参照配列をDBの修復結果へ揃える。意味や本文は変更しない。"""
    by_source = {(item["source_kind"], item["source_id"]): item for item in repaired_sources}

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for kind, (_, id_key, reference_key) in REFERENCE_SOURCES.items():
                source_id = item.get(id_key)
                repair = by_source.get((kind, source_id)) if isinstance(source_id, str) else None
                if repair is not None:
                    item.update(preserve_event_reference_integrity(
                        item, reference_key=reference_key, integrity=repair["event_reference_integrity"],
                    ))
            for endpoint in ("source", "target"):
                source_id = item.get(f"{endpoint}_memory_unit_id")
                repair = by_source.get(("memory_unit", source_id)) if isinstance(source_id, str) else None
                claim = item.get(f"{endpoint}_claim")
                if repair is not None and isinstance(claim, dict):
                    claim.update(event_reference_context(repair))
            for child in list(item.values()):
                visit(child)

    visit(value)
