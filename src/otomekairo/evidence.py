from __future__ import annotations

import json
import re
from typing import Any

from otomekairo.memory_utils import localize_timestamp_fields


# 定数
EVIDENCE_ITEM_LIMIT = 5
EVENT_TEXT_LIMIT = 240
TRACE_SECTION_ITEM_LIMIT = 3
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
JAPANESE_DATE_PATTERN = re.compile(r"\b\d{4}年\d{1,2}月\d{1,2}日\b")


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
        resolution = self.build_evidence_resolution(
            memory_set_id=memory_set_id,
            input_text=input_text,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            current_time=current_time,
        )
        return resolution["evidence_pack"]

    def build_evidence_resolution(
        self,
        *,
        memory_set_id: str,
        input_text: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        # contract 実行
        contract = str(answer_contract.get("contract") or "summary")
        if contract == "exact_boundary":
            return self._resolve_exact_boundary(
                memory_set_id=memory_set_id,
                input_text=input_text,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                current_time=current_time,
            )
        if contract == "exact_statement":
            return self._resolve_exact_statement(
                memory_set_id=memory_set_id,
                input_text=input_text,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                current_time=current_time,
            )
        if contract == "provenance":
            return self._resolve_provenance(
                memory_set_id=memory_set_id,
                input_text=input_text,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                current_time=current_time,
            )
        if contract == "conflict_check":
            return self._resolve_conflicts(
                input_text=input_text,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                current_time=current_time,
            )
        evidence_pack = self._summary_pack(answer_contract=answer_contract)
        return self._resolution(
            input_text=input_text,
            current_time=current_time,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            evidence_pack=evidence_pack,
            resolver_path="summary",
        )

    def _resolve_exact_boundary(
        self,
        *,
        memory_set_id: str,
        input_text: str,
        recall_pack: dict[str, Any],
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
            limit=EVIDENCE_ITEM_LIMIT,
        )
        if not records:
            evidence_pack = self._missing_pack(
                answer_contract=answer_contract,
                reason="direct_event_not_found",
                guidance="該当する raw event が見つからないため、正確な日時や原文として断定しない。",
            )
            return self._resolution(
                input_text=input_text,
                current_time=current_time,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                evidence_pack=evidence_pack,
                resolver_path="exact_boundary",
                boundary_event_candidates=[],
            )
        evidence_pack = self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=[self._event_evidence_item(records[0])],
            reply_guidance=self._boundary_reply_guidance(boundary, records[0]),
        )
        return self._resolution(
            input_text=input_text,
            current_time=current_time,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            evidence_pack=evidence_pack,
            resolver_path="exact_boundary",
            boundary_event_candidates=records,
        )

    def _resolve_exact_statement(
        self,
        *,
        memory_set_id: str,
        input_text: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        current_time: str,
        resolver_path: str = "exact_statement.search",
    ) -> dict[str, Any]:
        boundary = str(answer_contract.get("boundary") or "none")
        target_actor = str(answer_contract.get("target_actor") or "any")
        if boundary in {"first", "latest"}:
            return self._resolve_boundary_statement(
                memory_set_id=memory_set_id,
                input_text=input_text,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                current_time=current_time,
                boundary=boundary,
                target_actor=target_actor,
                resolver_path="exact_statement.boundary_cycle",
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
            evidence_pack = self._missing_pack(
                answer_contract=answer_contract,
                reason="exact_statement_not_found",
                guidance="該当する raw event が見つからないため、一字一句の発言として再現しない。",
            )
            return self._resolution(
                input_text=input_text,
                current_time=current_time,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                evidence_pack=evidence_pack,
                resolver_path=resolver_path,
                statement_event_candidates=[],
            )
        guidance = (
            "evidence_items.text は raw event の原文である。"
            "対象発話が曖昧な場合は候補として提示し、ログが存在しないとは言わない。"
        )
        evidence_pack = self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=[self._event_evidence_item(record) for record in records],
            reply_guidance=guidance,
        )
        return self._resolution(
            input_text=input_text,
            current_time=current_time,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            evidence_pack=evidence_pack,
            resolver_path=resolver_path,
            statement_event_candidates=records,
        )

    def _resolve_boundary_statement(
        self,
        *,
        memory_set_id: str,
        input_text: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        current_time: str,
        boundary: str,
        target_actor: str,
        resolver_path: str,
    ) -> dict[str, Any]:
        boundary_records = self.store.list_boundary_events_for_evidence(
            memory_set_id=memory_set_id,
            target_actor=target_actor,
            boundary=boundary,
            before_iso=current_time,
            limit=EVIDENCE_ITEM_LIMIT,
        )
        if not boundary_records:
            evidence_pack = self._missing_pack(
                answer_contract=answer_contract,
                reason="boundary_statement_not_found",
                guidance="境界に該当する raw event が見つからないため、一字一句の会話として再現しない。",
            )
            return self._resolution(
                input_text=input_text,
                current_time=current_time,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                evidence_pack=evidence_pack,
                resolver_path=resolver_path,
                boundary_event_candidates=[],
                cycle_event_candidates=[],
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
        evidence_pack = self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=[self._event_evidence_item(record) for record in cycle_records],
            reply_guidance=guidance,
        )
        return self._resolution(
            input_text=input_text,
            current_time=current_time,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            evidence_pack=evidence_pack,
            resolver_path=resolver_path,
            boundary_event_candidates=boundary_records,
            cycle_event_candidates=cycle_records,
        )

    def _resolve_provenance(
        self,
        *,
        memory_set_id: str,
        input_text: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        items = self._resolve_recall_items(recall_pack)
        if items:
            evidence_pack = self._grounded_pack(
                answer_contract=answer_contract,
                evidence_items=items,
                reply_guidance="根拠として渡された evidence_items だけを使い、根拠が弱い部分は弱いと明示する。",
            )
            return self._resolution(
                input_text=input_text,
                current_time=current_time,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                evidence_pack=evidence_pack,
                resolver_path="provenance.recall",
            )
        return self._resolve_exact_statement(
            memory_set_id=memory_set_id,
            input_text=input_text,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            current_time=current_time,
            resolver_path="provenance.fallback_exact_statement",
        )

    def _resolve_conflicts(
        self,
        *,
        input_text: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        conflicts = recall_pack.get("conflicts", [])
        items = [
            self._recall_evidence_item(item, item_type="conflict")
            for item in conflicts
            if isinstance(item, dict)
        ][:EVIDENCE_ITEM_LIMIT]
        if not items:
            evidence_pack = self._missing_pack(
                answer_contract=answer_contract,
                reason="conflict_not_found",
                guidance="矛盾根拠が見つからないため、矛盾が存在すると断定しない。",
            )
            return self._resolution(
                input_text=input_text,
                current_time=current_time,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                evidence_pack=evidence_pack,
                resolver_path="conflict_check",
                conflict_candidates=[],
            )
        evidence_pack = self._grounded_pack(
            answer_contract=answer_contract,
            evidence_items=items,
            reply_guidance="conflict evidence を比較し、どの記録同士が食い違うかを短く説明する。",
        )
        return self._resolution(
            input_text=input_text,
            current_time=current_time,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            evidence_pack=evidence_pack,
            resolver_path="conflict_check",
            conflict_candidates=conflicts,
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

    def _resolution(
        self,
        *,
        input_text: str,
        current_time: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        evidence_pack: dict[str, Any],
        resolver_path: str,
        boundary_event_candidates: list[dict[str, Any]] | None = None,
        cycle_event_candidates: list[dict[str, Any]] | None = None,
        statement_event_candidates: list[dict[str, Any]] | None = None,
        conflict_candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "evidence_pack": evidence_pack,
            "fact_resolution_trace": self._build_fact_resolution_trace(
                input_text=input_text,
                current_time=current_time,
                recall_pack=recall_pack,
                answer_contract=answer_contract,
                evidence_pack=evidence_pack,
                resolver_path=resolver_path,
                boundary_event_candidates=boundary_event_candidates or [],
                cycle_event_candidates=cycle_event_candidates or [],
                statement_event_candidates=statement_event_candidates or [],
                conflict_candidates=conflict_candidates or [],
            ),
        }

    def _build_fact_resolution_trace(
        self,
        *,
        input_text: str,
        current_time: str,
        recall_pack: dict[str, Any],
        answer_contract: dict[str, Any],
        evidence_pack: dict[str, Any],
        resolver_path: str,
        boundary_event_candidates: list[dict[str, Any]],
        cycle_event_candidates: list[dict[str, Any]],
        statement_event_candidates: list[dict[str, Any]],
        conflict_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        selected_recall_sections = self._selected_recall_sections(recall_pack)
        boundary_candidates = [self._event_evidence_item(record) for record in boundary_event_candidates[:EVIDENCE_ITEM_LIMIT]]
        cycle_candidates = [self._event_evidence_item(record) for record in cycle_event_candidates[:EVIDENCE_ITEM_LIMIT]]
        statement_candidates = [self._event_evidence_item(record) for record in statement_event_candidates[:EVIDENCE_ITEM_LIMIT]]
        conflict_items = [
            self._recall_evidence_item(item, item_type="conflict")
            for item in conflict_candidates[:EVIDENCE_ITEM_LIMIT]
            if isinstance(item, dict)
        ]
        return {
            "result_status": evidence_pack.get("status", "summary"),
            "resolver_path": resolver_path,
            "query": {
                "input_text": self._clamp_text(input_text),
                "current_time": current_time,
                "contract": answer_contract.get("contract"),
                "boundary": answer_contract.get("boundary"),
                "target_actor": answer_contract.get("target_actor"),
                "reason_codes": list(answer_contract.get("reason_codes") or []),
                "query_terms": list(answer_contract.get("query_terms") or []),
                "requires_direct_evidence": bool(answer_contract.get("requires_direct_evidence")),
            },
            "selected_recall_sections": selected_recall_sections,
            "boundary_event_candidates": boundary_candidates,
            "cycle_event_candidates": cycle_candidates,
            "statement_event_candidates": statement_candidates,
            "conflict_candidates": conflict_items,
            "adopted_evidence_items": list(evidence_pack.get("evidence_items") or [])[:EVIDENCE_ITEM_LIMIT],
            "consistency_checks": self._consistency_checks(
                answer_contract=answer_contract,
                selected_recall_sections=selected_recall_sections,
                boundary_candidates=boundary_candidates,
            ),
            "missing_reason": evidence_pack.get("missing_reason"),
            "reply_guidance": evidence_pack.get("reply_guidance"),
        }

    def _selected_recall_sections(self, recall_pack: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        sections: dict[str, list[dict[str, Any]]] = {}
        for section_name in (
            "self_model",
            "user_model",
            "relationship_model",
            "active_topics",
            "active_commitments",
            "episodic_evidence",
            "event_evidence",
            "conflicts",
        ):
            values = recall_pack.get(section_name, [])
            if not isinstance(values, list):
                sections[section_name] = []
                continue
            compact_items: list[dict[str, Any]] = []
            for value in values[:TRACE_SECTION_ITEM_LIMIT]:
                if not isinstance(value, dict):
                    continue
                compact_items.append(self._compact_recall_section_item(section_name, value))
            sections[section_name] = compact_items
        return sections

    def _compact_recall_section_item(self, section_name: str, item: dict[str, Any]) -> dict[str, Any]:
        source_kind = str(item.get("source_kind") or "")
        if source_kind == "memory_unit":
            return {
                "source_kind": source_kind,
                "memory_unit_id": item.get("memory_unit_id"),
                "memory_type": item.get("memory_type"),
                "scope_type": item.get("scope_type"),
                "scope_key": item.get("scope_key"),
                "predicate": item.get("predicate"),
                "object_ref_or_value": item.get("object_ref_or_value"),
                "summary_text": item.get("summary_text"),
                "status": item.get("status"),
                "confidence": item.get("confidence"),
                "salience": item.get("salience"),
            }
        if source_kind == "episode":
            return {
                "source_kind": source_kind,
                "episode_id": item.get("episode_id"),
                "episode_type": item.get("episode_type"),
                "primary_scope_type": item.get("primary_scope_type"),
                "primary_scope_key": item.get("primary_scope_key"),
                "summary_text": item.get("summary_text"),
                "outcome_text": item.get("outcome_text"),
                "formed_at": item.get("formed_at"),
                "salience": item.get("salience"),
            }
        if section_name == "event_evidence":
            return {
                "source_kind": item.get("source_kind"),
                "event_id": item.get("event_id"),
                "kind": item.get("kind"),
                "recorded_date": item.get("recorded_date"),
                "summary": item.get("summary"),
            }
        if section_name == "conflicts":
            return {
                "source_id": item.get("source_id") or item.get("event_id") or item.get("memory_unit_id"),
                "summary_text": self._first_text(item.get("summary_text"), item.get("text"), item.get("summary")),
            }
        return {
            "source_kind": item.get("source_kind"),
            "summary_text": self._first_text(item.get("summary_text"), item.get("summary"), item.get("text")),
        }

    def _consistency_checks(
        self,
        *,
        answer_contract: dict[str, Any],
        selected_recall_sections: dict[str, list[dict[str, Any]]],
        boundary_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        contract = str(answer_contract.get("contract") or "summary")
        boundary = str(answer_contract.get("boundary") or "none")
        if contract not in {"exact_boundary", "exact_statement"} or boundary not in {"first", "latest"}:
            return []
        if not boundary_candidates:
            return []
        canonical_date = str(boundary_candidates[0].get("recorded_date") or "")
        if not canonical_date:
            return []

        claims = self._extract_date_claims(selected_recall_sections)
        if not claims:
            return [
                {
                    "check_type": "boundary_date_alignment",
                    "boundary": boundary,
                    "canonical_recorded_date": canonical_date,
                    "status": "no_comparable_claim",
                    "claims": [],
                }
            ]

        status = "aligned"
        for claim in claims:
            if claim.get("claim_value") != canonical_date:
                status = "conflict"
                break
        return [
            {
                "check_type": "boundary_date_alignment",
                "boundary": boundary,
                "canonical_recorded_date": canonical_date,
                "status": status,
                "claims": claims,
            }
        ]

    def _extract_date_claims(self, selected_recall_sections: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        claims: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for section_name, items in selected_recall_sections.items():
            for item in items:
                if not isinstance(item, dict):
                    continue
                source_id = self._first_text(
                    item.get("memory_unit_id"),
                    item.get("episode_id"),
                    item.get("event_id"),
                    item.get("source_id"),
                ) or "unknown"
                for claim_kind, raw_value in self._candidate_claim_values(item):
                    claim_value = self._extract_date(raw_value)
                    if claim_value is None:
                        continue
                    identity = (section_name, source_id, claim_value)
                    if identity in seen:
                        continue
                    claims.append(
                        {
                            "section": section_name,
                            "source_id": source_id,
                            "claim_kind": claim_kind,
                            "claim_value": claim_value,
                            "summary_text": self._first_text(item.get("summary_text"), item.get("outcome_text")),
                        }
                    )
                    seen.add(identity)
                    if len(claims) >= EVIDENCE_ITEM_LIMIT:
                        return claims
        return claims

    def _candidate_claim_values(self, item: dict[str, Any]) -> list[tuple[str, Any]]:
        values: list[tuple[str, Any]] = []
        if item.get("object_ref_or_value") is not None:
            values.append(("object_ref_or_value", item.get("object_ref_or_value")))
        if item.get("summary_text") is not None:
            values.append(("summary_text", item.get("summary_text")))
        if item.get("formed_at") is not None:
            values.append(("formed_at", item.get("formed_at")))
        if item.get("recorded_date") is not None:
            values.append(("recorded_date", item.get("recorded_date")))
        return values

    def _extract_date(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        iso_match = DATE_PATTERN.search(normalized)
        if iso_match is not None:
            return iso_match.group(0)
        japanese_match = JAPANESE_DATE_PATTERN.search(normalized)
        if japanese_match is None:
            return None
        year, month, day = re.findall(r"\d+", japanese_match.group(0))
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

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
