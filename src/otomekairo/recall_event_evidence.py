from __future__ import annotations

from typing import Any

from otomekairo.llm_contracts import LLMContractError, LLMError


# 定数
EVENT_EVIDENCE_LIMIT = 3
EVENT_EVIDENCE_INTENTS = {
    "commitment_check",
    "fact_query",
    "meta_relationship",
    "reminisce",
}
EVENT_EVIDENCE_SOURCE_SUMMARY_LIMIT = 2


# イベント根拠Mixin
class RecallEventEvidenceMixin:
    def _build_event_evidence(
        self,
        *,
        memory_set_id: str,
        primary_intent: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
        role_definition: dict[str, Any],
    ) -> dict[str, Any]:
        # 初期状態
        result = self._empty_event_evidence_result()

        # 確認
        if not self._should_load_event_evidence(
            primary_intent=primary_intent,
            recall_hint=recall_hint,
            sections=sections,
        ):
            return result

        # 選択済みID群
        selected_event_ids = self._select_event_evidence_ids(
            primary_intent=primary_intent,
            sections=sections,
        )
        result["selected_event_ids"] = selected_event_ids
        result["event_evidence_generation"]["requested_event_count"] = len(selected_event_ids)
        if not selected_event_ids:
            return result

        # 読み込み
        records = self.store.load_events_for_evidence(
            memory_set_id=memory_set_id,
            event_ids=selected_event_ids,
            limit=EVENT_EVIDENCE_LIMIT,
        )
        records_by_id = {
            record["event_id"]: record
            for record in records
            if isinstance(record, dict) and isinstance(record.get("event_id"), str)
        }
        result["event_evidence_generation"]["loaded_event_count"] = len(records_by_id)

        # event 単位生成
        event_evidence: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []
        for event_id in selected_event_ids:
            record = records_by_id.get(event_id)
            if record is None:
                failed_items.append(
                    self._event_evidence_failure_item(
                        event_id=event_id,
                        kind="event",
                        failure_stage="load_event",
                        failure_reason="selected event was not found in events table.",
                    )
                )
                continue

            kind = self._event_evidence_kind(record)
            try:
                source_pack = self._build_event_evidence_source_pack(
                    primary_intent=primary_intent,
                    recall_hint=recall_hint,
                    sections=sections,
                    event_id=event_id,
                    record=record,
                )
            except Exception as exc:  # noqa: BLE001
                failed_items.append(
                    self._event_evidence_failure_item(
                        event_id=event_id,
                        kind=kind,
                        failure_stage="build_source_pack",
                        failure_reason=str(exc),
                    )
                )
                continue

            try:
                payload = self.llm.generate_event_evidence(
                    role_definition=role_definition,
                    source_pack=source_pack,
                )
            except LLMContractError as exc:
                failed_items.append(
                    self._event_evidence_failure_item(
                        event_id=event_id,
                        kind=kind,
                        failure_stage="contract_validation",
                        failure_reason=str(exc),
                    )
                )
                continue
            except LLMError as exc:
                failed_items.append(
                    self._event_evidence_failure_item(
                        event_id=event_id,
                        kind=kind,
                        failure_stage="llm_generation",
                        failure_reason=str(exc),
                    )
                )
                continue
            except Exception as exc:  # noqa: BLE001
                failed_items.append(
                    self._event_evidence_failure_item(
                        event_id=event_id,
                        kind=kind,
                        failure_stage="llm_generation",
                        failure_reason=str(exc),
                    )
                )
                continue

            try:
                event_evidence.append(
                    self._event_evidence_item_from_payload(
                        event_id=event_id,
                        kind=kind,
                        payload=payload,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed_items.append(
                    self._event_evidence_failure_item(
                        event_id=event_id,
                        kind=kind,
                        failure_stage="contract_validation",
                        failure_reason=str(exc),
                    )
                )

        # 結果
        result["event_evidence"] = event_evidence
        result["event_evidence_generation"]["succeeded_event_count"] = len(event_evidence)
        result["event_evidence_generation"]["failed_items"] = failed_items
        return result

    def _empty_event_evidence_result(self) -> dict[str, Any]:
        return {
            "event_evidence": [],
            "selected_event_ids": [],
            "event_evidence_generation": self._empty_event_evidence_generation(),
        }

    def _empty_event_evidence_generation(self) -> dict[str, Any]:
        return {
            "requested_event_count": 0,
            "loaded_event_count": 0,
            "succeeded_event_count": 0,
            "failed_items": [],
        }

    def _event_evidence_failure_item(
        self,
        *,
        event_id: str,
        kind: str,
        failure_stage: str,
        failure_reason: str,
    ) -> dict[str, str]:
        return {
            "event_id": event_id,
            "kind": kind,
            "failure_stage": failure_stage,
            "failure_reason": failure_reason,
        }

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
        if item["source_kind"] == "episode":
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

    def _build_event_evidence_source_pack(
        self,
        *,
        primary_intent: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
        event_id: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        # source 群
        matched_sources = self._matched_event_evidence_sources(
            primary_intent=primary_intent,
            sections=sections,
            event_id=event_id,
        )

        # 結果
        return {
            "primary_intent": primary_intent,
            "secondary_intents": self._secondary_intents(recall_hint),
            "time_reference": str(recall_hint.get("time_reference") or "none"),
            "selection_basis": self._event_evidence_selection_basis(matched_sources),
            "event": self._event_evidence_source_event(record),
        }

    def _matched_event_evidence_sources(
        self,
        *,
        primary_intent: str,
        sections: dict[str, list[dict[str, Any]]],
        event_id: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        # 収集
        matched: list[tuple[str, dict[str, Any]]] = []
        for section_name in self._event_evidence_section_priority(primary_intent):
            for item in sections.get(section_name, []):
                if event_id not in self._prioritized_event_ids_for_item(item):
                    continue
                matched.append((section_name, item))
        return matched

    def _event_evidence_selection_basis(
        self,
        matched_sources: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, list[str]]:
        # section 群
        retrieval_sections: list[str] = []
        source_summaries: list[str] = []
        for section_name, item in matched_sources:
            if section_name not in retrieval_sections:
                retrieval_sections.append(section_name)

            summary_text = self._event_evidence_source_summary(item)
            if summary_text is None or summary_text in source_summaries:
                continue
            source_summaries.append(summary_text)
            if len(source_summaries) >= EVENT_EVIDENCE_SOURCE_SUMMARY_LIMIT:
                break

        # 結果
        return {
            "retrieval_sections": retrieval_sections,
            "source_summaries": source_summaries,
        }

    def _event_evidence_source_summary(self, item: dict[str, Any]) -> str | None:
        return self._compact_text(item.get("summary_text"), limit=96)

    def _event_evidence_source_event(self, record: dict[str, Any]) -> dict[str, Any]:
        kind = self._event_evidence_kind(record)
        return {
            "kind": kind,
            "role": self._compact_text(record.get("role"), limit=32),
            "created_at": self._compact_text(record.get("created_at"), limit=40),
            "text": self._compact_text(record.get("text"), limit=120),
            "result_kind": self._compact_text(record.get("result_kind"), limit=32),
            "external_result_kind": self._compact_text(record.get("external_result_kind"), limit=32),
            "reason_code": self._compact_text(record.get("reason_code"), limit=48),
            "reason_summary": self._compact_text(record.get("reason_summary"), limit=120),
            "pending_intent_summary": self._compact_pending_intent_summary(record.get("pending_intent_summary")),
        }

    def _compact_pending_intent_summary(self, value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None

        payload: dict[str, str] = {}
        for key_name, limit in (
            ("intent_kind", 48),
            ("intent_summary", 120),
            ("reason_summary", 120),
        ):
            normalized = self._compact_text(value.get(key_name), limit=limit)
            if normalized is not None:
                payload[key_name] = normalized
        if not payload:
            return None
        return payload

    def _event_evidence_item_from_payload(
        self,
        *,
        event_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # 基底
        item = {
            "event_id": event_id,
            "kind": kind,
        }

        # slot 群
        for key_name in ("anchor", "topic", "decision_or_result", "tone_or_note"):
            value = payload.get(key_name)
            if value is None:
                continue
            item[key_name] = str(value).strip()

        if len(item) <= 2:
            raise ValueError("EventEvidence payload did not contain any present slots.")
        return item

    def _event_evidence_kind(self, record: dict[str, Any]) -> str:
        return str(record.get("kind", "event")).strip() or "event"

    def _compact_text(self, value: Any, *, limit: int) -> str | None:
        # 正規化
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        if not normalized:
            return None

        # 結果
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"
