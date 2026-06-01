from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from otomekairo.llm.contexts import (
    CurrentInput,
    DecisionContext,
    InitiativeCandidateFamily,
    InitiativeContext,
    ReplyContext,
)
from otomekairo.llm.contracts import (
    LLMContractError,
    LLMError,
    normalize_answer_contract_payload,
    normalize_recall_hint_payload,
    validate_activity_state_contract,
    validate_answer_contract_contract,
    validate_decision_contract,
    validate_event_evidence_contract,
    validate_memory_correction_reconciliation_contract,
    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
    validate_pending_intent_selection_contract,
    validate_recall_pack_selection_contract,
    validate_recall_hint_contract,
    validate_visual_observation_contract,
    validate_world_state_contract,
)
from otomekairo.llm.mock import MockLLMClient
from otomekairo.llm.parsing import parse_json_object, parse_recall_hint_payload
from otomekairo.llm.prompts import (
    build_answer_contract_messages,
    build_answer_contract_repair_prompt,
    build_activity_state_messages,
    build_activity_state_repair_prompt,
    build_decision_messages,
    build_decision_repair_prompt,
    build_event_evidence_messages,
    build_event_evidence_repair_prompt,
    build_input_interpretation_messages,
    build_input_interpretation_repair_prompt,
    build_memory_correction_reconciliation_messages,
    build_memory_correction_reconciliation_repair_prompt,
    build_memory_interpretation_messages,
    build_memory_interpretation_repair_prompt,
    build_memory_reflection_summary_messages,
    build_memory_reflection_summary_repair_prompt,
    build_pending_intent_selection_messages,
    build_pending_intent_selection_repair_prompt,
    build_recall_pack_selection_messages,
    build_recall_pack_selection_repair_prompt,
    build_recall_hint_messages,
    build_reply_messages,
    build_visual_observation_messages,
    build_visual_observation_repair_prompt,
    build_world_state_messages,
    build_world_state_repair_prompt,
)
from otomekairo.world_state.models import WorldStateSourcePack
from otomekairo.llm.transport import complete_text, generate_embeddings as transport_generate_embeddings
from otomekairo.service.common import debug_log

