from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.memory.utils import local_datetime


def source_event_ids(record: dict[str, Any], key: str) -> list[str]:
    values = record.get(key, [])
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("内省要約の根拠イベント参照は空でない文字列の配列である必要があります。")
    return list(dict.fromkeys(values))


def summary_event_ids(evidence_pack: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        event_id
        for section in ("episodes", "memory_units")
        for item in evidence_pack[section]
        for event_id in item["source_event_ids"]
    ))


def build_summary_source_pack(
    *, evidence_packs: list[dict[str, Any]], events_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """選択済み項目の出典だけを共有し、本文と話者の対応を保つ。"""
    event_refs: dict[str, str] = {}
    source_events: dict[str, Any] = {}
    scopes = deepcopy(evidence_packs)
    for scope in scopes:
        for section in ("episodes", "memory_units"):
            for item in scope[section]:
                refs = []
                for event_id in dict.fromkeys(item.pop("source_event_ids")):
                    if event_id not in event_refs:
                        event = events_by_id[event_id]
                        if event["event_id"] != event_id:
                            raise ValueError("内省要約の根拠イベントIDが一致しません。")
                        ref = f"source_event:{len(source_events)}"
                        event_refs[event_id] = ref
                        source = {
                            key: deepcopy(event[key]) for key in (
                                "kind", "role", "speaker_ref", "participant_refs", "interaction_ref",
                                "source_kind", "terminal_status", "text", "objective_summary",
                                "history_summary", "observed_result_summaries",
                            ) if key in event
                        }
                        formed_at = event.get("created_at")
                        source["formed_time_label"] = None
                        if formed_at is not None:
                            time = local_datetime(formed_at)
                            source["formed_time_label"] = (
                                f"{time.year}年{time.month}月{time.day}日 {time.hour}時{time.minute:02d}分{time.second:02d}秒"
                            )
                        source_events[ref] = source
                    refs.append(event_refs[event_id])
                item["source_event_refs"] = refs
    return {"scopes": scopes, "source_events": source_events}
