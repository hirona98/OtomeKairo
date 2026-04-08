from __future__ import annotations

from typing import Any


# 定数
EVENT_EVIDENCE_LIMIT = 3
EVENT_EVIDENCE_INTENTS = {
    "commitment_check",
    "fact_query",
    "meta_relationship",
    "reminisce",
}


# イベント根拠Mixin
class RecallEventEvidenceMixin:
    def _build_event_evidence(
        self,
        *,
        memory_set_id: str,
        primary_intent: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        # 確認
        if not self._should_load_event_evidence(
            primary_intent=primary_intent,
            recall_hint=recall_hint,
            sections=sections,
        ):
            return []

        # 選択済みID群
        selected_event_ids = self._select_event_evidence_ids(
            primary_intent=primary_intent,
            sections=sections,
        )
        if not selected_event_ids:
            return []

        # 読み込み
        records = self.store.load_events_for_evidence(
            memory_set_id=memory_set_id,
            event_ids=selected_event_ids,
            limit=EVENT_EVIDENCE_LIMIT,
        )

        # 結果
        return [self._to_event_evidence_item(record) for record in records]

    def _should_load_event_evidence(
        self,
        *,
        primary_intent: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> bool:
        # source確認
        if not self._has_event_evidence_sources(primary_intent=primary_intent, sections=sections):
            return False

        # intent確認
        if primary_intent in EVENT_EVIDENCE_INTENTS:
            return True

        # 時刻確認
        return recall_hint.get("time_reference") == "past"

    def _has_event_evidence_sources(
        self,
        *,
        primary_intent: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> bool:
        # 走査
        for section_name in self._event_evidence_section_priority(primary_intent):
            for item in sections.get(section_name, []):
                if self._prioritized_event_ids_for_item(item):
                    return True
        return False

    def _select_event_evidence_ids(
        self,
        *,
        primary_intent: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        # source群
        sources = self._event_evidence_sources(
            primary_intent=primary_intent,
            sections=sections,
        )
        if not sources:
            return []

        # ラウンドロビン
        selected: list[str] = []
        seen: set[str] = set()
        offset = 0
        while len(selected) < EVENT_EVIDENCE_LIMIT:
            added_in_round = False
            for event_ids in sources:
                if offset >= len(event_ids):
                    continue
                event_id = event_ids[offset]
                if event_id in seen:
                    continue
                selected.append(event_id)
                seen.add(event_id)
                added_in_round = True
                if len(selected) >= EVENT_EVIDENCE_LIMIT:
                    break
            if not added_in_round:
                break
            offset += 1

        # 結果
        return selected

    def _event_evidence_sources(
        self,
        *,
        primary_intent: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[list[str]]:
        # 状態
        sources: list[list[str]] = []

        # 収集
        for section_name in self._event_evidence_section_priority(primary_intent):
            for item in sections.get(section_name, []):
                prioritized_event_ids = self._prioritized_event_ids_for_item(item)
                if not prioritized_event_ids:
                    continue
                sources.append(prioritized_event_ids)
        return sources

    def _event_evidence_section_priority(self, primary_intent: str) -> list[str]:
        # 基底順序
        ordered = ["episodic_evidence"]
        recall_hint = {
            "primary_intent": primary_intent,
            "secondary_intents": [],
        }
        for section_name in self._section_priority(recall_hint):
            if section_name in {"episodic_evidence", "conflicts"}:
                continue
            ordered.append(section_name)
        return ordered

    def _prioritized_event_ids_for_item(self, item: dict[str, Any]) -> list[str]:
        # イベントID群
        if item["source_kind"] == "episode_digest":
            event_ids = item.get("linked_event_ids", [])
        else:
            event_ids = item.get("evidence_event_ids", [])
        return self._prioritized_event_ids(event_ids)

    def _prioritized_event_ids(self, event_ids: list[Any]) -> list[str]:
        # 収集
        ordered: list[str] = []
        seen: set[str] = set()
        preferred_indexes = (1, 0, 2)
        for index in preferred_indexes:
            if index >= len(event_ids):
                continue
            value = event_ids[index]
            if not isinstance(value, str) or value in seen:
                continue
            ordered.append(value)
            seen.add(value)
        for value in event_ids:
            if not isinstance(value, str) or value in seen:
                continue
            ordered.append(value)
            seen.add(value)
        return ordered

    def _to_event_evidence_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # 基底
        kind = str(record.get("kind", "event")).strip() or "event"
        item = {
            "event_id": record["event_id"],
            "kind": kind,
        }

        # スロット群
        anchor = self._event_evidence_anchor(record)
        topic = self._event_evidence_topic(record)
        decision_or_result = self._event_evidence_decision_or_result(record)
        tone_or_note = self._event_evidence_tone_or_note(record)
        if anchor is not None:
            item["anchor"] = anchor
        if topic is not None:
            item["topic"] = topic
        if decision_or_result is not None:
            item["decision_or_result"] = decision_or_result
        if tone_or_note is not None:
            item["tone_or_note"] = tone_or_note
        return item

    def _event_evidence_anchor(self, record: dict[str, Any]) -> str | None:
        # ラベル
        kind = str(record.get("kind", "")).strip()
        label = {
            "decision": "判断",
            "observation": "会話",
            "reply": "返答",
        }.get(kind, "出来事")

        # タイムスタンプ
        created_at = str(record.get("created_at", "")).strip()
        if not created_at:
            return label
        normalized = created_at.replace("T", " ")
        return f"{normalized[:16]} の{label}"

    def _event_evidence_topic(self, record: dict[str, Any]) -> str | None:
        # 種別Switch
        kind = str(record.get("kind", "")).strip()
        if kind not in {"observation", "reply"}:
            return None
        return self._short_event_text(record.get("text"))

    def _event_evidence_decision_or_result(self, record: dict[str, Any]) -> str | None:
        # 種別確認
        kind = str(record.get("kind", "")).strip()
        if kind != "decision":
            return None

        # result_kind判定
        result_kind = str(record.get("result_kind", "")).strip()
        if result_kind:
            return f"{result_kind} を選んだ"
        return "応答方針を決めた"

    def _event_evidence_tone_or_note(self, record: dict[str, Any]) -> str | None:
        # 種別Switch
        kind = str(record.get("kind", "")).strip()
        if kind == "decision":
            reason_code = str(record.get("reason_code", "")).strip()
            return f"reason={reason_code}" if reason_code else None
        if kind == "reply":
            return "assistant_reply"
        if kind == "observation":
            return "user_message"
        return None

    def _short_event_text(self, value: Any) -> str | None:
        # 正規化
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        if not normalized:
            return None

        # 結果
        if len(normalized) <= 56:
            return normalized
        return normalized[:55] + "…"