# LiteLLM連携
@dataclass(slots=True)
class LLMClient:
    mock_client: MockLLMClient = field(default_factory=MockLLMClient)

    def generate_input_interpretation(
        self,
        *,
        role_definition: dict,
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict],
        current_time: str,
        visual_observation_context: dict[str, Any] | None,
        activity_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = "input_interpretation"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} input_chars={len(input_text)} "
                f"recent_turns={len(recent_turns)}"
            ),
            level="DEBUG",
        )
        try:
            if self._is_mock_role_definition(role_definition):
                recall_hint = self.mock_client.generate_recall_hint(
                    role_definition,
                    input_text,
                    recent_turns,
                    current_time,
                )
                answer_contract = self.mock_client.generate_answer_contract(
                    role_definition,
                    input_text,
                    recall_hint,
                    current_time,
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
                current_input=current_input,
                recent_turns=recent_turns,
                current_time=current_time,
                visual_observation_context=visual_observation_context,
                activity_context=activity_context,
            )
            payload = self._generate_structured_payload(
                role_definition=role_definition,
                messages=messages,
                validator=self._validate_input_interpretation_contract,
                repair_prompt_builder=build_input_interpretation_repair_prompt,
                failure_message="InputInterpretation の生成に失敗しました。解析可能な応答が得られませんでした。",
                operation=operation,
            )
            recall_hint = normalize_recall_hint_payload(payload["recall_hint"])
            answer_contract = normalize_answer_contract_payload(payload["answer_contract"])
            debug_log(
                "LLM",
                (
                    f"{operation} done focus={recall_hint.get('primary_recall_focus')} "
                    f"contract={answer_contract.get('contract')}"
                ),
                level="DEBUG",
            )
            return {
                "recall_hint": recall_hint,
                "answer_contract": answer_contract,
            }
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}", level="ERROR")
            raise

    def _validate_input_interpretation_contract(self, payload: dict[str, Any]) -> None:
        required_keys = {"recall_hint", "answer_contract"}
        if set(payload.keys()) != required_keys:
            raise LLMError("InputInterpretation のキーが契約と一致しません。")
        recall_hint = payload["recall_hint"]
        if not isinstance(recall_hint, dict):
            raise LLMError("InputInterpretation.recall_hint は object である必要があります。")
        if not isinstance(payload["answer_contract"], dict):
            raise LLMError("InputInterpretation.answer_contract は object である必要があります。")
        validate_recall_hint_contract(normalize_recall_hint_payload(recall_hint))
        validate_answer_contract_contract(payload["answer_contract"])

    def generate_recall_hint(
        self,
        *,
        role_definition: dict,
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict],
        current_time: str,
    ) -> dict[str, Any]:
        operation = "recall_hint"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} input_chars={len(input_text)} "
                f"recent_turns={len(recent_turns)}"
            ),
            level="DEBUG",
        )
        try:
            # モック経路
            if self._is_mock_role_definition(role_definition):
                payload = self.mock_client.generate_recall_hint(role_definition, input_text, recent_turns, current_time)
                debug_log(
                    "LLM",
                    (
                        f"{operation} done mode=mock focus={payload.get('primary_recall_focus')} "
                        f"confidence={payload.get('confidence')}"
                    ),
                    level="DEBUG",
                )
                return payload

            # プロンプト構築
            messages = build_recall_hint_messages(
                current_input=current_input,
                recent_turns=recent_turns,
                current_time=current_time,
            )

            # 再試行
            last_contract_error: LLMError | None = None
            for attempt in range(2):
                debug_log("LLM", f"{operation} attempt={attempt + 1} request messages={len(messages)}", level="DEBUG")
                content = complete_text(role_definition=role_definition, messages=messages)
                try:
                    payload = parse_recall_hint_payload(content)
                    debug_log(
                        "LLM",
                        (
                            f"{operation} done attempt={attempt + 1} response_chars={len(content)} "
                            f"focus={payload.get('primary_recall_focus')} confidence={payload.get('confidence')}"
                        ),
                        level="DEBUG",
                    )
                    return payload
                except LLMError as exc:
                    last_contract_error = exc
                    debug_log(
                        "LLM",
                        f"{operation} parse_failed attempt={attempt + 1} error={self._debug_error(exc)}",
                        level="WARNING",
                    )
                    if attempt >= 1:
                        raise

            # 失敗
            if last_contract_error is not None:
                raise last_contract_error
            raise LLMError("RecallHint の生成に失敗しました。解析可能な応答が得られませんでした。")
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}", level="ERROR")
            raise

    def generate_decision(
        self,
        *,
        role_definition: dict,
        persona: dict,
        context: DecisionContext,
    ) -> dict[str, Any]:
        operation = "decision"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} recent_turns={len(context.recent_turns)} "
                f"recall_candidates={context.recall_pack.get('candidate_count', 0)}"
            ),
            level="DEBUG",
        )
        try:
            # モック経路
            if self._is_mock_role_definition(role_definition):
                payload = self.mock_client.generate_decision(
                    role_definition=role_definition,
                    persona=persona,
                    context=context,
                )
                debug_log("LLM", f"{operation} done mode=mock kind={payload.get('kind')}", level="DEBUG")
                return payload

            # プロンプト構築
            messages = build_decision_messages(
                persona=persona,
                context=context,
            )

            return self._generate_structured_payload(
                role_definition=role_definition,
                messages=messages,
                validator=lambda payload: self._validate_decision_contract_for_context(
                    payload=payload,
                    context=context,
                ),
                repair_prompt_builder=build_decision_repair_prompt,
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
        validate_decision_contract(payload)
        self._validate_decision_explicit_status_request(
            payload=payload,
            input_text=context.input_text,
            trigger_kind=context.trigger_kind,
            capability_decision_view=context.capability_decision_view,
        )
        if isinstance(context.capability_result_context, dict):
            self._validate_decision_capability_result_context(
                payload=payload,
                capability_result_context=context.capability_result_context,
            )
        try:
            self._validate_decision_fresh_world_state_reuse(
                payload=payload,
                input_text=context.input_text,
                trigger_kind=context.trigger_kind,
                capability_decision_view=context.capability_decision_view,
            )
        except LLMError as exc:
            if context.trigger_kind != "user_message" and payload.get("kind") == "capability_request":
                self._coerce_decision_to_noop_for_fresh_world_state_reuse(payload, exc)
                return
            raise
        self._validate_decision_visual_observation_context(
            payload=payload,
            trigger_kind=context.trigger_kind,
            visual_observation_context=context.visual_observation_context,
        )
        self._validate_decision_user_message_response(
            payload=payload,
            context=context,
        )
        if context.initiative_context is None:
            return
        selected_family = self._selected_initiative_family_entry(context.initiative_context)
        if selected_family is None:
            return
        preferred_result_kind = selected_family.preferred_result_kind
        if not isinstance(preferred_result_kind, str) or not preferred_result_kind:
            return
        decision_kind = payload.get("kind")
        if preferred_result_kind == "capability_request" and decision_kind != "capability_request":
            raise LLMError(
                "Initiative selected candidate entry は preferred_result_kind=capability_request です。"
                "kind=capability_request を返してください。"
            )
        if decision_kind == "capability_request" and preferred_result_kind != "capability_request":
            raise LLMError(
                "Initiative selected candidate entry は "
                f"preferred_result_kind={preferred_result_kind} です。"
                "preferred_result_kind=capability_request ではないため capability_request は不正です。"
                f"kind={preferred_result_kind} を返してください。"
            )
        if decision_kind == "capability_request":
            preferred_capability_id = selected_family.preferred_capability_id
            request_payload = payload.get("capability_request")
            request_capability_id = (
                request_payload.get("capability_id")
                if isinstance(request_payload, dict)
                else None
            )
            if isinstance(preferred_capability_id, str) and request_capability_id != preferred_capability_id:
                raise LLMError(
                    "Initiative selected candidate entry の preferred_capability_id と "
                    "capability_request.capability_id が一致していません。"
                )
            return
        if decision_kind == "noop" and preferred_result_kind == "reply":
            if selected_family.family == "autonomous":
                return
            foreground_summary = context.initiative_context.foreground_signal_summary
            suppression_summary = context.initiative_context.suppression_summary
            foreground_thinness = (
                foreground_summary.get("foreground_thinness")
                if isinstance(foreground_summary, dict)
                else None
            )
            suppression_level = (
                suppression_summary.get("suppression_level")
                if isinstance(suppression_summary, dict)
                else None
            )
            cooldown_active = (
                suppression_summary.get("cooldown_active")
                if isinstance(suppression_summary, dict)
                else None
            )
            if (
                foreground_thinness in {"ready", "grounded"}
                and suppression_level != "high"
                and cooldown_active is not True
            ):
                raise LLMError(
                    "Initiative selected candidate entry は preferred_result_kind=reply で、"
                    "foreground_signal_summary.foreground_thinness は reply 可能な前景です。"
                    "noop は不正です。kind=reply を返してください。"
                )

    def _validate_decision_visual_observation_context(
        self,
        *,
        payload: dict[str, Any],
        trigger_kind: str,
        visual_observation_context: dict[str, Any] | None,
    ) -> None:
        if trigger_kind != "user_message" or payload.get("kind") != "noop":
            return
        if not isinstance(visual_observation_context, dict):
            return
        if visual_observation_context.get("source") != "conversation_attachment":
            return
        if visual_observation_context.get("image_interpreted") is not True:
            return
        summary_text = visual_observation_context.get("visual_summary_text")
        if not isinstance(summary_text, str) or not summary_text.strip():
            return
        reason_text = " ".join(
            str(payload.get(key) or "")
            for key in ("reason_code", "reason_summary")
        )
        missing_terms = ("画像データ", "視覚情報", "欠落", "添付画像", "不足")
        if any(term in reason_text for term in missing_terms):
            raise LLMError(
                "会話添付画像は VisualObservationContext.visual_summary_text として解釈済みです。"
                "raw image が decision prompt に無いことを理由に noop を返してはいけません。"
                "visual_summary_text の範囲で kind=reply を返してください。"
            )

    def _validate_decision_user_message_response(
        self,
        *,
        payload: dict[str, Any],
        context: DecisionContext,
    ) -> None:
        if payload.get("kind") != "noop":
            return
        current_input = context.current_input
        if current_input.sender != "user" or current_input.response_target != "user":
            return
        text = current_input.text.strip()
        if not text or self._user_message_explicitly_allows_noop(text):
            return
        raise LLMError(
            "current_input.sender=user かつ response_target=user の非空 text はユーザー発話です。"
            "ユーザー発話への noop は不正です。短い挨拶や断片でも kind=reply を返してください。"
        )

    def _user_message_explicitly_allows_noop(self, text: str) -> bool:
        normalized = text.strip().lower()
        if not normalized:
            return True
        return any(
            marker in normalized
            for marker in (
                "返信不要",
                "返事不要",
                "反応不要",
                "no reply",
                "do not reply",
            )
        )

    def _coerce_decision_to_noop_for_fresh_world_state_reuse(
        self,
        payload: dict[str, Any],
        exc: LLMError,
    ) -> None:
        reason_summary = str(exc).replace("\n", " ").strip()
        if len(reason_summary) > 220:
            reason_summary = reason_summary[:219] + "…"
        payload.update(
            {
                "kind": "noop",
                "reason_code": "fresh_world_state_reuse_noop",
                "reason_summary": reason_summary
                or "新鮮な world_state があるため、非ユーザー起点の重複 capability request は行わない。",
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": None,
            }
        )
        debug_log(
            "LLM",
            "decision coerced_to_noop reason=fresh_world_state_reuse_non_user_trigger",
        )

    def _validate_decision_explicit_status_request(
        self,
        *,
        payload: dict[str, Any],
        input_text: str,
        trigger_kind: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> None:
        if trigger_kind != "user_message":
            return
        expected_capability_id = self._explicit_status_request_capability_id(input_text)
        if expected_capability_id is None:
            return
        capability_entry = self._capability_decision_view_entry(
            capability_decision_view=capability_decision_view,
            capability_id=expected_capability_id,
        )
        if not isinstance(capability_entry, dict) or capability_entry.get("available") is not True:
            return
        request_payload = payload.get("capability_request")
        request_capability_id = (
            request_payload.get("capability_id")
            if isinstance(request_payload, dict)
            else None
        )
        if payload.get("kind") == "capability_request" and request_capability_id == expected_capability_id:
            return
        raise LLMError(
            "ユーザーは現在状態の確認を明示的に依頼しています。"
            f"CapabilityDecisionView で {expected_capability_id} が available=true のため、"
            f"kind=capability_request で capability_id={expected_capability_id} を返してください。"
        )

    def _explicit_status_request_capability_id(self, input_text: str) -> str | None:
        normalized = input_text.strip()
        if not normalized:
            return None
        action_terms = (
            "確認",
            "教えて",
            "知りたい",
            "チェック",
            "見て",
        )
        if not any(term in normalized for term in action_terms):
            return None
        capability_terms = (
            ("external.status", ("GitHub", "github", "外部サービス", "サービス状態", "レビュー")),
            ("schedule.status", ("予定", "カレンダー", "このあと", "今日", "近日")),
            ("social.status", ("対人文脈", "会話状況", "会話文脈", "連絡状況", "会議文脈")),
            ("device.status", ("端末", "接続", "電源", "バッテリー", "ネットワーク")),
            ("body.status", ("体調", "身体", "疲労", "眠気", "姿勢")),
            ("environment.status", ("周囲", "作業環境", "部屋", "騒音", "明るさ")),
            ("location.status", ("場所", "居場所", "移動中", "作業場所")),
            ("vision.capture", ("視覚", "画面", "スクリーン", "表示", "ウィンドウ", "デスクトップ", "カメラ")),
        )
        for capability_id, terms in capability_terms:
            if any(term in normalized for term in terms):
                return capability_id
        return None

    def _validate_decision_fresh_world_state_reuse(
        self,
        *,
        payload: dict[str, Any],
        input_text: str,
        trigger_kind: str,
        capability_decision_view: list[dict[str, Any]] | None,
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
        if (
            trigger_kind == "user_message"
            and self._explicit_status_request_capability_id(input_text) == normalized_request_capability_id
        ):
            return
        capability_entry = self._capability_decision_view_entry(
            capability_decision_view=capability_decision_view,
            capability_id=normalized_request_capability_id,
        )
        if not isinstance(capability_entry, dict) or capability_entry.get("fresh_world_state_available") is not True:
            if normalized_request_capability_id == "vision.capture" and isinstance(capability_entry, dict):
                self._validate_vision_capture_fresh_world_state_reuse(
                    request_payload=request_payload,
                    capability_entry=capability_entry,
                )
            return
        if normalized_request_capability_id == "vision.capture":
            self._validate_vision_capture_fresh_world_state_reuse(
                request_payload=request_payload,
                capability_entry=capability_entry,
            )
            return
        fresh_world_state = capability_entry.get("fresh_world_state")
        state_type = None
        age_label = None
        summary_text = None
        if isinstance(fresh_world_state, dict):
            state_type = fresh_world_state.get("state_type")
            age_label = fresh_world_state.get("age_label")
            summary_text = fresh_world_state.get("summary_text")
        state_summary = ""
        if isinstance(state_type, str) and state_type.strip():
            state_summary += f" state_type={state_type.strip()}"
        if isinstance(age_label, str) and age_label.strip():
            state_summary += f" age_label={age_label.strip()}"
        if isinstance(summary_text, str) and summary_text.strip():
            state_summary += f" summary={summary_text.strip()[:80]}"
        raise LLMError(
            f"CapabilityDecisionView の {normalized_request_capability_id} は "
            f"fresh_world_state_available=true です。{state_summary}"
            "明示的なユーザー依頼なしで同じ現在状態を再取得する capability_request は不正です。"
            "既存の foreground_world_state を使って reply / noop / pending_intent を返してください。"
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
                state_summary += f" summary={summary_text.strip()[:80]}"
            raise LLMError(
                "CapabilityDecisionView の vision.capture には "
                f"vision_source_id={requested_source_id.strip()} の新鮮な visual_context があります。{state_summary}"
                "明示的なユーザー依頼なしで同じ vision_source_id を再取得する capability_request は不正です。"
                "既存の foreground_world_state を使って reply / noop / pending_intent を返してください。"
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
            if item.get("id") == capability_id:
                return item
        return None

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
        if request_capability_id.strip() in normalized_allowed:
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
            "受け取った result に基づく reply / noop / pending_intent を返してください。"
        )

    def _selected_initiative_family_entry(
        self,
        initiative_context: InitiativeContext,
    ) -> InitiativeCandidateFamily | None:
        return initiative_context.selected_family_entry()

    def generate_reply(
        self,
        *,
        role_definition: dict,
        persona: dict,
        context: ReplyContext,
    ) -> dict[str, Any]:
        operation = "reply"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} decision_kind={context.decision.get('kind')}"
            ),
            level="DEBUG",
        )
        try:
            # モック経路
            if self._is_mock_role_definition(role_definition):
                payload = self.mock_client.generate_reply(
                    role_definition=role_definition,
                    persona=persona,
                    context=context,
                )
                debug_log("LLM", f"{operation} done mode=mock reply_chars={len(payload.get('reply_text', ''))}", level="DEBUG")
                return payload

            # プロンプト構築
            messages = build_reply_messages(
                persona=persona,
                context=context,
            )

            # 補完
            debug_log("LLM", f"{operation} request messages={len(messages)}", level="DEBUG")
            content = complete_text(role_definition=role_definition, messages=messages)
            reply_text = content.strip()
            if not reply_text:
                raise LLMError("Reply の生成結果が空でした。")

            # payload作成
            payload = {
                "reply_text": reply_text,
                "reply_style_notes": f"model={role_definition.get('model')}",
                "confidence_note": "litellm_model",
            }
            debug_log("LLM", f"{operation} done response_chars={len(content)} reply_chars={len(reply_text)}", level="DEBUG")
            return payload
        except Exception as exc:
            debug_log("LLM", f"{operation} failed error={type(exc).__name__}: {self._debug_error(exc)}", level="ERROR")
            raise

    def generate_answer_contract(
        self,
        *,
        role_definition: dict,
        input_text: str,
        recall_hint: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        operation = "answer_contract"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} input_chars={len(input_text)}"
            ),
            level="DEBUG",
        )
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_answer_contract(
                role_definition,
                input_text,
                recall_hint,
                current_time,
            )
            normalized = normalize_answer_contract_payload(payload)
            debug_log("LLM", f"{operation} done mode=mock contract={normalized.get('contract')}", level="DEBUG")
            return normalized

        messages = build_answer_contract_messages(
            input_text=input_text,
            recall_hint=recall_hint,
            current_time=current_time,
        )
        payload = self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_answer_contract_contract,
            repair_prompt_builder=build_answer_contract_repair_prompt,
            failure_message="AnswerContract の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )
        normalized = normalize_answer_contract_payload(payload)
        debug_log("LLM", f"{operation} done contract={normalized.get('contract')}", level="DEBUG")
        return normalized

    def generate_memory_interpretation(
        self,
        *,
        role_definition: dict,
        input_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
        memory_context: dict[str, Any] | None,
        current_time: str,
    ) -> dict[str, Any]:
        operation = "memory_interpretation"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} input_chars={len(input_text)} "
                f"decision_kind={decision.get('kind')} reply_chars={len(reply_text or '')}"
            ),
            level="DEBUG",
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_memory_interpretation(
                role_definition,
                input_text,
                recall_hint,
                decision,
                reply_text,
                memory_context,
            )
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_memory_interpretation_messages(
            input_text=input_text,
            recall_hint=recall_hint,
            decision=decision,
            reply_text=reply_text,
            memory_context=memory_context,
            current_time=current_time,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_memory_interpretation_contract,
            repair_prompt_builder=build_memory_interpretation_repair_prompt,
            failure_message="MemoryInterpretation の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )

    def generate_memory_reflection_summary(
        self,
        *,
        role_definition: dict,
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "memory_reflection_summary"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} evidence_keys={self._debug_payload_keys(evidence_pack)}"
            ),
            level="DEBUG",
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_memory_reflection_summary(role_definition, evidence_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_memory_reflection_summary_messages(
            evidence_pack=evidence_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_memory_reflection_summary_contract,
            repair_prompt_builder=build_memory_reflection_summary_repair_prompt,
            failure_message="MemoryReflectionSummary の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )

    def generate_memory_correction_reconciliation(
        self,
        *,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "memory_correction_reconciliation"
        targets = source_pack.get("target_candidates", []) if isinstance(source_pack, dict) else []
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} target_count={len(targets)}"
            ),
            level="DEBUG",
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_memory_correction_reconciliation(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_memory_correction_reconciliation_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_memory_correction_reconciliation_contract,
            repair_prompt_builder=build_memory_correction_reconciliation_repair_prompt,
            failure_message="MemoryCorrectionReconciliation の生成に失敗しました。解析可能な応答が得られませんでした。",
            operation=operation,
        )

    def generate_event_evidence(
        self,
        *,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "event_evidence"
        source_events = source_pack.get("events", []) if isinstance(source_pack, dict) else []
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} events={len(source_events) if isinstance(source_events, list) else 0}"
            ),
            level="DEBUG",
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_event_evidence(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_event_evidence_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
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
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "recall_pack_selection"
        candidates = source_pack.get("candidates", []) if isinstance(source_pack, dict) else []
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} candidates={len(candidates) if isinstance(candidates, list) else 0}"
            ),
            level="DEBUG",
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_recall_pack_selection(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_recall_pack_selection_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
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
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "pending_intent_selection"
        candidates = source_pack.get("candidates", []) if isinstance(source_pack, dict) else []
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} candidates={len(candidates) if isinstance(candidates, list) else 0}"
            ),
            level="DEBUG",
        )
        # モック経路
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_pending_intent_selection(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        # プロンプト構築
        messages = build_pending_intent_selection_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=lambda payload: validate_pending_intent_selection_contract(payload, source_pack=source_pack),
            repair_prompt_builder=build_pending_intent_selection_repair_prompt,
            failure_message="PendingIntentSelection の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_world_state(
        self,
        *,
        role_definition: dict,
        source_pack: WorldStateSourcePack,
    ) -> dict[str, Any]:
        operation = "world_state"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)}"
            ),
            level="DEBUG",
        )
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_world_state(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        messages = build_world_state_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
            messages=messages,
            validator=validate_world_state_contract,
            repair_prompt_builder=build_world_state_repair_prompt,
            failure_message="WorldState の生成に失敗しました。解析可能な応答が得られませんでした。",
            wrap_validation_error=True,
            operation=operation,
        )

    def generate_activity_state(
        self,
        *,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        operation = "activity_state"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)}"
            ),
            level="DEBUG",
        )
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_activity_state(role_definition, source_pack)
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        messages = build_activity_state_messages(
            source_pack=source_pack,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
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
        role_definition: dict,
        source_pack: dict[str, Any],
        images: list[str],
    ) -> dict[str, Any]:
        operation = "visual_observation"
        debug_log(
            "LLM",
            (
                f"{operation} start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} images={len(images)}"
            ),
            level="DEBUG",
        )
        if self._is_mock_role_definition(role_definition):
            payload = self.mock_client.generate_visual_observation_summary(
                role_definition,
                source_pack,
                images,
            )
            debug_log("LLM", f"{operation} done mode=mock keys={self._debug_payload_keys(payload)}", level="DEBUG")
            return payload

        messages = build_visual_observation_messages(
            source_pack=source_pack,
            images=images,
        )
        return self._generate_structured_payload(
            role_definition=role_definition,
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
        role_definition: dict,
        texts: list[str],
    ) -> list[list[float]]:
        # 空
        if not texts:
            debug_log("LLM", "embeddings skipped empty_texts", level="DEBUG")
            return []

        # 次元
        embedding_dimension = self._embedding_dimension(role_definition)
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise LLMError("embedding_dimension は正の整数である必要があります。")

        debug_log(
            "LLM",
            (
                f"embeddings start mode={self._debug_mode(role_definition)} "
                f"model={self._debug_model(role_definition)} texts={len(texts)} dimension={embedding_dimension}"
            ),
            level="DEBUG",
        )

        # モック経路
        if self._is_mock_role_definition(role_definition):
            vectors = self.mock_client.generate_embeddings(role_definition, texts, embedding_dimension)
            debug_log("LLM", f"embeddings done mode=mock vectors={len(vectors)}", level="DEBUG")
            return vectors

        # model差分込みの transport へ委譲する。
        vectors = transport_generate_embeddings(
            role_definition=role_definition,
            texts=texts,
            expected_dimension=embedding_dimension,
        )
        debug_log("LLM", f"embeddings done vectors={len(vectors)}", level="DEBUG")
        return vectors

    # 設定補助
    def _debug_model(self, role_definition: dict) -> str:
        # 秘密情報を含まない model 名だけを出す。
        model = role_definition.get("model")
        if not isinstance(model, str) or not model.strip():
            return "-"
        return model.strip()

    def _debug_mode(self, role_definition: dict) -> str:
        # 実行経路
        return "mock" if self._is_mock_role_definition(role_definition) else "transport"

    def _debug_error(self, exc: BaseException) -> str:
        # 長い応答本文をログへ出しすぎない。
        message = str(exc).replace("\n", " ").strip()
        if len(message) <= 240:
            return message
        return message[:239] + "…"

    def _debug_payload_keys(self, payload: dict[str, Any]) -> str:
        # payload の中身ではなくキーだけを出す。
        keys = sorted(str(key) for key in payload.keys())[:8]
        return ",".join(keys) if keys else "-"

    def _is_mock_role_definition(self, role_definition: dict) -> bool:
        # model=mock* は開発用の内蔵ロジックへ切り替える。
        model = role_definition.get("model")
        return isinstance(model, str) and model.strip().startswith("mock")

    def _embedding_dimension(self, role_definition: dict) -> int:
        return role_definition.get("embedding_dimension")

    def _generate_structured_payload(
        self,
        *,
        role_definition: dict,
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
            debug_log("LLM", f"{operation} attempt={attempt + 1} request messages={len(attempt_messages)}", level="DEBUG")
            content = complete_text(role_definition=role_definition, messages=attempt_messages)
            try:
                payload = parse_json_object(content)
                try:
                    validator(payload)
                    debug_log(
                        "LLM",
                        (
                            f"{operation} done attempt={attempt + 1} response_chars={len(content)} "
                            f"keys={self._debug_payload_keys(payload)}"
                        ),
                        level="DEBUG",
                    )
                    return payload
                except LLMError as exc:
                    last_error = LLMContractError(str(exc)) if wrap_validation_error else exc
                    debug_log(
                        "LLM",
                        f"{operation} validation_failed attempt={attempt + 1} error={self._debug_error(last_error)}",
                        level="WARNING",
                    )
            except LLMError as exc:
                last_error = exc
                debug_log(
                    "LLM",
                    f"{operation} parse_failed attempt={attempt + 1} error={self._debug_error(exc)}",
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
