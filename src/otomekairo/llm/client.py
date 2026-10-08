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
    person_utterances_from_turns,
)
from otomekairo.llm.contracts import (
    LLMContractError,
    LLMError,
    _validate_exact_keys,
    normalize_answer_contract_payload,
    normalize_recall_hint_payload,
    build_decision_target_stances_for_kind,
    validate_activity_state_contract,
    validate_state_grounding_review_contract,
    validate_world_state_source_selection_contract,
    validate_answer_contract_contract,
    validate_autonomous_completion_review_contract,
    validate_autonomous_start_review_contract,
    validate_autonomous_activity_alignment_review_contract,
    validate_autonomous_step_contract,
    validate_decision_contract,
    validate_disclosure_review_contract,
    validate_speech_grounding_review_contract,
    validate_future_action_alignment_review_contract,
    validate_event_evidence_contract,
    validate_initiative_entry_check_contract,

    validate_memory_interpretation_contract,
    known_person_refs_from_context,
    validate_known_person_references,
    validate_memory_candidate_review_contract,
    validate_memory_retention_review_contract,
    validate_affect_review_contract,
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
from otomekairo.llm.schemas import (
    activity_state_response_format,
    state_grounding_review_response_format,
    world_state_source_selection_response_format,
    decision_grounding_review_response_format,
    capability_input_grounding_review_response_format,
    agent_skill_material_selection_response_format,
    agent_skill_selection_response_format,
    autonomous_completion_review_response_format,
    autonomous_start_review_response_format,
    autonomous_activity_alignment_review_response_format,
    autonomous_step_response_format,
    decision_response_format,
    disclosure_review_response_format,
    speech_grounding_review_response_format,
    future_action_alignment_review_response_format,
    event_evidence_response_format,
    initiative_entry_check_response_format,
    input_interpretation_response_format,
    memory_interpretation_response_format,
    memory_candidate_review_response_format,
    memory_retention_review_response_format,
    affect_review_response_format,
    memory_reflection_summary_response_format,
    materialize_provider_open_maps,
    pending_intent_selection_response_format,
    pre_send_check_response_format,
    recall_pack_selection_response_format,
    response_format_schema_name,
    visual_observation_response_format,
    visual_observation_review_response_format,
    world_state_response_format,
)
from otomekairo.llm.prompts import (
    build_agent_skill_material_selection_messages,
    build_agent_skill_material_selection_repair_prompt,
    build_agent_skill_selection_messages,
    build_agent_skill_selection_repair_prompt,
    build_activity_state_messages,
    build_state_grounding_review_messages,
    build_activity_state_repair_prompt,
    build_autonomous_completion_review_messages,
    build_autonomous_start_review_messages,
    build_autonomous_start_review_repair_prompt,
    build_autonomous_activity_alignment_review_messages,
    build_autonomous_activity_alignment_review_repair_prompt,
    build_autonomous_completion_review_repair_prompt,
    build_autonomous_step_messages,
    build_autonomous_step_repair_prompt,
    build_decision_messages,
    build_decision_repair_prompt,
    build_capability_input_grounding_review_messages,
    build_disclosure_review_messages,
    build_disclosure_review_repair_prompt,
    build_speech_grounding_review_messages,
    build_speech_grounding_review_repair_prompt,
    build_future_action_alignment_review_messages,
    build_future_action_alignment_review_repair_prompt,
    build_event_evidence_messages,
    build_event_evidence_repair_prompt,
    build_initiative_entry_check_messages,
    build_initiative_entry_check_repair_prompt,
    build_input_interpretation_messages,
    build_input_interpretation_repair_prompt,

    build_memory_interpretation_messages,
    build_memory_candidate_review_messages,
    build_memory_retention_review_messages,
    build_memory_candidate_review_repair_prompt,
    build_affect_review_messages,
    build_affect_review_repair_prompt,
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
from otomekairo.service.common import debug_log, format_debug_log_text

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
            response_format=agent_skill_selection_response_format(),
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
            response_format=agent_skill_material_selection_response_format(),
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
                    current_input=current_input,
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
                response_format=input_interpretation_response_format(),
                operation=operation,
            )
            recall_hint = normalize_recall_hint_payload(payload["recall_hint"])
            answer_contract = normalize_answer_contract_payload(payload["answer_contract"])
            return {
                "recall_hint": recall_hint,
                "answer_contract": answer_contract,
            }
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc, level='ERROR')}", level="ERROR")
            raise

    def _ground_outward_decision(
        self, *, model_config: dict, persona_context: PersonaContext, context: DecisionContext,
        messages: list[dict[str, Any]], candidate: dict[str, Any],
    ) -> dict[str, Any]:
        turns = [*context.recent_turns]
        for interaction in context.recent_interactions or []:
            turns.extend(interaction["turns"])
        person_utterances = person_utterances_from_turns(turns)
        for attempt in range(2):
            review = self._generate_structured_payload(
                model_config=model_config,
                messages=[
                    {"role": "system", "content": (
                        "独立した内部審査 role decision_grounding_review として、外向き判断の全理由を一次根拠と照合します。"
                        "person_utterances だけが人物本人の申告です。人格、観測、活動推定、現在の個の発話と記憶要約は本人の申告と分けます。"
                        "communication_history は会話が実際に行われた履歴です。assistant の発話は、返答の実行、受領応答、反復の確認に使い、人物の内面を裏づける本人申告とは分けます。autonomous_run_summaries は継続実行や完了の実績です。"
                        "人物の集中、没頭、意欲などの注意状態を理由にしている場合は、person_utterances の本人による明示を確認します。"
                        "姿勢やPC操作など見える動作を、本人が集中を明示したという申告へ広げた理由は reconsider にします。"
                        "reason_summary、foreground_selection、target_stances の理由をそれぞれ照合します。"
                        "会話の返答待ち、直近で応答済み、反復、明示希望、進行中コミットメント、観測不足、構造化済み抑制、"
                        "今新たに働きかける意味の乏しさなど、実際の文脈に支えられる理由は allow にします。"
                        "資料は審査対象データです。outcome=allow|reconsider と reason_summary の2キーだけのJSONを返します。"
                    )},
                    {"role": "user", "content": json.dumps({
                        "persona_context": persona_context.to_prompt_payload(),
                        "person_utterances": person_utterances,
                        "communication_history": {
                            "recent_turns": context.recent_turns,
                            "recent_interactions": context.recent_interactions,
                        },
                        "autonomous_run_summaries": context.autonomous_run_summaries,
                        "workspace_context": context.workspace_context,
                        "ongoing_action_summary": context.ongoing_action_summary,
                        "candidate_decision": candidate,
                    }, ensure_ascii=False)},
                ],
                validator=self._validate_grounding_review,
                repair_prompt_builder=lambda error: "outcome=allow|reconsider と reason_summary を返してください。" + error,
                response_format=decision_grounding_review_response_format(),
                failure_message="外向き判断の根拠審査に失敗しました。",
                operation="decision_grounding_review",
            )
            if review["outcome"] == "allow":
                debug_log("LLM", f"decision_grounding_review allowed attempts={attempt + 1}", level="DEBUG")
                return candidate
            if attempt == 1:
                raise LLMError("外向き判断の根拠審査に失敗しました: " + review["reason_summary"])
            candidate = self._generate_structured_payload(
                model_config=model_config,
                messages=[*messages, {"role": "user", "content": (
                    "前回の判断の根拠審査で、次のずれが確認されました。同じ文脈の一次根拠から判断し直してください。"
                    + review["reason_summary"]
                )}],
                validator=lambda item: self._validate_decision_contract_for_context(payload=item, context=context),
                repair_prompt_builder=lambda error: build_decision_repair_prompt(error, context.comparison_scope),
                response_format=decision_response_format(comparison_scope=context.comparison_scope),
                failure_message="外向き判断の再判断に失敗しました。",
                operation="decision_grounding_reconsider",
            )
        raise AssertionError("Decision review attempts exhausted.")

    def _validate_grounding_review(self, payload: dict[str, Any]) -> None:
        _validate_exact_keys(payload, {"outcome", "reason_summary"}, "GroundingReview")
        if payload["outcome"] not in {"allow", "reconsider"}:
            raise LLMError("GroundingReview.outcome が不正です。")
        if not isinstance(payload["reason_summary"], str) or not payload["reason_summary"].strip():
            raise LLMError("GroundingReview.reason_summary が必要です。")

    def _review_capability_input_grounding(
        self, *, model_config: dict, persona_context: PersonaContext,
        source_messages: list[dict[str, Any]], candidate_request: dict[str, Any] | None,
    ) -> None:
        if not isinstance(candidate_request, dict) or candidate_request.get("capability_id") != "mcp.call_tool":
            return
        try:
            review = self._generate_structured_payload(
                model_config=model_config,
                messages=build_capability_input_grounding_review_messages(
                    persona_context=persona_context,
                    source_messages=[message for message in source_messages if message["role"] == "user"],
                    candidate_request=candidate_request,
                ),
                validator=self._validate_grounding_review,
                repair_prompt_builder=lambda error: "outcome=allow|reconsider と非空の reason_summary を返してください。" + error,
                response_format=capability_input_grounding_review_response_format(),
                failure_message="MCP 本文の根拠審査に失敗しました。",
                operation="capability_input_grounding_review",
            )
        except Exception as exc:
            # Reviewer failure is not a reason to regenerate an unchecked action.
            raise RuntimeError("MCP 本文の根拠審査に失敗しました。") from exc
        if review["outcome"] == "reconsider":
            raise LLMError("MCP arguments の事実表現を同じ根拠の確かさへ対応させて判断し直してください。審査理由: " + review["reason_summary"])

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

            def validate_candidate(candidate: dict[str, Any]) -> None:
                self._validate_decision_contract_for_context(payload=candidate, context=context)
                self._review_capability_input_grounding(
                    model_config=model_config, persona_context=persona_context,
                    source_messages=messages, candidate_request=candidate.get("capability_request"),
                )

            payload = self._generate_structured_payload(
                model_config=model_config,
                messages=messages,
                validator=validate_candidate,
                repair_prompt_builder=lambda error: build_decision_repair_prompt(
                    error,
                    context.comparison_scope,
                ),
                failure_message="Decision の生成に失敗しました。解析可能な応答が得られませんでした。",
                response_format=decision_response_format(comparison_scope=context.comparison_scope),
                operation=operation,
            )
            if (
                context.comparison_scope != "full"
                or context.current_input.sender_kind != "person"
                or not context.current_input.response_target_refs
                or payload["kind"] == "autonomous_run"
            ):
                if context.comparison_scope == "outward_speech" and context.trigger_kind in {"wake", "background_thinking"}:
                    return self._ground_outward_decision(
                        model_config=model_config, persona_context=persona_context,
                        context=context, messages=messages, candidate=payload,
                    )
                return payload
            review_context = {
                "persona_context": persona_context.to_prompt_payload(),
                "current_input": context.current_input.to_prompt_payload(),
                "recent_turns": context.recent_turns,
                "autonomous_run_summaries": context.autonomous_run_summaries or [],
                "ongoing_action_summary": context.ongoing_action_summary,
                "candidate_decision": payload,
            }
            review = self.generate_future_action_alignment_review(
                model_config=model_config,
                review_context=review_context,
            )
            if review["outcome"] == "aligned":
                return payload
            retry_messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
                {"role": "user", "content": (
                    "前の判断は、現在の人物発話が求める行動の履行範囲に整合していません。"
                    "現在の run と ongoing action、依頼の完了条件を確認して判断を作り直してください。"
                    "複合・待機・継続の履行責務は autonomous_run に載せ、今回の単発操作と返却結果の報告で完結するなら、その実行を行う capability_request を選びます。"
                    f"審査理由: {review['reason_summary']}"
                )},
            ]
            corrected = self._generate_structured_payload(
                model_config=model_config,
                messages=retry_messages,
                validator=validate_candidate,
                repair_prompt_builder=lambda error: build_decision_repair_prompt(
                    error, context.comparison_scope,
                ),
                failure_message="未来行動の依頼に沿う判断の生成に失敗しました。",
                response_format=decision_response_format(comparison_scope=context.comparison_scope),
                operation="decision_future_action_retry",
            )
            if corrected["kind"] != "autonomous_run":
                corrected_review = self.generate_future_action_alignment_review(
                    model_config=model_config,
                    review_context={**review_context, "candidate_decision": corrected},
                )
                if corrected_review["outcome"] != "aligned":
                    raise LLMError("人物の行動依頼の履行範囲に整合する判断が得られませんでした。")
            return corrected
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc, level='ERROR')}", level="ERROR")
            raise

    def generate_future_action_alignment_review(
        self, *, model_config: dict, review_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            return {"outcome": "aligned", "reason_summary": "mock decision を維持する。"}
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_future_action_alignment_review_messages(review_context=review_context),
            validator=validate_future_action_alignment_review_contract,
            repair_prompt_builder=build_future_action_alignment_review_repair_prompt,
            failure_message="FutureActionAlignmentReview の生成に失敗しました。",
            response_format=future_action_alignment_review_response_format(),
            operation="future_action_alignment_review",
        )

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
                f"に含まれる参照だけを指定してください。不明な参照={','.join(missing_refs)}。"
                f"利用できる factor_ref 一覧={json.dumps(sorted(candidate_refs), ensure_ascii=False)}"
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

            def validate_candidate(candidate: dict[str, Any]) -> None:
                self._validate_autonomous_step_contract_for_context(payload=candidate, context=context)
                self._review_capability_input_grounding(
                    model_config=model_config, persona_context=persona_context,
                    source_messages=messages, candidate_request=candidate["action"].get("capability_request"),
                )

            payload = self._generate_structured_payload(
                model_config=model_config,
                messages=messages,
                validator=validate_candidate,
                repair_prompt_builder=build_autonomous_step_repair_prompt,
                failure_message="AutonomousStep の生成に失敗しました。解析可能な応答が得られませんでした。",
                response_format=autonomous_step_response_format(),
                operation=operation,
            )
            return payload
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc, level='ERROR')}", level="ERROR")
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
            review = self.generate_speech_grounding_review(
                model_config=model_config,
                persona_context=persona_context,
                context=context,
                candidate_speech=speech_text,
            )
            payload["speech_text"] = (
                speech_text if review["outcome"] == "allow" else review["speech_text"].strip()
            )
            payload["grounding_review"] = {
                "outcome": review["outcome"],
                "reason_summary": review["reason_summary"],
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
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc, level='ERROR')}", level="ERROR")
            raise

    def generate_speech_grounding_review(
        self,
        *,
        model_config: dict,
        persona_context: PersonaContext,
        context: SpeechContext,
        candidate_speech: str,
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            return {"outcome": "allow", "speech_text": None, "reason_summary": "mock speech を維持する。"}
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_speech_grounding_review_messages(
                context=context,
                persona_context=persona_context,
                candidate_speech=candidate_speech,
            ),
            validator=validate_speech_grounding_review_contract,
            repair_prompt_builder=build_speech_grounding_review_repair_prompt,
            failure_message="SpeechGroundingReview の生成に失敗しました。",
            response_format=speech_grounding_review_response_format(),
            operation="speech_grounding_review",
        )

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
            response_format=disclosure_review_response_format(),
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
            response_format=pre_send_check_response_format(),
            operation=operation,
        )

    def generate_autonomous_start_review(
        self, *, model_config: dict, review_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            raise LLMError("AutonomousStartReview requires an explicit reviewer test double for mock models.")
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_autonomous_start_review_messages(review_context=review_context),
            validator=validate_autonomous_start_review_contract,
            repair_prompt_builder=build_autonomous_start_review_repair_prompt,
            failure_message="AutonomousStartReview の生成に失敗しました。",
            response_format=autonomous_start_review_response_format(),
            operation="autonomous_start_review",
        )

    def generate_autonomous_activity_alignment_review(
        self, *, model_config: dict, review_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            raise LLMError(
                "AutonomousActivityAlignmentReview requires an explicit reviewer test double for mock models."
            )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_autonomous_activity_alignment_review_messages(review_context=review_context),
            validator=validate_autonomous_activity_alignment_review_contract,
            repair_prompt_builder=build_autonomous_activity_alignment_review_repair_prompt,
            failure_message="AutonomousActivityAlignmentReview の生成に失敗しました。",
            response_format=autonomous_activity_alignment_review_response_format(),
            operation="autonomous_activity_alignment_review",
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
            response_format=autonomous_completion_review_response_format(),
            operation=operation,
        )

    def generate_commitment_lifecycle_summaries(self, *, model_config: dict, context: dict[str, Any]) -> dict[str, Any]:
        from otomekairo.llm.commitment import generate_summaries
        return generate_summaries(self, model_config=model_config, context=context)

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
        def validate_interpretation(payload):
            validate_memory_interpretation_contract(payload)
            validate_known_person_references(payload, person_refs=known_person_refs_from_context(memory_context or {}))
        payload = self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_interpretation,
            repair_prompt_builder=build_memory_interpretation_repair_prompt,
            failure_message="MemoryInterpretation の生成に失敗しました。解析可能な応答が得られませんでした。",
            response_format=memory_interpretation_response_format(),
            operation=operation,
        )
        if not correction_targets:
            payload.setdefault("correction_status", "no_correction")
            payload.setdefault("selected_targets", [])
        return payload

    def generate_memory_candidate_review(
        self,
        *,
        model_config: dict,
        review_context: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_count = len(review_context["candidates"])
        target_revision_ids = {
            target["revision_id"] for target in review_context["correction_selection"]["target_candidates"]
        }
        if self._is_mock_model_config(model_config):
            payload = {
                "episode_review": {
                    "summary_text": review_context["episode"]["summary_text"],
                    "outcome_text": review_context["episode"]["outcome_text"],
                    "open_loops": review_context["episode"]["open_loops"],
                    "reason_summary": "mock episode を維持する。",
                },
                "decisions": [
                    {"index": index, "retention_basis": "explicit_pattern", "reason_summary": "mock candidate を維持する。"}
                    for index in range(candidate_count)
                ],
                "correction_review": {
                    "prior_claim_assessment": "contradicted" if review_context["correction_selection"]["correction_status"] == "selected" else "not_reviewed",
                    "contradicted_revision_ids": [
                        target["revision_id"] for target in review_context["correction_selection"]["selected_targets"]
                    ] if review_context["correction_selection"]["correction_status"] == "selected" else [],
                    "replacement_candidate_indices": [],
                    "reason_summary": "mock correction selection を維持する。",
                },
            }
            validate_memory_candidate_review_contract(
                payload, candidate_count=candidate_count, target_revision_ids=target_revision_ids,
            )
            return payload
        retention = self._generate_structured_payload(
            model_config=model_config, messages=build_memory_retention_review_messages(review_context=review_context),
            validator=lambda payload: validate_memory_retention_review_contract(payload, candidate_count=candidate_count),
            repair_prompt_builder=lambda error: "候補ごとに reason_summary, retention_basis, index の decisions だけを返してください。" + error,
            failure_message="MemoryRetentionReview の生成に失敗しました。",
            response_format=memory_retention_review_response_format(), operation="memory_retention_review",
        )
        def validate_assessment(payload: dict[str, Any]) -> None:
            _validate_exact_keys(payload, {"episode_review", "correction_review"}, "MemoryCandidateAssessment")
            validate_memory_candidate_review_contract(
                {**payload, "decisions": retention["decisions"]}, candidate_count=candidate_count,
                target_revision_ids=target_revision_ids,
            )
        review = self._generate_structured_payload(
            model_config=model_config,
            messages=build_memory_candidate_review_messages(review_context={**review_context, "retention_decisions": retention["decisions"]}),
            validator=validate_assessment,
            repair_prompt_builder=build_memory_candidate_review_repair_prompt,
            failure_message="MemoryCandidateReview の生成に失敗しました。",
            response_format=memory_candidate_review_response_format(), operation="memory_candidate_review",
        )
        return {**review, "decisions": retention["decisions"]}


    def generate_affect_review(
        self,
        *,
        model_config: dict,
        review_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            affects = review_context["candidate_episode_affects"]
            self_affects = [affect for affect in affects if affect["target_scope_type"] == "self"]
            if len(self_affects) > 1:
                raise LLMError("Mock affect_review に複数の self 反応があります。")
            payload = {
                "self_reaction": {
                    "affect": self_affects[0] if self_affects else None,
                    "reason_summary": "mock self reaction を維持する。",
                },
                "other_affects": [affect for affect in affects if affect["target_scope_type"] != "self"],
                "reason_summary": "mock affect を維持する。",
            }
            validate_affect_review_contract(payload)
            return payload
        def validate_review(payload):
            validate_affect_review_contract(payload)
            validate_known_person_references(payload, person_refs=known_person_refs_from_context(review_context))
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_affect_review_messages(review_context=review_context),
            validator=validate_review,
            repair_prompt_builder=build_affect_review_repair_prompt,
            failure_message="AffectReview の生成に失敗しました。",
            response_format=affect_review_response_format(),
            operation="affect_review",
        )

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
            response_format=memory_reflection_summary_response_format(),
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
            response_format=event_evidence_response_format(),
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
            response_format=recall_pack_selection_response_format(),
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
            response_format=pending_intent_selection_response_format(),
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
            response_format=initiative_entry_check_response_format(),
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_world_state_source_selection(
        self, *, model_config: dict, persona_context: PersonaContext, input_text: str,
    ) -> dict[str, Any]:
        if self._is_mock_model_config(model_config):
            return {"reported_states": []}
        return self._generate_structured_payload(
            model_config=model_config,
            messages=[
                {"role": "system", "content": (
                    "内部処理 role world_state_source_selection として、人物発話に明示された現在の外界状況の報告を抽出します。"
                    "人格は判断主体の基底ですが、抽出元は input_text の人物発話だけです。"
                    "質問や回答依頼だけなら reported_states は空配列です。"
                    "予定や仮定を現在状態へ移さず、窓を閉めたなど現在も成立する結果は状態の報告として扱います。"
                    "environment は周囲の物理条件、location は人物の現在場所、device は機器の状態、"
                    "external_service は外部サービスの現在条件、social_context は人物が報告した対人状況です。"
                    "人物の活動は activity_state で扱います。人格の自己設定や、情報がないという説明は抽出対象の外界報告と分けます。"
                    "reported_states だけのJSONを返し、各要素は state_type と evidence_text です。"
                    "各 state_type は1件までです。evidence_text は報告内容を含む input_text の連続した原文を引用します。"
                )},
                {"role": "user", "content": json.dumps({
                    "persona_context": persona_context.to_prompt_payload(), "input_text": input_text,
                }, ensure_ascii=False)},
            ],
            validator=lambda payload: validate_world_state_source_selection_contract(payload, input_text=input_text),
            repair_prompt_builder=lambda error: "reported_states の引用と型を修正してください。" + error,
            failure_message="世界状態の入力報告の抽出に失敗しました。",
            response_format=world_state_source_selection_response_format(),
            wrap_validation_error=True, operation="world_state_source_selection",
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
        candidate = self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=lambda payload: validate_world_state_contract(payload, source_pack=source_pack),
            repair_prompt_builder=build_world_state_repair_prompt,
            failure_message="WorldState の生成に失敗しました。解析可能な応答が得られませんでした。",
            response_format=world_state_response_format(),
            wrap_validation_error=True,
            operation=operation,
        )

        return self._review_state_candidates(
            model_config=model_config, state_kind="world_state",
            source_pack=source_pack.to_prompt_payload(), candidate=candidate, candidate_key="state_candidates",
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
        candidate = self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_activity_state_contract,
            repair_prompt_builder=build_activity_state_repair_prompt,
            failure_message="ActivityState の生成に失敗しました。解析可能な応答が得られませんでした。",
            response_format=activity_state_response_format(),
            wrap_validation_error=True,
            operation=operation,
        )

        return self._review_state_candidates(
            model_config=model_config, state_kind="activity_state",
            source_pack=source_pack, candidate=candidate, candidate_key="activity_candidates",
        )

    def _review_state_candidates(
        self, *, model_config: dict, state_kind: str, source_pack: dict[str, Any],
        candidate: dict[str, Any], candidate_key: str,
    ) -> dict[str, Any]:
        candidates = candidate[candidate_key]
        if not candidates:
            return candidate
        review = self._generate_structured_payload(
            model_config=model_config,
            messages=build_state_grounding_review_messages(
                state_kind=state_kind, source_pack=source_pack, candidate=candidate,
            ),
            validator=lambda payload: validate_state_grounding_review_contract(
                payload, candidate_count=len(candidates),
            ),
            repair_prompt_builder=lambda error: (
                "候補ごとに index, evidence_kind, reason_summary の decisions を返してください。\n" + error
            ),
            failure_message="状態候補の根拠審査に失敗しました。",
            response_format=state_grounding_review_response_format(),
            wrap_validation_error=True, operation=f"{state_kind}_grounding_review",
        )
        supporting_kinds = {"supported_report", "supported_observation"}
        if state_kind == "activity_state" and source_pack.get("source_owner") == "self":
            actor_ref = source_pack["activity_subject"]["actor_ref"]
            if actor_ref not in source_pack["observed_person_refs"]:
                supporting_kinds.discard("supported_observation")
        accepted = {item["index"] for item in review["decisions"] if item["evidence_kind"] in supporting_kinds}
        debug_log("LLM", f"{state_kind}_grounding_review decisions={self._debug_rejected_payload(review)}", level="DEBUG")
        return {candidate_key: [item for index, item in enumerate(candidates) if index in accepted]}

    def generate_visual_daily_grouping(self, *, model_config: dict, persona_context: PersonaContext,
                                       records: list[dict[str, Any]]) -> dict[str, Any]:
        from otomekairo.llm.schemas import visual_daily_grouping_response_format
        from otomekairo.llm.visual_daily import grouping_messages, validate_grouping
        if self._is_mock_model_config(model_config):
            # Mechanical mock: no semantic grouping is claimed.
            return {"groups": [{"observation_ids": [r["visual_observation_id"]],
                                "summary_text": r["detailed_summary_text"],
                                "reason_summary": "機械的mockの独立記録。"} for r in records]}
        context = {"persona_context": persona_context.to_prompt_payload(), "records": records}
        return self._generate_structured_payload(
            model_config=model_config, messages=grouping_messages(context),
            validator=lambda payload: validate_grouping(payload, records),
            repair_prompt_builder=lambda error: "全観測IDを順序通り一度ずつ参照するgroupsを返してください。\n" + error,
            failure_message="視覚の日次整理に失敗しました。", wrap_validation_error=True,
            response_format=visual_daily_grouping_response_format(), operation="visual_daily_grouping")

    def generate_visual_daily_support(self, *, model_config: dict, persona_context: PersonaContext,
                                     context: dict[str, Any]) -> dict[str, Any]:
        from otomekairo.llm.schemas import visual_daily_support_response_format
        from otomekairo.llm.visual_daily import support_messages, validate_support
        if self._is_mock_model_config(model_config):
            return {"decisions": [{"candidate_index": i, "support_refs": [],
                                   "reason_summary": "機械的mockでは意味を評価しない。"}
                                  for i in range(len(context["candidates"]))]}
        context = {**context, "persona_context": persona_context.to_prompt_payload()}
        return self._generate_structured_payload(
            model_config=model_config, messages=support_messages(context),
            validator=lambda payload: validate_support(payload, context),
            repair_prompt_builder=lambda error: "全候補のcandidate_index、support_refs、reason_summaryをdecisionsに返してください。\n" + error,
            failure_message="視覚の反復根拠審査に失敗しました。", wrap_validation_error=True,
            response_format=visual_daily_support_response_format(), operation="visual_daily_support")

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
        candidate = self._generate_structured_payload(
            model_config=model_config,
            messages=messages,
            validator=validate_visual_observation_contract,
            repair_prompt_builder=build_visual_observation_repair_prompt,
            failure_message="VisualObservation の生成に失敗しました。解析可能な応答が得られませんでした。",
            response_format=visual_observation_response_format(),
            wrap_validation_error=True,
            operation=operation,
        )
        return self._generate_structured_payload(
            model_config=model_config,
            messages=build_visual_observation_messages(
                persona_context=persona_context, source_pack=source_pack, images=images,
                candidate_observation=candidate,
            ),
            validator=validate_visual_observation_contract,
            repair_prompt_builder=build_visual_observation_repair_prompt,
            failure_message="VisualObservation の画像照合に失敗しました。",
            response_format=visual_observation_review_response_format(),
            wrap_validation_error=True,
            operation="visual_observation_review",
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

    def _debug_error(self, exc: BaseException, *, level: str) -> str:
        # ERROR は文字数で切らない。それ以外は長い例外文を短くする。
        return format_debug_log_text(str(exc), level=level, limit=240)

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
        response_format: dict[str, Any],
        wrap_validation_error: bool = False,
        operation: str = "structured",
    ) -> dict[str, Any]:
        last_error: LLMError | None = None
        attempt_messages = list(messages)
        schema_name = response_format_schema_name(response_format)
        for attempt in range(2):
            content = complete_text(
                model_config=model_config,
                messages=attempt_messages,
                response_format=response_format,
            )
            try:
                payload = parse_json_object(content)
                try:
                    materialize_provider_open_maps(payload, schema_name=schema_name)
                    validator(payload)
                    debug_log(
                        "LLM",
                        (
                            f"{operation} done model={self._debug_model(model_config)} "
                            f"schema={schema_name} attempt={attempt + 1} "
                            f"response_chars={len(content)} "
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
                            f"error={self._debug_error(last_error, level='WARNING')} "
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
                        f"error={self._debug_error(exc, level='WARNING')} "
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
            debug_log("LLM", f"{operation} failed error={self._debug_error(last_error, level='ERROR')}", level="ERROR")
            raise last_error
        debug_log("LLM", f"{operation} failed error={failure_message}", level="ERROR")
        raise LLMError(failure_message)
