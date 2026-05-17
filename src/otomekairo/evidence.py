from __future__ import annotations

import json
from typing import Any

from otomekairo.memory_utils import localize_timestamp_fields


# 定数
EVIDENCE_ITEM_LIMIT = 5
EVENT_TEXT_LIMIT = 240


# 回答根拠解決
class EvidenceResolver:
    def __init__(self, *, store: Any) -> None:
        # 依存
        self.store = store

    def build_evidence_pack(
        self,
        *,
        memory_set_id: str,
        input_text: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        # contract 実行
        _ = input_text
        contract = str(answer_contract.get("contract") or "summary")
        if contract == "exact_boundary":
            return self._resolve_exact_boundary(
                memory_set_id=memory_set_id,
                answer_contract=answer_contract,
                current_time=current_time,
            )
        if contract == "exact_statement":
            return self._resolve_exact_statement(
                memory_set_id=memory_set_id,
                answer_contract=answer_contract,
                current_time=current_time,
            )
        if contract == "provenance":
            return self._resolve_provenance(
                memory_set_id=memory_set_id,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                current_time=current_time,
            )
        if contract == "conflict_check":
            return self._resolve_conflicts(
                recall_pack=recall_pack,
                answer_contract=answer_contract,
            )
        return self._summary_pack(answer_contract=answer_contract)

    def _resolve_exact_boundary(
        self,
        *,
        memory_set_id: str,
        answer_contract: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        boundary = str(answer_contract.get("boundary") or "none")
        target_actor = str(answer_contract.get("target_actor") or "any")
        records = self.store.list_boundary_events_for_evidence(
            memory_set_id=memory_set_id,
            target_actor=target_actor,
            boundary=boundary,
            before_iso=current_time,
            limit=1,
        )
        if not records:
            return self._missing_pack(
                answer_contract=answer_contract,
                reason="direct_event_not_found",
                guidance="該当する raw event が見つからないため、正確な日時や原文として断定しない。",
            )
        return self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=[self._event_evidence_item(records[0])],
            reply_guidance=self._boundary_reply_guidance(boundary, records[0]),
        )

    def _resolve_exact_statement(
        self,
        *,
        memory_set_id: str,
        answer_contract: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        boundary = str(answer_contract.get("boundary") or "none")
        target_actor = str(answer_contract.get("target_actor") or "any")
        if boundary in {"first", "latest"}:
            return self._resolve_boundary_statement(
                memory_set_id=memory_set_id,
                answer_contract=answer_contract,
                current_time=current_time,
                boundary=boundary,
                target_actor=target_actor,
            )
        query_terms = [
            term.strip()
            for term in answer_contract.get("query_terms", [])
            if isinstance(term, str) and term.strip()
        ]
        records = self.store.search_text_events_for_evidence(
            memory_set_id=memory_set_id,
            target_actor=target_actor,
            query_terms=query_terms,
            before_iso=current_time,
            limit=EVIDENCE_ITEM_LIMIT,
        )
        if not records:
            return self._missing_pack(
                answer_contract=answer_contract,
                reason="exact_statement_not_found",
                guidance="該当する raw event が見つからないため、一字一句の発言として再現しない。",
            )
        guidance = (
            "evidence_items.text は raw event の原文である。"
            "対象発話が曖昧な場合は候補として提示し、ログが存在しないとは言わない。"
        )
        return self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=[self._event_evidence_item(record) for record in records],
            reply_guidance=guidance,
        )

    def _resolve_boundary_statement(
        self,
        *,
        memory_set_id: str,
        answer_contract: dict[str, Any],
        current_time: str,
        boundary: str,
        target_actor: str,
    ) -> dict[str, Any]:
        boundary_records = self.store.list_boundary_events_for_evidence(
            memory_set_id=memory_set_id,
            target_actor=target_actor,
            boundary=boundary,
            before_iso=current_time,
            limit=1,
        )
        if not boundary_records:
            return self._missing_pack(
                answer_contract=answer_contract,
                reason="boundary_statement_not_found",
                guidance="境界に該当する raw event が見つからないため、一字一句の会話として再現しない。",
            )

        boundary_record = boundary_records[0]
        cycle_id = str(boundary_record.get("cycle_id") or "")
        cycle_records: list[dict[str, Any]] = []
        if cycle_id:
            cycle_records = self.store.list_cycle_events_for_evidence(
                memory_set_id=memory_set_id,
                cycle_id=cycle_id,
                target_actor=target_actor,
                limit=EVIDENCE_ITEM_LIMIT,
            )
        if not cycle_records:
            cycle_records = boundary_records

        guidance = (
            "evidence_items は boundary で特定した cycle の raw event 原文である。"
            "この原文をそのまま会話として提示し、ログが存在しないとは言わない。"
        )
        return self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=[self._event_evidence_item(record) for record in cycle_records],
            reply_guidance=guidance,
        )

    def _resolve_provenance(
        self,
        *,
        memory_set_id: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        items = self._resolve_recall_items(recall_pack)
        if items:
            return self._grounded_pack(
                answer_contract=answer_contract,
                evidence_items=items,
                reply_guidance="根拠として渡された evidence_items だけを使い、根拠が弱い部分は弱いと明示する。",
            )
        return self._resolve_exact_statement(
            memory_set_id=memory_set_id,
            answer_contract=answer_contract,
            current_time=current_time,
        )

    def _resolve_conflicts(
        self,
        *,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
    ) -> dict[str, Any]:
        conflicts = recall_pack.get("conflicts", [])
        items = [
            self._recall_evidence_item(item, item_type="conflict")
            for item in conflicts
            if isinstance(item, dict)
        ][:EVIDENCE_ITEM_LIMIT]
        if not items:
            return self._missing_pack(
                answer_contract=answer_contract,
                reason="conflict_not_found",
                guidance="矛盾根拠が見つからないため、矛盾が存在すると断定しない。",
            )
        return self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=items,
            reply_guidance="conflict evidence を比較し、どの記録同士が食い違うかを短く説明する。",
        )

    def _resolve_recall_items(self, recall_pack: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for section in ("event_evidence", "episodic_evidence", "user_model", "relationship_model", "self_model"):
            values = recall_pack.get(section, [])
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                items.append(self._recall_evidence_item(value, item_type=section))
                if len(items) >= EVIDENCE_ITEM_LIMIT:
                    return items
        return items

    def _summary_pack(self, *, answer_contract: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "summary",
            "answer_contract": answer_contract,
            "requires_direct_evidence": False,
            "evidence_items": [],
            "reply_guidance": "通常の recall_pack と直近文脈で自然に応答する。",
        }

    def _grounded_pack(
        self,
        *,
        answer_contract: dict[str, Any],
        evidence_items: list[dict[str, Any]],
        reply_guidance: str,
    ) -> dict[str, Any]:
        return {
            "status": "grounded",
            "answer_contract": answer_contract,
            "requires_direct_evidence": bool(answer_contract.get("requires_direct_evidence")),
            "evidence_items": localize_timestamp_fields(evidence_items),
            "reply_guidance": reply_guidance,
        }

    def _missing_pack(
        self,
        *,
        answer_contract: dict[str, Any],
        reason: str,
        guidance: str,
    ) -> dict[str, Any]:
        return {
            "status": "missing",
            "answer_contract": answer_contract,
            "requires_direct_evidence": bool(answer_contract.get("requires_direct_evidence")),
            "missing_reason": reason,
            "evidence_items": [],
            "reply_guidance": guidance,
        }

    def _event_evidence_item(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = self._payload(record)
        text = self._first_text(record.get("text"), payload.get("text"))
        created_at = str(record.get("created_at") or payload.get("created_at") or "")
        return {
            "type": "event",
            "event_id": record.get("event_id") or payload.get("event_id"),
            "cycle_id": record.get("cycle_id") or payload.get("cycle_id"),
            "kind": record.get("kind") or payload.get("kind"),
            "role": record.get("role") or payload.get("role"),
            "created_at": created_at,
            "recorded_date": self._recorded_date(created_at),
            "text": self._clamp_text(text),
        }

    def _recall_evidence_item(self, item: dict[str, Any], *, item_type: str) -> dict[str, Any]:
        text = self._first_text(
            item.get("text"),
            item.get("summary"),
            item.get("summary_text"),
            item.get("content"),
        )
        return {
            "type": item_type,
            "source_id": item.get("event_id") or item.get("episode_id") or item.get("memory_id") or item.get("id"),
            "text": self._clamp_text(text),
            "payload": localize_timestamp_fields(item),
        }

    def _boundary_reply_guidance(self, boundary: str, record: dict[str, Any]) -> str:
        item = self._event_evidence_item(record)
        if boundary == "first":
            return (
                "最初の raw event を根拠に答える。"
                f"日付は recorded_date={item.get('recorded_date')} を使い、text は必要なら原文として引用する。"
            )
        return (
            "最新の raw event を根拠に答える。"
            f"日付は recorded_date={item.get('recorded_date')} を使い、text は必要なら原文として引用する。"
        )

    def _payload(self, record: dict[str, Any]) -> dict[str, Any]:
        payload_json = record.get("payload_json")
        if not isinstance(payload_json, str):
            return {}
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    def _clamp_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if len(normalized) <= EVENT_TEXT_LIMIT:
            return normalized
        return normalized[: EVENT_TEXT_LIMIT - 1].rstrip() + "…"

    def _first_text(self, *values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _recorded_date(self, created_at: str) -> str | None:
        if not created_at:
            return None
        return created_at.split("T", 1)[0]
