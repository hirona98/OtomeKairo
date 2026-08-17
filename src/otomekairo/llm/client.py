from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from otomekairo.capabilities import capability_manifests, validate_capability_payload
from otomekairo.llm.contexts import (
    AutonomousStepContext,
    CurrentInput,
    DecisionContext,
    InitiativeContext,
    PersonaContext,
    SpeechContext,
)
from otomekairo.llm.contracts import (
    LLMContractError,
    LLMError,
    _validate_exact_keys,
    normalize_answer_contract_payload,
    normalize_recall_hint_payload,
    build_decision_target_stances_for_kind,
    validate_activity_state_contract,
    validate_answer_contract_contract,
    validate_autonomous_completion_review_contract,
    validate_autonomous_step_contract,
    validate_decision_contract,
    validate_disclosure_review_contract,
    validate_event_evidence_contract,
    validate_initiative_entry_check_contract,

    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
    validate_pre_send_check_contract,
    validate_pending_intent_selection_contract,
    validate_recall_pack_selection_contract,
    validate_recall_hint_contract,
    validate_visual_observation_contract,
    validate_world_state_contract,
)
from otomekairo.llm.mock import MockLLMClient
from otomekairo.llm.parsing import parse_json_object
from otomekairo.llm.prompts import (
    build_agent_skill_material_selection_messages,
    build_agent_skill_material_selection_repair_prompt,
    build_agent_skill_selection_messages,
    build_agent_skill_selection_repair_prompt,
    build_activity_state_messages,
    build_activity_state_repair_prompt,
    build_autonomous_completion_review_messages,
    build_autonomous_completion_review_repair_prompt,
    build_autonomous_step_messages,
    build_autonomous_step_repair_prompt,
    build_decision_messages,
    build_decision_repair_prompt,
    build_disclosure_review_messages,
    build_disclosure_review_repair_prompt,
    build_event_evidence_messages,
    build_event_evidence_repair_prompt,
    build_initiative_entry_check_messages,
    build_initiative_entry_check_repair_prompt,
    build_input_interpretation_messages,
    build_input_interpretation_repair_prompt,

    build_memory_interpretation_messages,
    build_memory_interpretation_repair_prompt,
    build_memory_reflection_summary_messages,
    build_memory_reflection_summary_repair_prompt,
    build_pre_send_check_messages,
    build_pre_send_check_repair_prompt,
    build_pending_intent_selection_messages,
    build_pending_intent_selection_repair_prompt,
    build_recall_pack_selection_messages,
    build_recall_pack_selection_repair_prompt,
    build_speech_messages,
    build_visual_observation_messages,
    build_visual_observation_repair_prompt,
    build_world_state_messages,
    build_world_state_repair_prompt,
)
from otomekairo.world_state.models import WorldStateSourcePack
from otomekairo.llm.transport import complete_text, generate_embeddings as transport_generate_embeddings
from otomekairo.service.common import debug_log

DEBUG_REJECTED_TEXT_LIMIT = 2000
DEBUG_REJECTED_STRING_LIMIT = 200
DEBUG_REJECTED_LIST_LIMIT = 12
DEBUG_REDACTED_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "authorization",
        "credential",
        "credentials",
        "private_key",
        "arguments",
    }
)
DEBUG_REJECTED_PAYLOAD_KEY_ORDER = (
    "kind",
    "reason_code",
    "foreground_selection",
    "target_stances",
    "capability_request",
    "autonomous_run",
    "pending_intent",
    "action",
    "outcome",
    "section_selection",
    "selected_skill_ids",
)

