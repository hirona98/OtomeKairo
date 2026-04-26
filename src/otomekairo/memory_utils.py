from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any


# JSON処理
def to_json_string(value: Any) -> str:
    # 直列化
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def stable_json(value: Any) -> str:
    # ハッシュ
    return hashlib.sha256(
        to_json_string(value).encode("utf-8")
    ).hexdigest()


# 時間
def local_now() -> datetime:
    # OtomeKairo が生活するローカルタイムゾーンの現在時刻を正本にする。
    return datetime.now().astimezone()


def now_iso() -> str:
    # タイムスタンプ
    return local_now().isoformat()


def parse_iso(value: str) -> datetime:
    # duration 計算用に UTC へ正規化する。
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def local_datetime(value: str) -> datetime:
    # API/inspection/LLM 表示用にローカルタイムゾーンへそろえる。
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def display_local_iso(value: str) -> str:
    # API/inspection では offset 付きローカル timestamp を返す。
    return local_datetime(value).isoformat()


def llm_local_time_text(value: str) -> str:
    # LLM には ISO timestamp ではなく生活文脈の自然文を渡す。
    local_time = local_datetime(value)
    weekdays = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")
    timezone_label = _local_timezone_label(local_time)
    return (
        f"現在時刻: {local_time.year}年{local_time.month}月{local_time.day}日 "
        f"{weekdays[local_time.weekday()]} {local_time.hour}時{local_time.minute:02d}分\n"
        f"生活タイムゾーン: {timezone_label}"
    )


def _local_timezone_label(value: datetime) -> str:
    # 日本時間は判断文脈で読みやすい名称にする。
    tz_name = value.tzname()
    if tz_name in {"JST", "UTC+09:00", "+09"}:
        return "日本時間"
    return tz_name or "ローカル時刻"


def localize_timestamp_fields(value: Any) -> Any:
    # 表示面だけを offset 付きローカル timestamp へ変換する。
    if isinstance(value, list):
        return [localize_timestamp_fields(item) for item in value]
    if not isinstance(value, dict):
        return value

    localized: dict[str, Any] = {}
    for key, item in value.items():
        if _is_timestamp_display_key(key) and isinstance(item, str) and item.strip():
            localized[key] = display_local_iso(item)
            continue
        localized[key] = localize_timestamp_fields(item)
    return localized


def _is_timestamp_display_key(key: Any) -> bool:
    # `_at` と一部の例外キーを表示用 timestamp とみなす。
    if not isinstance(key, str):
        return False
    return (
        key.endswith("_at")
        or key.endswith("_iso")
        or key in {"current_time", "ts", "valid_from", "valid_to", "not_before", "cooldown_until"}
    )


def hours_since(older_iso: str, newer_iso: str) -> float:
    # 差分
    older = parse_iso(older_iso)
    newer = parse_iso(newer_iso)
    return max(0.0, (newer - older).total_seconds() / 3600.0)


def days_since(older_iso: str | None, newer_iso: str) -> int:
    # 確認
    if not isinstance(older_iso, str) or not older_iso:
        return 0

    # 差分
    older = parse_iso(older_iso)
    newer = parse_iso(newer_iso)
    delta = newer - older
    if delta <= timedelta(0):
        return 0
    return delta.days


def timestamp_sort_key(value: Any) -> float:
    # 解析
    if not isinstance(value, str) or not value:
        return float("inf")
    return parse_iso(value).timestamp()


# スコア計算
def clamp_score(value: Any) -> float:
    # 正規化
    if not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(float(value), 1.0))


# テキスト
def normalized_text_list(values: list[Any], *, limit: int) -> list[str]:
    # 正規化
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped and stripped not in normalized:
            normalized.append(stripped)
        if len(normalized) >= limit:
            break
    return normalized


def optional_text(value: Any) -> str | None:
    # 正規化
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def display_scope_key(scope_key: str) -> str:
    # トピック接頭辞
    if scope_key.startswith("topic:"):
        return scope_key.split(":", 1)[1]

    # 結果
    return scope_key


NON_SEMANTIC_QUALIFIER_KEYS = {
    "allow_parallel",
    "negates_previous",
    "replace_prior",
    "source",
    "summary_scope",
    "source_memory_types",
}


def build_memory_unit_semantic_text(
    record: dict[str, Any],
    *,
    exclude_qualifier_keys: set[str] | None = None,
) -> str:
    # 比較と索引用の意味テキストを組み立てる。
    parts: list[str] = []

    summary_text = optional_text(record.get("summary_text"))
    if summary_text is not None:
        parts.append(summary_text)

    predicate = optional_text(record.get("predicate"))
    if predicate is not None:
        parts.append(f"predicate:{predicate}")

    object_text = _semantic_text(record.get("object_ref_or_value"))
    if object_text is not None:
        parts.append(f"object:{object_text}")

    qualifiers = semantic_qualifiers(
        record.get("qualifiers"),
        exclude_keys=exclude_qualifier_keys,
    )
    for key in sorted(qualifiers):
        value_text = _semantic_text(qualifiers.get(key))
        if value_text is None:
            continue
        parts.append(f"qualifier:{key}={value_text}")

    return "\n".join(parts)


def semantic_qualifiers(
    qualifiers: Any,
    *,
    exclude_keys: set[str] | None = None,
) -> dict[str, Any]:
    # 制御用や集計用の qualifier を落として、意味差分だけを残す。
    if not isinstance(qualifiers, dict):
        return {}

    semantic_items: dict[str, Any] = {}
    for key, value in qualifiers.items():
        if not isinstance(key, str) or not key:
            continue
        if key in NON_SEMANTIC_QUALIFIER_KEYS:
            continue
        if exclude_keys and key in exclude_keys:
            continue
        if key.endswith("_count"):
            continue
        value_text = _semantic_text(value)
        if value_text is None:
            continue
        semantic_items[key] = value

    return semantic_items


def _semantic_text(value: Any) -> str | None:
    # ベクトル化に使える短いテキストへ正規化する。
    if value is None:
        return None
    if isinstance(value, str):
        return optional_text(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return to_json_string(value)
    return optional_text(str(value))


# コレクション
def merged_event_ids(existing_event_ids: list[Any], new_event_ids: list[str]) -> list[str]:
    # 統合
    merged: list[str] = []
    for event_id in existing_event_ids + new_event_ids:
        if isinstance(event_id, str) and event_id not in merged:
            merged.append(event_id)
    return merged


def merged_cycle_ids(existing_cycle_ids: list[Any], new_cycle_ids: list[str]) -> list[str]:
    # 統合
    merged: list[str] = []
    for cycle_id in existing_cycle_ids + new_cycle_ids:
        if isinstance(cycle_id, str) and cycle_id not in merged:
            merged.append(cycle_id)
    return merged


def unique_memory_unit_ids(actions: list[dict[str, Any]]) -> list[str]:
    # 収集
    unique_ids: list[str] = []
    for action in actions:
        memory_unit_id = action.get("memory_unit_id")
        if not isinstance(memory_unit_id, str):
            continue
        if memory_unit_id in unique_ids:
            continue
        unique_ids.append(memory_unit_id)

    # 結果
    return unique_ids


def action_counts(actions: list[dict[str, Any]]) -> dict[str, int]:
    # 件数
    counts = Counter(action["operation"] for action in actions)

    # 結果
    return dict(counts)
