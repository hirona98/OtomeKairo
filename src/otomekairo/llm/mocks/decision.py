from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from otomekairo.llm.contexts import AutonomousStepContext, DecisionContext
from otomekairo.llm.contracts import validate_autonomous_step_contract, validate_decision_contract
from otomekairo.llm.mocks.capability import MOCK_CAPABILITY_REQUEST_RULES


class LLMMockDecisionMixin:
    def generate_decision(
        self,
        *,
        role_definition: dict,
        persona_context: Any,
        context: DecisionContext,
    ) -> dict[str, Any]:
        # model確認
        _ = persona_context
        self._assert_mock_model(role_definition)
        input_text = context.input_text
        recent_turns = context.recent_turns
        time_context = context.time_context
        affect_context = context.affect_context
        ongoing_action_summary = context.ongoing_action_summary
        autonomous_run_summaries = context.autonomous_run_summaries
        capability_decision_view = context.capability_decision_view
        initiative_context = context.initiative_context
        capability_result_context = context.capability_result_context
        recall_hint = context.recall_hint
        recall_pack = context.recall_pack
        _ = recent_turns
        _ = time_context

        # コンテキスト
        normalized = input_text.strip()
        primary_recall_focus = recall_hint["primary_recall_focus"]
        secondary_recall_focuses = self._secondary_recall_focuses(recall_hint)
        conflicts = recall_pack.get("conflicts", [])
        active_commitments = recall_pack.get("active_commitments", [])
        episodic_evidence = recall_pack.get("episodic_evidence", [])
        event_evidence = recall_pack.get("event_evidence", [])
        active_topics = recall_pack.get("active_topics", [])
        mood_state = affect_context.get("mood_state") or {}
        recent_episode_affects = affect_context.get("recent_episode_affects", [])
        current_vad = mood_state.get("current_vad") or {}
        current_valence = float(current_vad.get("v", 0.0)) if isinstance(current_vad, dict) else 0.0

        payload = self._mock_capability_result_followup_decision(
            capability_result_context=capability_result_context,
            capability_decision_view=capability_decision_view,
        )
        if payload is None:
            payload = self._mock_initiative_decision(
                initiative_context=initiative_context,
                capability_decision_view=capability_decision_view,
            )
        if payload is None:
            payload = self._mock_autonomous_run_decision(
                normalized=normalized,
                autonomous_run_summaries=autonomous_run_summaries,
            )
        if payload is None:
            payload = self._mock_capability_request_decision(
                normalized=normalized,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
            )
        if payload is None:
            payload = self._mock_pending_intent_decision(
                normalized=normalized,
                primary_recall_focus=primary_recall_focus,
                active_commitments=active_commitments,
                episodic_evidence=episodic_evidence,
                event_evidence=event_evidence,
                active_topics=active_topics,
            )
        if payload is None:
            payload = self._mock_default_conversation_decision(
                primary_recall_focus=primary_recall_focus,
                secondary_recall_focuses=secondary_recall_focuses,
                conflicts=conflicts,
                active_commitments=active_commitments,
                episodic_evidence=episodic_evidence,
                recent_episode_affects=recent_episode_affects,
                current_valence=current_valence,
            )

        # 検証
        payload.setdefault("capability_request", None)
        payload.setdefault("autonomous_run", None)
        payload.setdefault("foreground_selection", self._mock_foreground_selection(context))
        validate_decision_contract(payload)
        return payload

    def _mock_foreground_selection(self, context: DecisionContext) -> dict[str, Any]:
        workspace_context = context.workspace_context if isinstance(context.workspace_context, dict) else {}
        candidates = workspace_context.get("workspace_candidates")
        if not isinstance(candidates, list):
            candidates = []
        factor_refs = [
            str(candidate.get("factor_ref")).strip()
            for candidate in candidates
            if isinstance(candidate, dict)
            and isinstance(candidate.get("factor_ref"), str)
            and candidate.get("factor_ref").strip()
        ]
        primary_factor_ref = factor_refs[0] if factor_refs else None
        supporting_factor_refs = factor_refs[1:4]
        suppressed_factors = [
            {
                "factor_ref": factor_ref,
                "reason_summary": "mock decision では前景化の主因または補助因子に選ばれなかった。",
            }
            for factor_ref in factor_refs[4:9]
        ]
        return {
            "primary_factor_ref": primary_factor_ref,
            "supporting_factor_refs": supporting_factor_refs,
            "suppressed_factors": suppressed_factors,
            "summary_text": "mock decision は workspace 候補順で前景化結果を作った。",
        }

    def generate_autonomous_step(
        self,
        *,
        role_definition: dict,
        persona_context: Any,
        context: AutonomousStepContext,
    ) -> dict[str, Any]:
        # model確認
        _ = persona_context
        self._assert_mock_model(role_definition)

        run = context.run
        objective = str(run.get("objective_summary") or "").strip()
        history = str(run.get("history_summary") or "").strip()
        last_result_context = context.last_result_context if isinstance(context.last_result_context, dict) else {}
        capability_decision_view = context.capability_decision_view

        action: dict[str, Any]
        transition = {
            "kind": "complete",
            "next_run_at": None,
        }
        current_step_summary = "mock autonomous_run を完了した。"

        if self._mock_autonomous_step_needs_initial_speech(objective=objective, history=history):
            action = {
                "kind": "speech",
                "capability_request": None,
                "speech": {
                    "reason_code": "autonomous_run:initial_speech",
                    "reason_summary": "複合行動の最初に短く伝える。",
                },
            }
            transition = {
                "kind": "wait_until",
                "next_run_at": self._mock_autonomous_step_next_run_at(),
            }
            current_step_summary = "発話後の次の一手を待つ。"
        elif self._mock_autonomous_step_needs_camera_ptz(
            objective=objective,
            history=history,
            last_result_context=last_result_context,
            capability_decision_view=capability_decision_view,
        ):
            request_input = self._mock_camera_ptz_input(
                capability_decision_view=capability_decision_view,
                operation=self._mock_camera_ptz_operation(objective) or "move_right",
                amount=self._mock_camera_ptz_amount(objective),
            )
            action = {
                "kind": "capability_request",
                "capability_request": {
                    "capability_id": "camera.ptz",
                    "input": request_input or {},
                },
                "speech": None,
            }
            transition = {
                "kind": "continue",
                "next_run_at": None,
            }
            current_step_summary = "camera.ptz の結果を待つ。"
        elif self._mock_autonomous_step_needs_vision_capture(
            objective=objective,
            history=history,
            last_result_context=last_result_context,
            capability_decision_view=capability_decision_view,
        ):
            request_input = self._mock_autonomous_vision_capture_input(
                objective=objective,
                capability_decision_view=capability_decision_view,
                last_result_context=last_result_context,
            )
            action = {
                "kind": "capability_request",
                "capability_request": {
                    "capability_id": "vision.capture",
                    "input": request_input or {},
                },
                "speech": None,
            }
            transition = {
                "kind": "continue",
                "next_run_at": None,
            }
            current_step_summary = "vision.capture の結果を待つ。"
        else:
            action = {
                "kind": "none",
                "capability_request": None,
                "speech": None,
            }

        payload = {
            "action": action,
            "transition": transition,
            "run_update": {
                "current_step_summary": current_step_summary,
                "history_summary": self._mock_autonomous_run_history_update(
                    history=history,
                    action=action,
                    transition=transition,
                ),
            },
        }
        validate_autonomous_step_contract(payload)
        return payload

    def _mock_autonomous_step_next_run_at(self) -> str:
        return (datetime.now().astimezone() + timedelta(seconds=5)).isoformat()

    def _mock_autonomous_run_decision(
        self,
        *,
        normalized: str,
        autonomous_run_summaries: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        if not normalized:
            return None
        markers = (
            "自律実行",
            "自律run",
            "autonomous_run",
            "続けて",
            "してから",
            "見てから",
            "話してから",
            "言ってから",
            "3分後",
            "三分後",
        )
        if not any(marker in normalized for marker in markers):
            return None
        return {
            "kind": "autonomous_run",
            "reason_code": "autonomous_run:start",
            "reason_summary": "複数の行動や待機をまたぐ目的として扱う。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": {
                "objective_summary": normalized[:180],
                "initial_step_summary": "目的に沿って最初の一手を決める。",
                "coordination": self._mock_autonomous_run_coordination(
                    autonomous_run_summaries=autonomous_run_summaries,
                ),
            },
        }

    def _mock_autonomous_run_coordination(
        self,
        *,
        autonomous_run_summaries: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        _ = autonomous_run_summaries
        return {
            "mode": "create_new",
            "target_run_ids": [],
            "reason_summary": "既存 run との関係を持たない新しい目的として開始する。",
        }

    def _mock_autonomous_step_needs_initial_speech(self, *, objective: str, history: str) -> bool:
        if history:
            return False
        markers = ("発話", "話して", "言って", "伝えて", "speech")
        return any(marker in objective for marker in markers)

    def _mock_autonomous_step_needs_camera_ptz(
        self,
        *,
        objective: str,
        history: str,
        last_result_context: dict[str, Any],
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        _ = last_result_context
        if "camera.ptz" in history:
            return False
        if not self._mock_capability_available(capability_decision_view, "camera.ptz"):
            return False
        camera_markers = ("カメラ", "視野", "視界", "画角", "向き")
        operation = self._mock_camera_ptz_operation(objective)
        return operation is not None and any(marker in objective for marker in camera_markers)

    def _mock_autonomous_step_needs_vision_capture(
        self,
        *,
        objective: str,
        history: str,
        last_result_context: dict[str, Any],
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if not self._mock_capability_available(capability_decision_view, "vision.capture"):
            return False
        if "vision.capture" in history and last_result_context.get("source_capability_id") == "vision.capture":
            return False
        markers = ("見て", "見る", "観測", "確認", "画面", "デスクトップ", "カメラ", "視覚")
        return any(marker in objective for marker in markers) or last_result_context.get("source_capability_id") == "camera.ptz"

    def _mock_autonomous_vision_capture_input(
        self,
        *,
        objective: str,
        capability_decision_view: list[dict[str, Any]] | None,
        last_result_context: dict[str, Any],
    ) -> dict[str, str] | None:
        source_request_summary = last_result_context.get("source_request_summary")
        if isinstance(source_request_summary, dict):
            source_id = source_request_summary.get("vision_source_id")
            if isinstance(source_id, str) and source_id.strip():
                return {
                    "vision_source_id": source_id.strip(),
                    "mode": "still",
                }
        vision_input = self._mock_vision_capture_input(capability_decision_view)
        if "デスクトップ" not in objective and "画面" not in objective:
            return vision_input
        return vision_input

    def _mock_autonomous_run_history_update(
        self,
        *,
        history: str,
        action: dict[str, Any],
        transition: dict[str, Any],
    ) -> str:
        action_kind = action.get("kind")
        capability_request = action.get("capability_request")
        entry = f"action={action_kind}"
        if isinstance(capability_request, dict):
            entry += f":{capability_request.get('capability_id')}"
        entry += f" transition={transition.get('kind')}"
        return f"{history} / {entry}" if history else entry

    def _mock_capability_result_followup_decision(
        self,
        *,
        capability_result_context: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(capability_result_context, dict):
            return None
        if capability_result_context.get("source_capability_id") != "camera.ptz":
            return None
        source_id = self._mock_camera_ptz_followup_vision_source_id(capability_result_context)
        if source_id is None:
            return None
        if not self._mock_vision_source_available(
            capability_decision_view=capability_decision_view,
            vision_source_id=source_id,
        ):
            return None
        return {
            "kind": "capability_request",
            "reason_code": "capability_result:camera.ptz",
            "reason_summary": "camera.ptz の結果を受け、同じカメラ source を観測する。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": {
                "capability_id": "vision.capture",
                "input": {
                    "vision_source_id": source_id,
                    "mode": "still",
                },
            },
        }

    def _mock_camera_ptz_followup_vision_source_id(
        self,
        capability_result_context: dict[str, Any],
    ) -> str | None:
        constraints = capability_result_context.get("followup_constraints")
        if isinstance(constraints, list):
            for constraint in constraints:
                if not isinstance(constraint, dict):
                    continue
                if constraint.get("capability_id") != "vision.capture":
                    continue
                if constraint.get("constraint") != "same_vision_source_id":
                    continue
                source_id = constraint.get("vision_source_id")
                if isinstance(source_id, str) and source_id.strip():
                    return source_id.strip()
        source_request_summary = capability_result_context.get("source_request_summary")
        if isinstance(source_request_summary, dict):
            source_id = source_request_summary.get("vision_source_id")
            if isinstance(source_id, str) and source_id.strip():
                return source_id.strip()
        return None

    def _mock_vision_source_available(
        self,
        *,
        capability_decision_view: list[dict[str, Any]] | None,
        vision_source_id: str,
    ) -> bool:
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") != "vision.capture" or item.get("available") is not True:
                continue
            sources = item.get("vision_sources")
            if not isinstance(sources, list):
                return False
            for source in sources:
                if not isinstance(source, dict):
                    continue
                if source.get("vision_source_id") == vision_source_id:
                    return True
        return False

    def _mock_initiative_decision(
        self,
        *,
        initiative_context: Any,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        initiative_trigger = initiative_context.trigger_kind if initiative_context is not None else None
        initiative_pending = initiative_context.pending_intent_summaries if initiative_context is not None else []
        if initiative_trigger not in {"wake", "background_thinking"} or initiative_pending:
            return None
        capability_request = self._mock_autonomous_initiative_capability_request(
            initiative_context=initiative_context,
            capability_decision_view=capability_decision_view,
        )
        if capability_request is not None:
            return {
                "kind": "capability_request",
                "reason_code": f"initiative:{capability_request['capability_id']}",
                "reason_summary": "継続中の initiative 候補から capability follow-up を進める。",
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": capability_request,
            }
        if self._should_mock_autonomous_initiative_speech(initiative_context):
            visual_summary = self._mock_initiative_changed_visual_summary(initiative_context)
            reason_summary = "現在の drive_state や world_state から自発的に前へ出る理由がある。"
            if visual_summary is not None:
                reason_summary = f"視覚観測の変化があり、短く触れる理由がある。{visual_summary}"
            return {
                "kind": "speech",
                "reason_code": "initiative_context",
                "reason_summary": reason_summary,
                "requires_confirmation": False,
                "pending_intent": None,
            }
        return {
            "kind": "noop",
            "reason_code": "initiative_wait",
            "reason_summary": "現在の前景だけでは自発的に前へ出る理由がまだ弱い。",
            "requires_confirmation": False,
            "pending_intent": None,
        }

    def _mock_capability_request_decision(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        for capability_id, predicate_name, input_builder_name, reason_summary in MOCK_CAPABILITY_REQUEST_RULES:
            predicate = getattr(self, predicate_name)
            if not predicate(
                normalized=normalized,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
            ):
                continue
            request_input = getattr(self, input_builder_name)(
                normalized=normalized,
                capability_decision_view=capability_decision_view,
            )
            if request_input is None:
                continue
            return {
                "kind": "capability_request",
                "reason_code": f"capability:{capability_id}",
                "reason_summary": reason_summary,
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": {
                    "capability_id": capability_id,
                    "input": request_input,
                },
            }
        return None

    def _mock_pending_intent_decision(
        self,
        *,
        normalized: str,
        primary_recall_focus: str,
        active_commitments: list[dict[str, Any]],
        episodic_evidence: list[dict[str, Any]],
        event_evidence: list[dict[str, Any]],
        active_topics: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not self._should_mock_pending_intent(
            normalized=normalized,
            active_commitments=active_commitments,
            episodic_evidence=episodic_evidence,
            event_evidence=event_evidence,
            active_topics=active_topics,
        ):
            return None
        return {
            "kind": "pending_intent",
            "reason_code": "defer_for_later",
            "reason_summary": "継続価値はあるが、今は返さず後で触れたほうが自然。",
            "requires_confirmation": False,
            "pending_intent": self._mock_pending_intent_payload(
                primary_recall_focus=primary_recall_focus,
                active_commitments=active_commitments,
                episodic_evidence=episodic_evidence,
                event_evidence=event_evidence,
                active_topics=active_topics,
            ),
        }

    def _mock_default_conversation_decision(
        self,
        *,
        primary_recall_focus: str,
        secondary_recall_focuses: list[str],
        conflicts: list[dict[str, Any]],
        active_commitments: list[dict[str, Any]],
        episodic_evidence: list[dict[str, Any]],
        recent_episode_affects: list[dict[str, Any]],
        current_valence: float,
    ) -> dict[str, Any]:
        if conflicts:
            return {
                "kind": "speech",
                "reason_code": "conflict_present",
                "reason_summary": "RecallPack に矛盾候補があり、確認寄りの返答が必要。",
                "requires_confirmation": True,
                "pending_intent": None,
            }
        if primary_recall_focus == "commitment" and active_commitments:
            return {
                "kind": "speech",
                "reason_code": "active_commitment",
                "reason_summary": "進行中の約束や保留があり、継続会話として返答する。",
                "requires_confirmation": False,
                "pending_intent": None,
            }
        if "episodic" in secondary_recall_focuses and episodic_evidence:
            return {
                "kind": "speech",
                "reason_code": "secondary_episodic",
                "reason_summary": "補助焦点として回想があり、関連エピソードを踏まえて返答する。",
                "requires_confirmation": False,
                "pending_intent": None,
            }
        if recent_episode_affects and recent_episode_affects[0]["affect_label"] in {"不安", "緊張", "迷い", "concern"}:
            return {
                "kind": "speech",
                "reason_code": "affect_caution",
                "reason_summary": "AffectContext に慎重さを要する感情があり、確認寄りに返す。",
                "requires_confirmation": True,
                "pending_intent": None,
            }
        if current_valence <= -0.25:
            return {
                "kind": "speech",
                "reason_code": "mood_caution",
                "reason_summary": "AffectContext の現在機嫌がやや張っており、慎重寄りに返す。",
                "requires_confirmation": True,
                "pending_intent": None,
            }
        return {
            "kind": "speech",
            "reason_code": f"focus:{primary_recall_focus}",
            "reason_summary": "A normal conversation speech is appropriate for the current input.",
            "requires_confirmation": primary_recall_focus in {"fact", "relationship"},
            "pending_intent": None,
        }

    def _should_mock_pending_intent(
        self,
        *,
        normalized: str,
        active_commitments: list[dict[str, Any]],
        episodic_evidence: list[dict[str, Any]],
        event_evidence: list[dict[str, Any]],
        active_topics: list[dict[str, Any]],
    ) -> bool:
        # マーカー確認
        defer_markers = (
            "また今度",
            "あとで",
            "後で",
            "今はいい",
            "今じゃなくて",
            "いったん保留",
            "また後で",
            "またあとで",
            "今は寝る",
            "明日また",
        )
        if not any(marker in normalized for marker in defer_markers):
            return False

        # recall基準
        return bool(active_commitments or episodic_evidence or event_evidence or active_topics)

    def _mock_pending_intent_payload(
        self,
        *,
        primary_recall_focus: str,
        active_commitments: list[dict[str, Any]],
        episodic_evidence: list[dict[str, Any]],
        event_evidence: list[dict[str, Any]],
        active_topics: list[dict[str, Any]],
    ) -> dict[str, str]:
        # commitment候補
        commitment_item = active_commitments[0] if active_commitments else None
        if commitment_item is not None:
            scope_type = commitment_item.get("scope_type", "relationship")
            scope_key = commitment_item.get("scope_key", "self|user")
            predicate = commitment_item.get("predicate", "follow_up")
            return {
                "intent_kind": "conversation_follow_up",
                "intent_summary": commitment_item.get("summary_text", "継続中の約束や保留にあとで触れたい。"),
                "dedupe_key": f"pending_intent:{scope_type}:{scope_key}:{predicate}",
            }

        # episode候補
        episode_item = episodic_evidence[0] if episodic_evidence else None
        if episode_item is not None:
            scope_type = episode_item.get("primary_scope_type", "user")
            scope_key = episode_item.get("primary_scope_key", "user")
            episode_id = episode_item.get("episode_id", "unknown")
            return {
                "intent_kind": "conversation_follow_up",
                "intent_summary": episode_item.get("summary_text", "あとで続きに触れたい出来事がある。"),
                "dedupe_key": f"pending_intent:{scope_type}:{scope_key}:{episode_id}",
            }

        # イベント候補
        event_item = event_evidence[0] if event_evidence else None
        event_basis = self._event_evidence_basis_text(event_item)
        if event_item is not None:
            return {
                "intent_kind": "conversation_follow_up",
                "intent_summary": event_basis or "あとで触れたい出来事がある。",
                "dedupe_key": f"pending_intent:event:{event_item.get('event_id', 'unknown')}",
            }

        # トピック候補
        topic_item = active_topics[0] if active_topics else None
        if topic_item is not None:
            scope_key = topic_item.get("scope_key", topic_item.get("primary_scope_key", "topic"))
            return {
                "intent_kind": "conversation_follow_up",
                "intent_summary": topic_item.get("summary_text", "あとで続けたい話題がある。"),
                "dedupe_key": f"pending_intent:topic:{scope_key}",
            }

        # 代替
        return {
            "intent_kind": "conversation_follow_up",
            "intent_summary": "あとで会話を再開したい。",
            "dedupe_key": f"pending_intent:focus:{primary_recall_focus}",
        }
