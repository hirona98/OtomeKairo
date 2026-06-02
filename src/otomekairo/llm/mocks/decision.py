from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import DecisionContext
from otomekairo.llm.contracts import validate_decision_contract
from otomekairo.llm.mocks.capability import MOCK_CAPABILITY_REQUEST_RULES


class LLMMockDecisionMixin:
    def generate_decision(
        self,
        *,
        role_definition: dict,
        persona: dict,
        context: DecisionContext,
    ) -> dict[str, Any]:
        # model確認
        _ = persona
        self._assert_mock_model(role_definition)
        input_text = context.input_text
        recent_turns = context.recent_turns
        time_context = context.time_context
        affect_context = context.affect_context
        ongoing_action_summary = context.ongoing_action_summary
        capability_decision_view = context.capability_decision_view
        initiative_context = context.initiative_context
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

        payload = self._mock_initiative_decision(
            initiative_context=initiative_context,
            capability_decision_view=capability_decision_view,
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
        validate_decision_contract(payload)
        return payload

    def _mock_initiative_decision(
        self,
        *,
        initiative_context: Any,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        initiative_trigger = initiative_context.trigger_kind if initiative_context is not None else None
        initiative_pending = initiative_context.pending_intent_summaries if initiative_context is not None else []
        if initiative_trigger not in {"wake", "background_wake"} or initiative_pending:
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
            return {
                "kind": "speech",
                "reason_code": "initiative_context",
                "reason_summary": "現在の drive_state や world_state から自発的に前へ出る理由がある。",
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