# LiteLLM連携
@dataclass(slots=True)
class LLMClient:
    mock_client: MockLLMClient = field(default_factory=MockLLMClient)

    def generate_agent_skill_selection(
        self,
        *,
        model_config: dict[str, Any],
        selection_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            return {"selected_skill_ids": [], "reason_summary": "mock model does not select Agent Skills."}
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_agent_skill_selection_messages(selection_context=selection_context),
            validator=lambda payload: self._validate_agent_skill_selection(
                payload,
                selection_context=selection_context,
            ),
            repair_prompt_builder=build_agent_skill_selection_repair_prompt,
            failure_message="Agent Skill の選択に失敗しました。",
            operation="agent_skill_selection",
        )

    def generate_agent_skill_material_selection(
        self,
        *,
        model_config: dict[str, Any],
        selection_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            return {
                "additional_skill_ids": [],
                "resource_reads": [],
                "reason_summary": "mock model does not read Agent Skill materials.",
            }
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_agent_skill_material_selection_messages(selection_context=selection_context),
            validator=lambda payload: self._validate_agent_skill_material_selection(
                payload,
                selection_context=selection_context,
            ),
            repair_prompt_builder=build_agent_skill_material_selection_repair_prompt,
            failure_message="Agent Skill resource の選択に失敗しました。",
            operation="agent_skill_material_selection",
        )

    def _validate_agent_skill_selection(
        self,
        payload: dict[str, Any],
        *,
        selection_context: dict[str, Any] | None = None,
    ) -> None:
        _validate_exact_keys(payload, {"selected_skill_ids", "reason_summary"}, "AgentSkillSelection")
        skill_ids = payload.get("selected_skill_ids")
        reason_summary = payload.get("reason_summary")
        if (
            not isinstance(skill_ids, list)
            or not all(isinstance(value, str) and value.strip() for value in skill_ids)
            or len(skill_ids) != len(set(skill_ids))
            or not isinstance(reason_summary, str)
            or not reason_summary.strip()
        ):
            raise LLMError("AgentSkillSelection の値が不正です。")

        if selection_context is None:
            return
        allowed_values = selection_context.get("allowed_skill_ids")
        if isinstance(allowed_values, list):
            allowed_skill_ids = {
                value
                for value in allowed_values
                if isinstance(value, str)
            }
        else:
            catalog = selection_context.get("skill_catalog")
            allowed_skill_ids = {
                entry.get("skill_id")
                for entry in catalog if isinstance(entry, dict)
            } if isinstance(catalog, list) else set()
        unknown_ids = sorted(set(skill_ids) - allowed_skill_ids)
        if unknown_ids:
            raise LLMError(
                "AgentSkillSelection が catalog にない skill_id を返しました: "
                + ", ".join(unknown_ids)
            )

    def _validate_agent_skill_material_selection(
        self,
        payload: dict[str, Any],
        *,
        selection_context: dict[str, Any] | None = None,
    ) -> None:
        _validate_exact_keys(
            payload,
            {"additional_skill_ids", "resource_reads", "reason_summary"},
            "AgentSkillMaterialSelection",
        )
        skill_ids = payload.get("additional_skill_ids")
        reads = payload.get("resource_reads")
        if (
            not isinstance(skill_ids, list)
            or not all(isinstance(value, str) and value.strip() for value in skill_ids)
            or len(skill_ids) != len(set(skill_ids))
            or not isinstance(reads, list)
            or not isinstance(payload.get("reason_summary"), str)
            or not payload["reason_summary"].strip()
        ):
            raise LLMError("AgentSkillMaterialSelection の値が不正です。")
        seen_reads: set[tuple[str, str]] = set()
        for read in reads:
            if not isinstance(read, dict) or set(read) != {"skill_id", "path"}:
                raise LLMError("AgentSkillMaterialSelection.resource_reads が不正です。")
            pair = (read.get("skill_id"), read.get("path"))
            if not all(isinstance(value, str) and value.strip() for value in pair) or pair in seen_reads:
                raise LLMError("AgentSkillMaterialSelection.resource_reads が不正です。")
            seen_reads.add(pair)

        if selection_context is None:
            return
        additional_candidates = selection_context.get("allowed_additional_skill_ids")
        if not isinstance(additional_candidates, list):
            additional_candidates = selection_context.get("additional_skill_candidates")
        allowed_additional_ids = {
            value
            for value in additional_candidates
            if isinstance(value, str)
        } if isinstance(additional_candidates, list) else set()
        active_ids = {
            str(entry.get("skill_id") or "").strip()
            for entry in selection_context.get("active_skills") or []
            if isinstance(entry, dict) and str(entry.get("skill_id") or "").strip()
        }
        invalid_additional = sorted(set(skill_ids) - allowed_additional_ids - active_ids)
        if invalid_additional:
            raise LLMError(
                "AgentSkillMaterialSelection が候補にない skill_id を返しました: "
                + ", ".join(invalid_additional)
            )

        resource_candidates = selection_context.get("allowed_resource_reads")
        if not isinstance(resource_candidates, list):
            resource_candidates = selection_context.get("resource_candidates")
        candidate_pairs = {
            (entry.get("skill_id"), entry.get("path"))
            for entry in resource_candidates
            if isinstance(entry, dict)
        } if isinstance(resource_candidates, list) else set()
        invalid_pairs = sorted(seen_reads - candidate_pairs)
        if invalid_pairs:
            raise LLMError(
                "AgentSkillMaterialSelection が候補にない resource を返しました: "
                + ", ".join(f"{skill_id}/{path}" for skill_id, path in invalid_pairs)
            )

    def generate_input_interpretation(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict],
        current_time: str,
        visual_observation_context: dict[str, Any] | None,
        activity_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = "input_interpretation"
        try:
            if self._is_mock_model_config(model_config):
                recall_hint = self.mock_client.generate_recall_hint(
                    model_config,
                    input_text,
                    recent_turns,
                    current_time,
                    persona_context=persona_context,
                    current_input=current_input,
                )
                answer_contract = self.mock_client.generate_answer_contract(
                    model_config,
                    input_text,
                    recall_hint,
                    current_time,
                    persona_context=persona_context,
                )
                answer_contract = normalize_answer_contract_payload(answer_contract)
                payload = {
                    "recall_hint": recall_hint,
                    "answer_contract": answer_contract,
                }
                debug_log(
                    "LLM",
                    (
                        f"{operation} done mode=mock focus={recall_hint.get('primary_recall_focus')} "
                        f"contract={answer_contract.get('contract')}"
                    ),
                    level="DEBUG",
                )
                return payload

            messages = build_input_interpretation_messages(
                persona_context=persona_context,
                current_input=current_input,
                recent_turns=recent_turns,
                current_time=current_time,
                visual_observation_context=visual_observation_context,
                activity_context=activity_context,
            )
            payload = self._generate_structured_payload(
                model_config=model_config,
                messages=messages,
                validator=self._validate_input_interpretation_contract,
                repair_prompt_builder=build_input_interpretation_repair_prompt,
                failure_message="InputInterpretation の生成に失敗しました。解析可能な応答が得られませんでした。",
                operation=operation,
            )
            recall_hint = normalize_recall_hint_payload(payload["recall_hint"])
            answer_contract = normalize_answer_contract_payload(payload["answer_contract"])
            return {
                "recall_hint": recall_hint,
                "answer_contract": answer_contract,
            }
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}", level="ERROR")
            raise

    def _validate_input_interpretation_contract(self, payload: dict[str, Any]) -> None:
        _validate_exact_keys(payload, {"recall_hint", "answer_contract"}, "InputInterpretation")
        recall_hint = payload["recall_hint"]
        if not isinstance(recall_hint, dict):
            raise LLMError("InputInterpretation.recall_hint は object である必要があります。")
        if not isinstance(payload["answer_contract"], dict):
            raise LLMError("InputInterpretation.answer_contract は object である必要があります。")
        validate_recall_hint_contract(normalize_recall_hint_payload(recall_hint))
        validate_answer_contract_contract(payload["answer_contract"])

    def generate_decision(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        context: DecisionContext,
    ) -> dict[str, Any]:
        operation = self._decision_operation_name(context)
        try:
            # モック経路
            if self._is_mock_model_config(model_config):
                payload = self.mock_client.generate_decision(
                    model_config=model_config,
                    persona_context=persona_context,
                    context=context,
                )
                self._validate_decision_contract_for_context(payload=payload, context=context)
                debug_log("LLM", f"{operation} done mode=mock kind={payload.get('kind')}", level="DEBUG")
                return payload

            # プロンプト構築
            messages = build_decision_messages(
                persona_context=persona_context,
                context=context,
            )

            return self._generate_structured_payload(
                model_config=model_config,
                messages=messages,
                validator=lambda payload: self._validate_decision_contract_for_context(
                    payload=payload,
                    context=context,
                ),
                repair_prompt_builder=lambda error: build_decision_repair_prompt(
                    error,
                    context.comparison_scope,
                ),
                failure_message="Decision の生成に失敗しました。解析可能な応答が得られませんでした。",
                operation=operation,
            )
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}", level="ERROR")
            raise

    def _validate_decision_contract_for_context(
        self,
        *,
        payload: dict[str, Any],
        context: DecisionContext,
    ) -> None:
        validate_decision_contract(
            payload,
            workspace_context=context.workspace_context if isinstance(context.workspace_context, dict) else None,
            initiative_context=context.initiative_context,
            comparison_scope=context.comparison_scope,
        )
        self._validate_decision_foreground_selection_refs(
            payload=payload,
            context=context,
        )
        self._validate_decision_autonomous_run_coordination(
            payload=payload,
            context=context,
        )
        if payload.get("kind") == "capability_request":
            self._validate_capability_request_for_context(
                request_payload=payload.get("capability_request"),
                capability_decision_view=context.capability_decision_view,
                label="Decision capability_request",
            )
        if isinstance(context.capability_result_context, dict):
            self._validate_decision_capability_result_context(
                payload=payload,
                capability_result_context=context.capability_result_context,
            )
        try:
            self._validate_decision_vision_capture_fresh_world_state_reuse(
                payload=payload,
                capability_decision_view=context.capability_decision_view,
                capability_result_context=context.capability_result_context,
            )
        except LLMError as exc:
            if context.trigger_kind != "user_message" and payload.get("kind") == "capability_request":
                self._coerce_decision_to_noop_for_fresh_visual_context_reuse(payload, exc)
                return
            raise

    def _validate_decision_foreground_selection_refs(
        self,
        *,
        payload: dict[str, Any],
        context: DecisionContext,
    ) -> None:
        workspace_context = context.workspace_context if isinstance(context.workspace_context, dict) else {}
        candidates = workspace_context.get("workspace_candidates")
        candidate_refs = {
            candidate["factor_ref"].strip()
            for candidate in candidates
            if isinstance(candidate, dict)
            and isinstance(candidate.get("factor_ref"), str)
            and candidate["factor_ref"].strip()
        } if isinstance(candidates, list) else set()
        foreground_selection = payload.get("foreground_selection")
        if not isinstance(foreground_selection, dict):
            return
        selected_refs: list[str] = []
        primary_factor_ref = foreground_selection.get("primary_factor_ref")
        if isinstance(primary_factor_ref, str):
            selected_refs.append(primary_factor_ref.strip())
        supporting_factor_refs = foreground_selection.get("supporting_factor_refs")
        if isinstance(supporting_factor_refs, list):
            selected_refs.extend(
                factor_ref.strip()
                for factor_ref in supporting_factor_refs
                if isinstance(factor_ref, str)
            )
        suppressed_factors = foreground_selection.get("suppressed_factors")
        if isinstance(suppressed_factors, list):
            selected_refs.extend(
                item.get("factor_ref", "").strip()
                for item in suppressed_factors
                if isinstance(item, dict) and isinstance(item.get("factor_ref"), str)
            )
        missing_refs = sorted({factor_ref for factor_ref in selected_refs if factor_ref not in candidate_refs})
        if missing_refs:
            raise LLMError(
                "Decision foreground_selection には WorkspaceContext.workspace_candidates[].factor_ref "
                f"に含まれる参照だけを指定してください。不明な参照={','.join(missing_refs)}"
            )
        if candidate_refs and primary_factor_ref is None:
            raise LLMError(
                "WorkspaceContext.workspace_candidates があるときは "
                "foreground_selection.primary_factor_ref を 1 件指定してください。"
            )

    def _validate_decision_autonomous_run_coordination(
        self,
        *,
        payload: dict[str, Any],
        context: DecisionContext,
    ) -> None:
        if payload.get("kind") != "autonomous_run":
            return
        autonomous_run = payload.get("autonomous_run")
        if not isinstance(autonomous_run, dict):
            return
        coordination = autonomous_run.get("coordination")
        if not isinstance(coordination, dict):
            return
        target_run_ids = coordination.get("target_run_ids")
        if not isinstance(target_run_ids, list) or not target_run_ids:
            return
        summaries_by_id = {
            str(summary.get("run_id") or "").strip(): summary
            for summary in context.autonomous_run_summaries or []
            if isinstance(summary, dict)
        }
        for run_id in target_run_ids:
            target = summaries_by_id.get(str(run_id).strip())
            if not isinstance(target, dict):
                raise LLMError(
                    "Decision autonomous_run.coordination.target_run_ids には "
                    "AutonomousRunSummaries に含まれる run_id だけを指定してください。"
                )

    def generate_autonomous_step(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        context: AutonomousStepContext,
    ) -> dict[str, Any]:
        operation = "autonomous_step"
        try:
            if self._is_mock_model_config(model_config):
                payload = self.mock_client.generate_autonomous_step(
                    model_config=model_config,
                    persona_context=persona_context,
                    context=context,
                )
                self._validate_autonomous_step_contract_for_context(payload=payload, context=context)
                debug_log(
                    "LLM",
                    (
                        f"{operation} done mode=mock action={payload.get('action', {}).get('kind')} "
                        f"transition={payload.get('transition', {}).get('kind')}"
                    ),
                    level="DEBUG",
                )
                return payload

            messages = build_autonomous_step_messages(
                persona_context=persona_context,
                context=context,
            )
            payload = self._generate_structured_payload(
                model_config=model_config,
                messages=messages,
                validator=lambda value: self._validate_autonomous_step_contract_for_context(
                    payload=value,
                    context=context,
                ),
                repair_prompt_builder=build_autonomous_step_repair_prompt,
                failure_message="AutonomousStep の生成に失敗しました。解析可能な応答が得られませんでした。",
                operation=operation,
            )
            return payload
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}", level="ERROR")
            raise

    def _coerce_decision_to_noop_for_fresh_visual_context_reuse(
        self,
        payload: dict[str, Any],
        exc: LLMError,
    ) -> None:
        reason_summary = str(exc).replace("\n", " ").strip()
        if len(reason_summary) > 220:
            reason_summary = reason_summary[:219] + "…"
        summary = reason_summary or "同じ vision_source_id の新鮮な visual_context を判断根拠に使う。"
        existing_targets = []
        current_stances = payload.get("target_stances")
        if isinstance(current_stances, list):
            existing_targets = [
                item.get("target")
                for item in current_stances
                if isinstance(item, dict) and item.get("target") in {"outward_speech", "self_activity"}
            ]
        payload.update(
            {
                "kind": "noop",
                "reason_code": "fresh_visual_context_reuse_noop",
                "reason_summary": summary,
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": None,
                "autonomous_run": None,
                "target_stances": build_decision_target_stances_for_kind(
                    "noop",
                    required_targets=existing_targets or ("outward_speech",),
                    reason_summary=summary,
                ),
            }
        )
        debug_log(
            "LLM",
            "decision coerced_to_noop reason=fresh_visual_context_reuse_non_user_trigger",
        )

    def _validate_decision_vision_capture_fresh_world_state_reuse(
        self,
        *,
        payload: dict[str, Any],
        capability_decision_view: list[dict[str, Any]] | None,
        capability_result_context: dict[str, Any] | None = None,
    ) -> None:
        if payload.get("kind") != "capability_request":
            return
        request_payload = payload.get("capability_request")
        request_capability_id = (
            request_payload.get("capability_id")
            if isinstance(request_payload, dict)
            else None
        )
        if not isinstance(request_capability_id, str) or not request_capability_id.strip():
            return
        normalized_request_capability_id = request_capability_id.strip()
        if normalized_request_capability_id != "vision.capture":
            return
        if self._capability_result_context_allows_same_vision_source_capture(
            request_payload=request_payload,
            capability_result_context=capability_result_context,
        ):
            return
        capability_entry = self._capability_decision_view_entry(
            capability_decision_view=capability_decision_view,
            capability_id=normalized_request_capability_id,
        )
        if not isinstance(capability_entry, dict):
            return
        self._validate_vision_capture_fresh_world_state_reuse(
            request_payload=request_payload,
            capability_entry=capability_entry,
        )

    def _validate_vision_capture_fresh_world_state_reuse(
        self,
        *,
        request_payload: dict[str, Any],
        capability_entry: dict[str, Any],
    ) -> None:
        input_payload = request_payload.get("input")
        if not isinstance(input_payload, dict):
            return
        requested_source_id = input_payload.get("vision_source_id")
        if not isinstance(requested_source_id, str) or not requested_source_id.strip():
            return
        fresh_sources = capability_entry.get("fresh_world_state_by_vision_source")
        if not isinstance(fresh_sources, list):
            return
        for fresh_source in fresh_sources:
            if not isinstance(fresh_source, dict):
                continue
            source_id = fresh_source.get("vision_source_id")
            if source_id != requested_source_id.strip():
                continue
            summary_text = fresh_source.get("summary_text")
            age_label = fresh_source.get("age_label")
            state_summary = ""
            if isinstance(age_label, str) and age_label.strip():
                state_summary += f" age_label={age_label.strip()}"
            if isinstance(summary_text, str) and summary_text.strip():
                state_summary += f" summary={summary_text.strip()}"
            raise LLMError(
                "CapabilityDecisionView の vision.capture には "
                f"vision_source_id={requested_source_id.strip()} の新鮮な visual_context があります。{state_summary}"
                "判断入力に含まれる同じ vision_source_id の現在状態を再取得する capability_request は不正です。"
                "既存の foreground_world_state を使って speech / noop / pending_intent を返してください。"
            )

    def _capability_decision_view_entry(
        self,
        *,
        capability_decision_view: list[dict[str, Any]] | None,
        capability_id: str,
    ) -> dict[str, Any] | None:
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id.strip() == capability_id.strip():
                return item
        return None

    def _validate_autonomous_step_contract_for_context(
        self,
        *,
        payload: dict[str, Any],
        context: AutonomousStepContext,
    ) -> None:
        validate_autonomous_step_contract(payload)
        action = payload.get("action")
        if not isinstance(action, dict) or action.get("kind") != "capability_request":
            return
        self._validate_capability_request_for_context(
            request_payload=action.get("capability_request"),
            capability_decision_view=context.capability_decision_view,
            label="AutonomousStep action.capability_request",
        )

    def _validate_capability_request_for_context(
        self,
        *,
        request_payload: Any,
        capability_decision_view: list[dict[str, Any]] | None,
        label: str,
    ) -> None:
        if not isinstance(request_payload, dict):
            return
        capability_id = request_payload.get("capability_id")
        input_payload = request_payload.get("input")
        if not isinstance(capability_id, str) or not capability_id.strip() or not isinstance(input_payload, dict):
            return
        normalized_capability_id = capability_id.strip()
        capability_entry = self._capability_decision_view_entry(
            capability_decision_view=capability_decision_view,
            capability_id=normalized_capability_id,
        )
        if capability_entry is None:
            raise LLMError(
                f"{label}.capability_id={normalized_capability_id} は "
                "CapabilityDecisionView に存在しません。"
                "CapabilityDecisionView の available=true の id だけを指定してください。"
            )
        if capability_entry.get("available") is not True:
            unavailable_reason = capability_entry.get("unavailable_reason")
            reason_suffix = (
                f" unavailable_reason={unavailable_reason}"
                if isinstance(unavailable_reason, str) and unavailable_reason.strip()
                else ""
            )
            raise LLMError(
                f"{label}.capability_id={normalized_capability_id} は現在実行できません。"
                f"{reason_suffix} CapabilityDecisionView の available=true の能力を選んでください。"
            )

        manifest = capability_manifests().get(normalized_capability_id)
        if not isinstance(manifest, dict):
            raise LLMError(f"{label}.capability_id={normalized_capability_id} の manifest がありません。")
        try:
            validate_capability_payload(
                payload=input_payload,
                schema=manifest.get("input_schema"),
                label=f"{label}.input",
            )
        except ValueError as exc:
            raise LLMError(str(exc)) from exc

        if normalized_capability_id == "mcp.call_tool":
            self._validate_mcp_call_tool_request_for_context(
                input_payload=input_payload,
                capability_entry=capability_entry,
                label=label,
            )
        if normalized_capability_id in {"vision.capture", "camera.ptz"}:
            self._validate_vision_target_for_context(
                capability_id=normalized_capability_id,
                input_payload=input_payload,
                capability_entry=capability_entry,
                label=label,
            )

    def _validate_mcp_call_tool_request_for_context(
        self,
        *,
        input_payload: dict[str, Any],
        capability_entry: dict[str, Any],
        label: str,
    ) -> None:
        mcp_server_id = str(input_payload.get("mcp_server_id") or "").strip()
        tool_name = str(input_payload.get("tool_name") or "").strip()
        selected_server = next(
            (
                server
                for server in capability_entry.get("mcp_servers", [])
                if isinstance(server, dict)
                and server.get("mcp_server_id") == mcp_server_id
                and server.get("available") is True
            ),
            None,
        )
        if selected_server is None:
            raise LLMError(
                f"{label}.input.mcp_server_id={mcp_server_id} は現在利用可能な MCP server ではありません。"
            )
        selected_tool = next(
            (
                tool
                for tool in selected_server.get("tools", [])
                if isinstance(tool, dict) and tool.get("name") == tool_name
            ),
            None,
        )
        if selected_tool is None:
            raise LLMError(
                f"{label}.input.tool_name={tool_name} は MCP server={mcp_server_id} の catalog にありません。"
            )
        try:
            validate_capability_payload(
                payload=input_payload.get("arguments"),
                schema=selected_tool.get("input_schema"),
                label=f"{label}.input.arguments.{tool_name}",
            )
        except ValueError as exc:
            raise LLMError(str(exc)) from exc

    def _validate_vision_target_for_context(
        self,
        *,
        capability_id: str,
        input_payload: dict[str, Any],
        capability_entry: dict[str, Any],
        label: str,
    ) -> None:
        vision_source_id = str(input_payload.get("vision_source_id") or "").strip()
        selected_source = next(
            (
                source
                for source in capability_entry.get("vision_sources", [])
                if isinstance(source, dict)
                and source.get("vision_source_id") == vision_source_id
                and source.get("available") is True
            ),
            None,
        )
        if selected_source is None:
            raise LLMError(
                f"{label}.input.vision_source_id={vision_source_id} は現在利用可能な対象ではありません。"
            )
        if capability_id != "camera.ptz":
            return
        operation = input_payload.get("operation")
        amount = input_payload.get("amount")
        if operation not in selected_source.get("supported_operations", []):
            raise LLMError(f"{label}.input.operation={operation} は対象cameraで利用できません。")
        if amount not in selected_source.get("supported_amounts", []):
            raise LLMError(f"{label}.input.amount={amount} は対象cameraで利用できません。")

    def _validate_decision_capability_result_context(
        self,
        *,
        payload: dict[str, Any],
        capability_result_context: dict[str, Any],
    ) -> None:
        if payload.get("kind") != "capability_request":
            return
        request_payload = payload.get("capability_request")
        request_capability_id = (
            request_payload.get("capability_id")
            if isinstance(request_payload, dict)
            else None
        )
        if not isinstance(request_capability_id, str) or not request_capability_id.strip():
            return
        allowed_capability_ids = capability_result_context.get("allowed_followup_capability_ids")
        if not isinstance(allowed_capability_ids, list):
            allowed_capability_ids = []
        normalized_allowed = {
            capability_id.strip()
            for capability_id in allowed_capability_ids
            if isinstance(capability_id, str) and capability_id.strip()
        }
        normalized_request_capability_id = request_capability_id.strip()
        if normalized_request_capability_id in normalized_allowed:
            self._validate_decision_capability_result_followup_constraints(
                request_payload=request_payload,
                capability_result_context=capability_result_context,
                request_capability_id=normalized_request_capability_id,
            )
            return
        source_capability_id = capability_result_context.get("source_capability_id")
        if not isinstance(source_capability_id, str) or not source_capability_id.strip():
            source_capability_id = "unknown"
        allowed_summary = ", ".join(sorted(normalized_allowed)) if normalized_allowed else "なし"
        raise LLMError(
            "CapabilityResultContext は "
            f"source_capability_id={source_capability_id} の follow-up です。"
            f"allowed_followup_capability_ids={allowed_summary} に含まれない "
            f"{request_capability_id.strip()} の capability_request は不正です。"
            "受け取った result に基づく speech / noop / pending_intent を返してください。"
        )

    def _validate_decision_capability_result_followup_constraints(
        self,
        *,
        request_payload: dict[str, Any],
        capability_result_context: dict[str, Any],
        request_capability_id: str,
    ) -> None:
        constraints = capability_result_context.get("followup_constraints")
        if not isinstance(constraints, list):
            return
        input_payload = request_payload.get("input")
        if not isinstance(input_payload, dict):
            input_payload = {}
        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            if constraint.get("capability_id") != request_capability_id:
                continue
            constraint_kind = constraint.get("constraint")
            if constraint_kind == "same_vision_source_id":
                expected_source_id = constraint.get("vision_source_id")
                actual_source_id = input_payload.get("vision_source_id")
                if (
                    isinstance(expected_source_id, str)
                    and expected_source_id.strip()
                    and isinstance(actual_source_id, str)
                    and actual_source_id.strip() == expected_source_id.strip()
                ):
                    continue
                raise LLMError(
                    "CapabilityResultContext の followup_constraints は "
                    f"{request_capability_id} に same_vision_source_id を要求しています。"
                    f"vision_source_id={expected_source_id} と異なる capability_request は不正です。"
                )
            if constraint_kind != "exclude_completed_mcp_tool":
                continue
            completed_server_id = constraint.get("mcp_server_id")
            completed_tool_name = constraint.get("tool_name")
            requested_server_id = input_payload.get("mcp_server_id")
            requested_tool_name = input_payload.get("tool_name")
            if not (
                isinstance(completed_server_id, str)
                and completed_server_id.strip()
                and isinstance(completed_tool_name, str)
                and completed_tool_name.strip()
                and isinstance(requested_server_id, str)
                and requested_server_id.strip()
                and isinstance(requested_tool_name, str)
                and requested_tool_name.strip()
                and requested_server_id.strip() == completed_server_id.strip()
                and requested_tool_name.strip() == completed_tool_name.strip()
            ):
                continue
            raise LLMError(
                "CapabilityResultContext の followup_constraints は "
                f"今回完了した MCP tool {completed_server_id.strip()}/{completed_tool_name.strip()} "
                "の再実行を許可しません。"
                "同じ tool の会話 follow-up 再実行はできません。"
                "残りが同じ作用なら autonomous_run、"
                "未完了の別手順ならその tool、"
                "向きが果たされていれば speech または noop を選んでください。"
            )

    def _capability_result_context_allows_same_vision_source_capture(
        self,
        *,
        request_payload: dict[str, Any],
        capability_result_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(capability_result_context, dict):
            return False
        if capability_result_context.get("source_capability_id") != "camera.ptz":
            return False
        if request_payload.get("capability_id") != "vision.capture":
            return False
        constraints = capability_result_context.get("followup_constraints")
        if not isinstance(constraints, list):
            return False
        input_payload = request_payload.get("input")
        if not isinstance(input_payload, dict):
            return False
        requested_source_id = input_payload.get("vision_source_id")
        if not isinstance(requested_source_id, str) or not requested_source_id.strip():
            return False
        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            if constraint.get("capability_id") != "vision.capture":
                continue
            if constraint.get("constraint") != "same_vision_source_id":
                continue
            expected_source_id = constraint.get("vision_source_id")
            if isinstance(expected_source_id, str) and requested_source_id.strip() == expected_source_id.strip():
                return True
        return False

    def generate_speech(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        context: SpeechContext,
    ) -> dict[str, Any]:
        operation = "speech"
        try:
            # モック経路
            if self._is_mock_model_config(model_config):
                payload = self.mock_client.generate_speech(
                    model_config=model_config,
                    persona_context=persona_context,
                    context=context,
                )
                debug_log("LLM", f"{operation} done mode=mock speech_chars={len(payload.get('speech_text', ''))}", level="DEBUG")
                return payload

            # プロンプト構築
            messages = build_speech_messages(
                persona_context=persona_context,
                context=context,
            )

            # 補完
            content = complete_text(model_config=model_config, messages=messages)
            speech_text = content.strip()
            if not speech_text:
                raise LLMError("Speech の生成結果が空でした。")

            # payload作成
            payload = {
                "speech_text": speech_text,
                "speech_style_notes": f"model={model_config.get('model')}",
                "confidence_note": "litellm_model",
            }
            debug_log(
                "LLM",
                (
                    f"{operation} done model={self._debug_model(model_config)} "
                    f"response_chars={len(content)} speech_chars={len(speech_text)}"
                ),
                level="DEBUG",
            )
            return payload
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}", level="ERROR")
            raise

    def generate_disclosure_review(
        self,
        *,
        model_config: dict,
        review_context: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "disclosure_review"
        if self._is_mock_model_config(model_config):
            payload = {
                "outcome": "allow",
                "speech_text": review_context["candidate_speech"],
                "reason_code": "mock_allow",
            }
            validate_disclosure_review_contract(payload)
            return payload
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_disclosure_review_messages(review_context=review_context),
            validator=validate_disclosure_review_contract,
            repair_prompt_builder=build_disclosure_review_repair_prompt,
            failure_message="DisclosureReview の生成に失敗しました。",
            operation=operation,
        )

    def generate_pre_send_check(
        self,
        *,
        model_config: dict,
        review_context: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "pre_send_check"
        # 外部送信の安全境界では、開発用 mock を暗黙の許可として扱わない。
        if self._is_mock_model_config(model_config):
            raise LLMError("PreSendCheck requires an explicit reviewer test double for mock models.")
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_pre_send_check_messages(review_context=review_context),
            validator=validate_pre_send_check_contract,
            repair_prompt_builder=build_pre_send_check_repair_prompt,
            failure_message="PreSendCheck の生成に失敗しました。",
            operation=operation,
        )

    def generate_autonomous_completion_review(
        self,
        *,
        model_config: dict,
        review_context: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "autonomous_completion_review"
        if self._is_mock_model_config(model_config):
            payload = {
                "outcome": "allow_complete",
                "reason_summary": "mock model は complete 候補を許可する。",
            }
            validate_autonomous_completion_review_contract(payload)
            debug_log(
                "LLM",
                f"{operation} done mode=mock outcome={payload['outcome']}",
                level="DEBUG",
            )
            return payload
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_autonomous_completion_review_messages(
                review_context=review_context,
            ),
            validator=validate_autonomous_completion_review_contract,
            repair_prompt_builder=build_autonomous_completion_review_repair_prompt,
            failure_message="AutonomousCompletionReview の生成に失敗しました。",
            operation=operation,
        )

    def generate_memory_interpretation(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        input_text: str,
        recall_hint: dict,
        decision: dict,
        speech_text: str | None,
        memory_context: dict[str, Any] | None,
        current_time: str,
        correction_targets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        operation = "memory_interpretation"
        # モック経路
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_memory_interpretation(
                model_config,
                input_text,
                recall_hint,
                decision,
                speech_text,
                memory_context,
                persona_context=persona_context,
                correction_targets=correction_targets,
            )
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_memory_interpretation_messages(
            persona_context=persona_context,
            input_text=input_text,
            recall_hint=recall_hint,
            decision=decision,
            speech_text=speech_text,
            memory_context=memory_context,
            current_time=current_time,
            correction_targets=correction_targets,
        )
        payload = self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_memory_interpretation_contract,
            repair_prompt_builder=build_memory_interpretation_repair_prompt,
            failure_message="MemoryInterpretation の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )
        if not correction_targets:
            payload.setdefault("correction_status", "no_correction")
            payload.setdefault("selected_targets", [])
        return payload

    def generate_memory_reflection_summary(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "memory_reflection_summary"
        # モック経路
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_memory_reflection_summary(
                model_config,
                self._source_pack_with_persona_context(source_pack, persona_context),
            )
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_memory_reflection_summary_messages(
            persona_context=persona_context,
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_memory_reflection_summary_contract,
            repair_prompt_builder=build_memory_reflection_summary_repair_prompt,
            failure_message="MemoryReflectionSummary の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )

    def generate_event_evidence(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "event_evidence"
        source_pack = self._source_pack_with_persona_context(source_pack, persona_context)
        # モック経路
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_event_evidence(model_config, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_event_evidence_messages(
            persona_context=persona_context,
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_event_evidence_contract,
            repair_prompt_builder=build_event_evidence_repair_prompt,
            failure_message="EventEvidence の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_recall_pack_selection(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "recall_pack_selection"
        source_pack = self._source_pack_with_persona_context(source_pack, persona_context)
        # モック経路
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_recall_pack_selection(model_config, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_recall_pack_selection_messages(
            persona_context=persona_context,
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=lambda payload: validate_recall_pack_selection_contract(payload, source_pack=source_pack),
            repair_prompt_builder=build_recall_pack_selection_repair_prompt,
            failure_message="RecallPackSelection の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_pending_intent_selection(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "pending_intent_selection"
        source_pack = self._source_pack_with_persona_context(source_pack, persona_context)
        # モック経路
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_pending_intent_selection(model_config, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_pending_intent_selection_messages(
            persona_context=persona_context,
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=lambda payload: validate_pending_intent_selection_contract(payload, source_pack=source_pack),
            repair_prompt_builder=build_pending_intent_selection_repair_prompt,
            failure_message="PendingIntentSelection の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_initiative_entry_check(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "initiative_entry_check"
        source_pack = self._source_pack_with_persona_context(source_pack, persona_context)
        # モック経路
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_initiative_entry_check(model_config, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_initiative_entry_check_messages(
            persona_context=persona_context,
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_initiative_entry_check_contract,
            repair_prompt_builder=build_initiative_entry_check_repair_prompt,
            failure_message="InitiativeEntryCheck の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_world_state(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: WorldStateSourcePack,
    ) -> dict[str, Any]:
        operation = "world_state"
        source_pack.persona_context = persona_context.to_prompt_payload()
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_world_state(model_config, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        messages = build_world_state_messages(
            persona_context=persona_context,
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=lambda payload: validate_world_state_contract(payload, source_pack=source_pack),
            repair_prompt_builder=build_world_state_repair_prompt,
            failure_message="WorldState の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_activity_state(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "activity_state"
        source_pack = self._source_pack_with_persona_context(source_pack, persona_context)
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_activity_state(model_config, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        messages = build_activity_state_messages(
            persona_context=persona_context,
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_activity_state_contract,
            repair_prompt_builder=build_activity_state_repair_prompt,
            failure_message="ActivityState の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_visual_observation_summary(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        source_pack: dict[str, Any],
        images: list[str],
    ) -> dict[str, Any]:
        operation = "visual_observation"
        source_pack = self._source_pack_with_persona_context(source_pack, persona_context)
        if self._is_mock_model_config(model_config):
            payload = self.mock_client.generate_visual_observation_summary(
                model_config,
                source_pack,
                images,
            )
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        messages = build_visual_observation_messages(
            persona_context=persona_context,
            source_pack=source_pack,
            images=images,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_visual_observation_contract,
            repair_prompt_builder=build_visual_observation_repair_prompt,
            failure_message="VisualObservation の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_embeddings(
        self,
        *,
        model_config: dict,
        texts: list[str],
    ) -> list[list[float]]:
        # 空
        if not texts:
            return []

        # 次元
        embedding_dimension = self._embedding_dimension(model_config)
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise LLMError("embedding_dimension は正の整数である必要があります。")

        # モック経路
        if self._is_mock_model_config(model_config):
            vectors = self.mock_client.generate_embeddings(model_config, texts, embedding_dimension)
            debug_log("LLM", f"embeddings done mode=mock vectors={len(vectors)}", level="DEBUG")
            return vectors

        # model差分込みの transport へ委譲する。
        vectors = transport_generate_embeddings(
            model_config=model_config,
            texts=texts,
            expected_dimension=embedding_dimension,
        )
        debug_log(
            "LLM",
            f"embeddings done model={self._debug_model(model_config)} vectors={len(vectors)}",
            level="DEBUG",
        )
        return vectors

    def _source_pack_with_persona_context(
        self,
        source_pack: dict[str, Any],
        persona_context: PersonaContext,
    ) -> dict[str, Any]:
        payload = dict(source_pack)
        payload["persona_context"] = persona_context.to_prompt_payload()
        return payload

    # 設定補助
    def _debug_model(self, model_config: dict) -> str:
        # 秘密情報を含まない model 名だけを出す。
        model = model_config.get("model")
        if not isinstance(model, str) or not model.strip():
            return "-"
        return model.strip()

    def _debug_error(self, exc: BaseException) -> str:
        # 長い応答本文をログへ出しすぎない。
        message = str(exc).replace("\n", " ").strip()
        return self._debug_clip(message, 240)

    def _debug_payload_keys(self, payload: dict[str, Any]) -> str:
        # payload の中身ではなくキーだけを出す。
        keys = sorted(str(key) for key in payload.keys())[:8]
        return ",".join(keys) if keys else "-"

    def _decision_operation_name(self, context: DecisionContext) -> str:
        scope = context.comparison_scope
        if scope in {"self_activity", "outward_speech"}:
            return f"decision:{scope}"
        return "decision"

    def _debug_clip(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _debug_rejected_content(self, content: str) -> str:
        flattened = content.replace("\r", " ").replace("\n", " ").strip()
        return self._debug_clip(flattened, DEBUG_REJECTED_TEXT_LIMIT)

    def _debug_rejected_payload(self, payload: dict[str, Any]) -> str:
        compact = self._compact_debug_value(payload)
        if isinstance(compact, dict):
            ordered: dict[str, Any] = {}
            for key in DEBUG_REJECTED_PAYLOAD_KEY_ORDER:
                if key in compact:
                    ordered[key] = compact[key]
            for key, value in compact.items():
                if key not in ordered:
                    ordered[key] = value
            compact = ordered
        encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        return self._debug_clip(encoded, DEBUG_REJECTED_TEXT_LIMIT)

    def _compact_debug_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for key, item in value.items():
                name = str(key)
                if name.lower() in DEBUG_REDACTED_PAYLOAD_KEYS:
                    compact[name] = "[redacted]"
                    continue
                compact[name] = self._compact_debug_value(item)
            return compact
        if isinstance(value, list):
            return [self._compact_debug_value(item) for item in value[:DEBUG_REJECTED_LIST_LIMIT]]
        if isinstance(value, str):
            flattened = value.replace("\r", " ").replace("\n", " ").strip()
            return self._debug_clip(flattened, DEBUG_REJECTED_STRING_LIMIT)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self._debug_clip(str(value), DEBUG_REJECTED_STRING_LIMIT)

    def _is_mock_model_config(self, model_config: dict) -> bool:
        # model=mock* は開発用の内蔵ロジックへ切り替える。
        model = model_config.get("model")
        return isinstance(model, str) and model.strip().startswith("mock")

    def _embedding_dimension(self, model_config: dict) -> int:
        return model_config.get("embedding_dimension")

    def _generate_structured_payload(
        self,
        *,
        model_config: dict,
        messages: list[dict[str, Any]],
        validator: Callable[[dict[str, Any]], None],
        repair_prompt_builder: Callable[[str], str],
        failure_message: str,
        wrap_validation_error: bool = False,
        operation: str = "structured",
    ) -> dict[str, Any]:
        last_error: LLMError | None = None
        attempt_messages = list(messages)
        for attempt in range(2):
            content = complete_text(model_config=model_config, messages=attempt_messages)
            try:
                payload = parse_json_object(content)
                try:
                    validator(payload)
                    debug_log(
                        "LLM",
                        (
                            f"{operation} done model={self._debug_model(model_config)} "
                            f"attempt={attempt + 1} response_chars={len(content)} "
                            f"keys={self._debug_payload_keys(payload)}"
                        ),
                        level="DEBUG",
                    )
                    return payload
                except LLMError as exc:
                    last_error = LLMContractError(str(exc)) if wrap_validation_error else exc
                    debug_log(
                        "LLM",
                        (
                            f"{operation} validation_failed attempt={attempt + 1} "
                            f"error={self._debug_error(last_error)} "
                            f"payload={self._debug_rejected_payload(payload)}"
                        ),
                        level="WARNING",
                    )
            except LLMError as exc:
                last_error = exc
                debug_log(
                    "LLM",
                    (
                        f"{operation} parse_failed attempt={attempt + 1} "
                        f"error={self._debug_error(exc)} "
                        f"content={self._debug_rejected_content(content)}"
                    ),
                    level="WARNING",
                )

            if attempt >= 1:
                if last_error is not None:
                    raise last_error
                raise LLMError(failure_message)

            attempt_messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": content,
                },
                {
                    "role": "user",
                    "content": repair_prompt_builder(str(last_error)),
                },
            ]

        if last_error is not None:
            debug_log("LLM", f"{operation} failed error={self._debug_error(last_error)}", level="ERROR")
            raise last_error
        debug_log("LLM", f"{operation} failed error={failure_message}", level="ERROR")
        raise LLMError(failure_message)
