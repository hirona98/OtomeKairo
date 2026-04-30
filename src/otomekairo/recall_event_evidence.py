from __future__ import annotations

from typing import Any

from otomekairo.llm_contracts import LLMContractError, LLMError


# 定数
EVENT_EVIDENCE_LIMIT = 3
EVENT_EVIDENCE_FOCUSES = {
    "commitment",
    "fact",
    "relationship",
    "episodic",
}
EVENT_EVIDENCE_SOURCE_SUMMARY_LIMIT = 2
PRECISE_EVENT_EVIDENCE_LIMIT = 3
PRECISE_EVENT_EVIDENCE_FOCUS_HISTORY = {
    "commitment",
    "relationship",
    "fact",
}
PRECISE_EVENT_EVIDENCE_HISTORY_TIME_REFERENCES = {
    "past",
    "persistent",
}
PRECISE_EVENT_EVIDENCE_RISK_FLAGS = {
    "ambiguous_reference",
    "mixed_intent",
    "time_ambiguous",
    "weak_memory_cue",
}


# イベント根拠Mixin
class RecallEventEvidenceMixin:
    def _build_event_evidence(
        self,
        *,
        memory_set_id: str,
        primary_recall_focus: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
        role_definition: dict[str, Any],
    ) -> dict[str, Any]:
        # 初期状態
        result = self._empty_event_evidence_result()

        # 確認
        if not self._should_load_event_evidence(
            primary_recall_focus=primary_recall_focus,
            recall_hint=recall_hint,
            sections=sections,
        ):
            return result

        # 選択済みID群
        selected_event_ids = self._select_event_evidence_ids(
            primary_recall_focus=primary_recall_focus,
            sections=sections,
        )
        result["selected_event_ids"] = selected_event_ids
        precise_plan = self._plan_precise_event_evidence(
            primary_recall_focus=primary_recall_focus,
            recall_hint=recall_hint,
            sections=sections,
            selected_event_ids=selected_event_ids,
        )
        generation = result["event_evidence_generation"]
        generation["precise_evidence_used"] = precise_plan["used"]
        generation["precise_reason_codes"] = precise_plan["reason_codes"]
        generation["precise_reason_summary"] = precise_plan["reason_summary"]
        generation["precise_selected_event_ids"] = precise_plan["selected_event_ids"]
        generation["precise_requested_event_count"] = len(precise_plan["selected_event_ids"])
        requested_event_ids = [
            *selected_event_ids,
            *precise_plan["selected_event_ids"],
        ]
        generation["requested_event_count"] = len(requested_event_ids)
        if not requested_event_ids:
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
        precise_records = self.store.load_events_for_evidence(
            memory_set_id=memory_set_id,
            event_ids=precise_plan["selected_event_ids"],
            limit=PRECISE_EVENT_EVIDENCE_LIMIT,
        )
        precise_records_by_id = {
            record["event_id"]: record
            for record in precise_records
            if isinstance(record, dict) and isinstance(record.get("event_id"), str)
        }
        records_by_id.update(precise_records_by_id)
        generation["loaded_event_count"] = len(records_by_id)
        generation["precise_loaded_event_count"] = len(precise_records_by_id)

        # event 単位生成
        event_evidence: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []
        precise_selected_set = set(precise_plan["selected_event_ids"])
        for event_id in requested_event_ids:
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
                    primary_recall_focus=primary_recall_focus,
                    recall_hint=recall_hint,
                    sections=sections,
                    event_id=event_id,
                    record=record,
                    selection_mode="precise" if event_id in precise_selected_set else "standard",
                    precise_reason_summary=(
                        precise_plan["reason_summary"] if event_id in precise_selected_set else None
                    ),
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
            "precise_evidence_used": False,
            "precise_reason_codes": [],
            "precise_reason_summary": None,
            "precise_selected_event_ids": [],
            "precise_requested_event_count": 0,
            "precise_loaded_event_count": 0,
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
        primary_recall_focus: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> bool:
        # source確認
        if not self._has_event_evidence_sources(primary_recall_focus=primary_recall_focus, sections=sections):
            return False

        # focus確認
        if primary_recall_focus in EVENT_EVIDENCE_FOCUSES:
            return True

        # 時刻確認
        return recall_hint.get("time_reference") == "past"

    def _has_event_evidence_sources(
        self,
        *,
        primary_recall_focus: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> bool:
        # 走査
        for section_name in self._event_evidence_section_priority(primary_recall_focus):
            for item in sections.get(section_name, []):
                if self._prioritized_event_ids_for_item(item):
                    return True
        return False

    def _select_event_evidence_ids(
        self,
        *,
        primary_recall_focus: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        # source群
        sources = self._event_evidence_sources(
            primary_recall_focus=primary_recall_focus,
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
        primary_recall_focus: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[list[str]]:
        # 状態
        sources: list[list[str]] = []

        # 収集
        for section_name in self._event_evidence_section_priority(primary_recall_focus):
            for item in sections.get(section_name, []):
                prioritized_event_ids = self._prioritized_event_ids_for_item(item)
                if not prioritized_event_ids:
                    continue
                sources.append(prioritized_event_ids)
        return sources

    def _plan_precise_event_evidence(
        self,
        *,
        primary_recall_focus: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
        selected_event_ids: list[str],
    ) -> dict[str, Any]:
        # selected event 不在
        if not selected_event_ids:
            return {
                "used": False,
                "reason_codes": ["no_selected_events"],
                "reason_summary": "selected event が無いため、追加 event の確認は行わない。",
                "selected_event_ids": [],
            }

        # ゲート理由
        reason_codes = self._precise_event_evidence_reason_codes(
            primary_recall_focus=primary_recall_focus,
            recall_hint=recall_hint,
            sections=sections,
            selected_event_ids=selected_event_ids,
        )
        if not reason_codes:
            return {
                "used": False,
                "reason_codes": ["not_needed"],
                "reason_summary": "曖昧性や継続確認の条件が無いため、圧縮済み event_evidence だけを使う。",
                "selected_event_ids": [],
            }

        # selected source にぶら下がる追加 event だけを限定で開く。
        precise_event_ids = self._select_precise_event_evidence_ids(
            primary_recall_focus=primary_recall_focus,
            sections=sections,
            selected_event_ids=selected_event_ids,
        )
        if not precise_event_ids:
            merged_reason_codes = [*reason_codes, "no_additional_candidates"]
            return {
                "used": False,
                "reason_codes": merged_reason_codes,
                "reason_summary": self._precise_event_evidence_reason_summary(
                    reason_codes=merged_reason_codes,
                    used=False,
                ),
                "selected_event_ids": [],
            }

        return {
            "used": True,
            "reason_codes": reason_codes,
            "reason_summary": self._precise_event_evidence_reason_summary(
                reason_codes=reason_codes,
                used=True,
            ),
            "selected_event_ids": precise_event_ids,
        }

    def _precise_event_evidence_reason_codes(
        self,
        *,
        primary_recall_focus: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
        selected_event_ids: list[str],
    ) -> list[str]:
        # 判定理由群
        reason_codes: list[str] = []
        risk_flags = {
            str(value)
            for value in (recall_hint.get("risk_flags") or [])
            if isinstance(value, str)
        }
        if risk_flags & PRECISE_EVENT_EVIDENCE_RISK_FLAGS:
            reason_codes.append("risk_flags_present")

        time_reference = str(recall_hint.get("time_reference") or "none")
        if (
            primary_recall_focus in PRECISE_EVENT_EVIDENCE_FOCUS_HISTORY
            and time_reference in PRECISE_EVENT_EVIDENCE_HISTORY_TIME_REFERENCES
        ):
            reason_codes.append("focus_requires_history")

        if (
            len(selected_event_ids) <= 1
            and self._selected_sources_have_additional_events(
                primary_recall_focus=primary_recall_focus,
                sections=sections,
                selected_event_ids=selected_event_ids,
            )
        ):
            reason_codes.append("thin_selected_coverage")
        return reason_codes

    def _selected_sources_have_additional_events(
        self,
        *,
        primary_recall_focus: str,
        sections: dict[str, list[dict[str, Any]]],
        selected_event_ids: list[str],
    ) -> bool:
        selected_event_set = {event_id for event_id in selected_event_ids if isinstance(event_id, str)}
        if not selected_event_set:
            return False

        # selected 済み source に sibling event が残るかだけを見る。
        for section_name in self._event_evidence_section_priority(primary_recall_focus):
            for item in sections.get(section_name, []):
                prioritized_event_ids = self._prioritized_event_ids_for_item(item)
                if len(prioritized_event_ids) <= 1:
                    continue
                if any(event_id in selected_event_set for event_id in prioritized_event_ids):
                    return True
        return False

    def _select_precise_event_evidence_ids(
        self,
        *,
        primary_recall_focus: str,
        sections: dict[str, list[dict[str, Any]]],
        selected_event_ids: list[str],
    ) -> list[str]:
        # selected source の sibling event だけを追加候補にする。
        selected_event_set = {event_id for event_id in selected_event_ids if isinstance(event_id, str)}
        if not selected_event_set:
            return []

        precise_event_ids: list[str] = []
        seen = set(selected_event_set)
        for section_name in self._event_evidence_section_priority(primary_recall_focus):
            for item in sections.get(section_name, []):
                prioritized_event_ids = self._prioritized_event_ids_for_item(item)
                if len(prioritized_event_ids) <= 1:
                    continue
                if not any(event_id in selected_event_set for event_id in prioritized_event_ids):
                    continue
                for event_id in prioritized_event_ids:
                    if event_id in seen:
                        continue
                    precise_event_ids.append(event_id)
                    seen.add(event_id)
                    if len(precise_event_ids) >= PRECISE_EVENT_EVIDENCE_LIMIT:
                        return precise_event_ids
        return precise_event_ids

    def _precise_event_evidence_reason_summary(
        self,
        *,
        reason_codes: list[str],
        used: bool,
    ) -> str:
        if "no_selected_events" in reason_codes:
            return "selected event が無いため、追加 event の確認は行わない。"
        if reason_codes == ["not_needed"]:
            return "曖昧性や継続確認の条件が無いため、圧縮済み event_evidence だけを使う。"

        reason_texts: list[str] = []
        for code in reason_codes:
            if code == "risk_flags_present":
                reason_texts.append("曖昧参照や弱い手掛かりが含まれる")
            elif code == "focus_requires_history":
                reason_texts.append("継続判断で前後 event の確認価値が高い")
            elif code == "thin_selected_coverage":
                reason_texts.append("selected event が薄く同じ source の別 event を確認したい")

        if "no_additional_candidates" in reason_codes:
            base = "、".join(reason_texts) if reason_texts else "追加確認の条件はあった"
            return f"{base}が、selected source から追加で開ける event が無かった。"

        base = "、".join(reason_texts) if reason_texts else "追加確認の条件がある"
        if used:
            return f"{base}ため、selected source の sibling event を限定ロードして確認する。"
        return f"{base}が、今回は追加 event を開かない。"

    def _event_evidence_section_priority(self, primary_recall_focus: str) -> list[str]:
        # 基底順序
        ordered = ["episodic_evidence"]
        recall_hint = {
            "interaction_mode": "conversation",
            "primary_recall_focus": primary_recall_focus,
            "secondary_recall_focuses": [],
            "confidence": 1.0,
            "time_reference": "none",
            "focus_scopes": [],
            "mentioned_entities": [],
            "mentioned_topics": [],
            "risk_flags": [],
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
        primary_recall_focus: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
        event_id: str,
        record: dict[str, Any],
        selection_mode: str = "standard",
        precise_reason_summary: str | None = None,
    ) -> dict[str, Any]:
        # source 群
        matched_sources = self._matched_event_evidence_sources(
            primary_recall_focus=primary_recall_focus,
            sections=sections,
            event_id=event_id,
        )
        selection_basis = self._event_evidence_selection_basis(matched_sources)
        selection_basis["selection_mode"] = selection_mode
        if precise_reason_summary is not None:
            selection_basis["precise_reason_summary"] = precise_reason_summary

        # 結果
        return {
            "interaction_mode": str(recall_hint.get("interaction_mode") or "conversation"),
            "primary_recall_focus": primary_recall_focus,
            "secondary_recall_focuses": self._secondary_recall_focuses(recall_hint),
            "time_reference": str(recall_hint.get("time_reference") or "none"),
            "risk_flags": list(recall_hint.get("risk_flags") or []),
            "selection_basis": selection_basis,
            "event": self._event_evidence_source_event(record),
        }

    def _matched_event_evidence_sources(
        self,
        *,
        primary_recall_focus: str,
        sections: dict[str, list[dict[str, Any]]],
        event_id: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        # 収集
        matched: list[tuple[str, dict[str, Any]]] = []
        for section_name in self._event_evidence_section_priority(primary_recall_focus):
            for item in sections.get(section_name, []):
                if event_id not in self._prioritized_event_ids_for_item(item):
                    continue
                matched.append((section_name, item))
        return matched

    def _event_evidence_selection_basis(
        self,
        matched_sources: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
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
