from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from otomekairo.llm_contracts import (
    RECALL_PACK_SECTION_NAMES,
    RECALL_FOCUS_VALUES,
    LLMError,
    validate_decision_contract,
    validate_event_evidence_contract,
    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
    validate_pending_intent_selection_contract,
    validate_recall_pack_selection_contract,
    validate_recall_hint_contract,
    validate_visual_observation_contract,
    validate_world_state_contract,
)


# モッククライアント
@dataclass(slots=True)
class MockLLMClient:
    def generate_visual_observation_summary(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
        images: list[str],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # context
        _ = images
        client_context = source_pack.get("client_context", {}) if isinstance(source_pack, dict) else {}
        active_app = ""
        window_title = ""
        if isinstance(client_context, dict):
            active_app = str(client_context.get("active_app") or "").strip()
            window_title = str(client_context.get("window_title") or "").strip()

        # summary
        if active_app and window_title:
            if active_app in {"Slack", "Discord", "Teams"}:
                channel_name = window_title.split("|", 1)[0].strip()
                summary_text = f"{active_app} の会話画面が前景で、{channel_name} のやり取りが見えている。"
            else:
                summary_text = f"{active_app} の画面が前景で、{window_title} が表示されている。"
        elif active_app:
            summary_text = f"{active_app} の画面が前景にある。"
        elif window_title:
            summary_text = f"{window_title} の画面が前景にある。"
        else:
            summary_text = "現在のデスクトップ画面の前景が見えている。"

        payload = {
            "summary_text": summary_text,
            "confidence_hint": "medium",
        }
        validate_visual_observation_contract(payload)
        return payload

    def generate_recall_hint(
        self,
        role_definition: dict,
        input_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # ヒューリスティックfocus
        normalized = input_text.strip()
        lower_text = normalized.lower()

        interaction_mode = "conversation" if normalized else "autonomous"
        primary_recall_focus = "user"
        secondary_recall_focuses: list[str] = []
        risk_flags: list[str] = []
        time_reference = "none"

        if any(token in normalized for token in ("この前", "昨日", "前に", "続き")):
            primary_recall_focus = "episodic"
            time_reference = "past"
        elif any(token in normalized for token in ("約束", "今度", "また話", "また今度")):
            primary_recall_focus = "commitment"
            time_reference = "future"
        elif any(token in normalized for token in ("相談", "どうしたら", "悩", "困って")):
            primary_recall_focus = "user"
            time_reference = "recent"
        elif any(token in normalized for token in ("元気", "大丈夫", "調子", "眠れて")):
            primary_recall_focus = "state"
            time_reference = "recent"
        elif any(token in normalized for token in ("好き", "嫌い", "食べたい", "食べ")):
            primary_recall_focus = "preference"
            time_reference = "persistent"
        elif any(token in normalized for token in ("関係", "距離", "話しにく")):
            primary_recall_focus = "relationship"
            time_reference = "recent"
        elif lower_text.endswith("?") or "?" in lower_text:
            primary_recall_focus = "fact"

        # 副次focus
        if primary_recall_focus in {"user", "state"} and recent_turns:
            secondary_recall_focuses.append("episodic")
        if secondary_recall_focuses:
            risk_flags.append("mixed_intent")
        if any(token in normalized for token in ("あれ", "あの", "その件", "例の")):
            risk_flags.append("ambiguous_reference")
        if any(token in normalized for token in ("いつか", "前", "この前")) and time_reference == "none":
            risk_flags.append("time_ambiguous")

        # focus scope判定
        focus_scopes = ["user"]
        if primary_recall_focus == "relationship":
            focus_scopes.append("relationship:self|user")
        if primary_recall_focus == "preference":
            focus_scopes.append("topic:preference")
        if primary_recall_focus == "commitment":
            focus_scopes.append("relationship:self|user")

        # 言及hint群
        mentioned_entities = self._mock_mentioned_entities(normalized)
        mentioned_topics = self._mock_mentioned_topics(normalized)

        # payload作成
        payload = {
            "interaction_mode": interaction_mode,
            "primary_recall_focus": primary_recall_focus,
            "secondary_recall_focuses": secondary_recall_focuses[:2],
            "confidence": 0.7 if normalized else 0.1,
            "time_reference": time_reference,
            "focus_scopes": focus_scopes[:4],
            "mentioned_entities": mentioned_entities[:4],
            "mentioned_topics": mentioned_topics[:4],
            "risk_flags": risk_flags[:3],
        }
        validate_recall_hint_contract(payload)
        return payload

    def _mock_mentioned_entities(self, normalized: str) -> list[str]:
        # 空
        if not normalized:
            return []

        # 一致群
        entities: list[str] = []
        for match in re.findall(r"([一-龠ぁ-んァ-ヶA-Za-z0-9]{1,20})(?:さん|君|ちゃん)", normalized):
            tag = f"person:{match}"
            if tag not in entities:
                entities.append(tag)
            if len(entities) >= 4:
                break

        # 結果
        return entities

    def _mock_mentioned_topics(self, normalized: str) -> list[str]:
        # 空
        if not normalized:
            return []

        # キーワード対応表
        topic_keywords = {
            "睡眠": ("眠", "寝", "朝型", "夜型"),
            "食事": ("食べ", "ご飯", "ランチ", "夕飯", "カフェ"),
            "仕事": ("仕事", "会社", "会議", "残業", "出勤"),
            "約束": ("約束", "予定", "今度", "また今度"),
            "関係": ("関係", "距離", "話しにく", "ぎくしゃく"),
            "相談": ("相談", "悩", "困っ", "どうしたら"),
        }

        # 収集
        topics: list[str] = []
        for topic_name, keywords in topic_keywords.items():
            if not any(keyword in normalized for keyword in keywords):
                continue
            topics.append(f"topic:{topic_name}")
            if len(topics) >= 4:
                break

        # 結果
        return topics

    def generate_decision(
        self,
        role_definition: dict,
        persona: dict,
        input_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: dict[str, Any] | None,
        recall_hint: dict,
        recall_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        _ = persona
        _ = drive_state_summary
        _ = foreground_world_state
        self._assert_mock_model(role_definition)

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

        # decisionルール
        initiative_trigger = initiative_context.get("trigger_kind") if isinstance(initiative_context, dict) else None
        initiative_pending = initiative_context.get("pending_intent_summaries", []) if isinstance(initiative_context, dict) else []
        if initiative_trigger in {"wake", "background_wake"} and not initiative_pending:
            capability_request = self._mock_autonomous_initiative_capability_request(
                initiative_context=initiative_context,
                capability_decision_view=capability_decision_view,
            )
            if capability_request is not None:
                payload = {
                    "kind": "capability_request",
                    "reason_code": f"initiative:{capability_request['capability_id']}",
                    "reason_summary": "継続中の initiative 候補から capability follow-up を進める。",
                    "requires_confirmation": False,
                    "pending_intent": None,
                    "capability_request": capability_request,
                }
            elif self._should_mock_autonomous_initiative_reply(initiative_context):
                payload = {
                    "kind": "reply",
                    "reason_code": "initiative_context",
                    "reason_summary": "現在の drive_state や world_state から自発的に前へ出る理由がある。",
                    "requires_confirmation": False,
                    "pending_intent": None,
                }
            else:
                payload = {
                    "kind": "noop",
                    "reason_code": "initiative_wait",
                    "reason_summary": "現在の前景だけでは自発的に前へ出る理由がまだ弱い。",
                    "requires_confirmation": False,
                    "pending_intent": None,
                }
        elif not normalized:
            payload = {
                "kind": "noop",
                "reason_code": "empty_input",
                "reason_summary": "Input text was empty after normalization.",
                "requires_confirmation": False,
                "pending_intent": None,
            }
        elif self._should_mock_vision_capture_request(
            normalized=normalized,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
        ):
            payload = {
                "kind": "capability_request",
                "reason_code": "capability:vision.capture",
                "reason_summary": "現在の画面状態を観測する必要がある。",
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": {
                    "capability_id": "vision.capture",
                    "input": {
                        "source": "desktop",
                        "mode": "still",
                    },
                },
            }
        elif self._should_mock_schedule_status_request(
            normalized=normalized,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
        ):
            payload = {
                "kind": "capability_request",
                "reason_code": "capability:schedule.status",
                "reason_summary": "近い予定やカレンダー状態を確認する必要がある。",
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": {
                    "capability_id": "schedule.status",
                    "input": self._mock_schedule_status_input(normalized),
                },
            }
        elif self._should_mock_external_status_request(
            normalized=normalized,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
        ):
            payload = {
                "kind": "capability_request",
                "reason_code": "capability:external.status",
                "reason_summary": "外部サービスの現在状態を確認する必要がある。",
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": {
                    "capability_id": "external.status",
                    "input": self._mock_external_status_input(normalized),
                },
            }
        elif self._should_mock_pending_intent(
            normalized=normalized,
            active_commitments=active_commitments,
            episodic_evidence=episodic_evidence,
            event_evidence=event_evidence,
            active_topics=active_topics,
        ):
            payload = {
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
        elif conflicts:
            payload = {
                "kind": "reply",
                "reason_code": "conflict_present",
                "reason_summary": "RecallPack に矛盾候補があり、確認寄りの返答が必要。",
                "requires_confirmation": True,
                "pending_intent": None,
            }
        elif primary_recall_focus == "commitment" and active_commitments:
            payload = {
                "kind": "reply",
                "reason_code": "active_commitment",
                "reason_summary": "進行中の約束や保留があり、継続会話として返答する。",
                "requires_confirmation": False,
                "pending_intent": None,
            }
        elif "episodic" in secondary_recall_focuses and episodic_evidence:
            payload = {
                "kind": "reply",
                "reason_code": "secondary_episodic",
                "reason_summary": "補助焦点として回想があり、関連エピソードを踏まえて返答する。",
                "requires_confirmation": False,
                "pending_intent": None,
            }
        elif recent_episode_affects and recent_episode_affects[0]["affect_label"] in {"不安", "緊張", "迷い", "concern"}:
            payload = {
                "kind": "reply",
                "reason_code": "affect_caution",
                "reason_summary": "AffectContext に慎重さを要する感情があり、確認寄りに返す。",
                "requires_confirmation": True,
                "pending_intent": None,
            }
        elif current_valence <= -0.25:
            payload = {
                "kind": "reply",
                "reason_code": "mood_caution",
                "reason_summary": "AffectContext の現在機嫌がやや張っており、慎重寄りに返す。",
                "requires_confirmation": True,
                "pending_intent": None,
            }
        else:
            payload = {
                "kind": "reply",
                "reason_code": f"focus:{primary_recall_focus}",
                "reason_summary": "A normal conversation reply is appropriate for the current input.",
                "requires_confirmation": primary_recall_focus in {"fact", "relationship"},
                "pending_intent": None,
            }

        # 検証
        payload.setdefault("capability_request", None)
        validate_decision_contract(payload)
        return payload

    def generate_reply(
        self,
        role_definition: dict,
        persona: dict,
        input_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: dict[str, Any] | None,
        recall_hint: dict,
        recall_pack: dict[str, Any],
        decision: dict,
    ) -> dict[str, Any]:
        # model確認
        _ = drive_state_summary
        _ = foreground_world_state
        _ = ongoing_action_summary
        _ = capability_decision_view
        self._assert_mock_model(role_definition)

        # コンテキスト
        persona_prompt = str(persona.get("persona_prompt", "")).strip()
        primary_recall_focus = recall_hint["primary_recall_focus"]
        secondary_recall_focuses = self._secondary_recall_focuses(recall_hint)
        text = input_text.strip()
        conflict_items = recall_pack.get("conflicts", [])
        commitment_items = recall_pack.get("active_commitments", [])
        relationship_items = recall_pack.get("relationship_model", [])
        user_items = recall_pack.get("user_model", [])
        topic_items = recall_pack.get("active_topics", [])
        episode_items = recall_pack.get("episodic_evidence", [])
        event_items = recall_pack.get("event_evidence", [])
        mood_state = affect_context.get("mood_state") or {}
        recent_episode_affects = affect_context.get("recent_episode_affects", [])
        conflict_item = conflict_items[0] if conflict_items else None
        commitment_item = commitment_items[0] if commitment_items else None
        relationship_item = relationship_items[0] if relationship_items else None
        user_item = user_items[0] if user_items else None
        topic_item = topic_items[0] if topic_items else None
        episode_item = episode_items[0] if episode_items else None
        event_item = event_items[0] if event_items else None
        recent_episode_affect = recent_episode_affects[0] if recent_episode_affects else None
        event_basis = self._event_evidence_basis_text(event_item)
        current_vad = mood_state.get("current_vad") or {}
        current_valence = float(current_vad.get("v", 0.0)) if isinstance(current_vad, dict) else 0.0

        # 注意プレフィックス
        caution_prefix = ""
        if conflict_item is not None:
            caution_prefix = "今は少し慎重に受け取っている。"
        elif recent_episode_affect is not None and recent_episode_affect["affect_label"] in {"不安", "緊張", "迷い", "concern"}:
            caution_prefix = "少し慎重に聞いているよ。"
        elif current_valence <= -0.25:
            caution_prefix = "少し気を引き締めて聞いているよ。"

        # 継続プレフィックス
        continuity_prefix = ""
        if primary_recall_focus != "episodic" and "episodic" in secondary_recall_focuses:
            if episode_item is not None or event_basis is not None or recent_turns:
                continuity_prefix = "前の流れも踏まえると、"

        initiative_reply = self._mock_initiative_reply_text(
            initiative_context=initiative_context,
            decision=decision,
        )

        # 返信ルール
        if initiative_reply is not None:
            reply_text = initiative_reply
        elif decision["requires_confirmation"]:
            basis_text = None
            if relationship_item is not None:
                basis_text = relationship_item["summary_text"]
            elif episode_item is not None:
                basis_text = episode_item["summary_text"]
            elif event_basis is not None:
                basis_text = event_basis
            elif conflict_item is not None:
                basis_text = conflict_item["summary_text"]
            if basis_text is not None:
                reply_text = (
                    f"{caution_prefix}{basis_text} という流れで受け取っているけれど、"
                    f"{text} の理解はこれで合っている？"
                )
            else:
                reply_text = f"{caution_prefix}{text} の受け取りを断定せず確認したい。いまの理解で合っている？"
        elif primary_recall_focus == "user" and any(token in text for token in ("相談", "どうしたら", "悩", "困って")):
            if user_item is not None:
                reply_text = f"{caution_prefix}{continuity_prefix}{user_item['summary_text']} も踏まえて聞くね。{text} の中で、今いちばん困っている点をもう少し教えて。"
            else:
                reply_text = f"{caution_prefix}{continuity_prefix}状況は受け取ったよ。{text} の中で、今いちばん困っている点をもう少し教えて。"
        elif primary_recall_focus == "commitment":
            if commitment_item is not None:
                if "どこまで" in text:
                    reply_text = f"{commitment_item['summary_text']} の続きとして受け取ったよ。いまはどの範囲まで進めたい？"
                else:
                    reply_text = f"{commitment_item['summary_text']} の続きとして受け取ったよ。{text} について、今回はどこまで進めたい？"
            elif event_basis is not None:
                reply_text = f"{event_basis} の続きとして受け取ったよ。{text} について、今回はどこまで進めたい？"
            else:
                reply_text = f"{caution_prefix}その流れは覚えている前提で話すね。{text} に関して、今回どこまで進めたい？"
        elif primary_recall_focus == "episodic":
            if episode_item is not None:
                reply_text = f"{episode_item['summary_text']} の流れとして受け取ったよ。{text} のどの部分からつなげたい？"
            elif event_basis is not None:
                reply_text = f"{event_basis} の場面として受け取ったよ。{text} のどの部分からつなげたい？"
            else:
                reply_text = f"{caution_prefix}その続きとして受け取ったよ。{text} のどの部分からつなげたい？"
        elif primary_recall_focus == "preference":
            reply_text = f"{caution_prefix}{continuity_prefix}好みの話として受け取ったよ。{text} について、今の気分も含めて聞かせて。"
        else:
            topic_prefix = ""
            if topic_item is not None:
                topic_prefix = f"{topic_item['summary_text']} の流れで、"
            elif recent_turns:
                topic_prefix = "前の流れをつなげつつ、"
            reply_text = f"{caution_prefix}{continuity_prefix}{topic_prefix}{text}として受け取ったよ。"

        # payload作成
        return {
            "reply_text": reply_text,
            "reply_style_notes": (
                f"persona_prompt_present={bool(persona_prompt)}; part_of_day={time_context.get('part_of_day', 'unknown')}"
            ),
            "confidence_note": "mock_model",
        }

    def _event_evidence_basis_text(self, item: dict[str, Any] | None) -> str | None:
        # 空
        if item is None:
            return None

        # スロット群
        for key in ("decision_or_result", "topic", "anchor", "tone_or_note"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        # 結果
        return None

    def _should_mock_autonomous_initiative_reply(self, initiative_context: dict[str, Any] | None) -> bool:
        if not isinstance(initiative_context, dict):
            return False
        selected_family = self._selected_initiative_family_entry(initiative_context)
        if isinstance(selected_family, dict):
            preferred_result_kind = str(selected_family.get("preferred_result_kind") or "").strip()
            if preferred_result_kind in {"noop", "capability_request"}:
                return False
        drive_summaries = initiative_context.get("drive_summaries", [])
        if isinstance(drive_summaries, list) and drive_summaries:
            return True
        world_state_summary = initiative_context.get("world_state_summary", [])
        if isinstance(world_state_summary, list):
            for item in world_state_summary:
                if not isinstance(item, dict):
                    continue
                if item.get("state_type") in {"schedule", "social_context", "body"}:
                    return True
        ongoing_action_summary = initiative_context.get("ongoing_action_summary")
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status and status != "waiting_result":
                return True
        return False

    def _mock_autonomous_initiative_capability_request(
        self,
        *,
        initiative_context: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(initiative_context, dict):
            return None
        selected_family = self._selected_initiative_family_entry(initiative_context)
        if not isinstance(selected_family, dict):
            return None
        preferred_result_kind = str(selected_family.get("preferred_result_kind") or "").strip()
        if preferred_result_kind != "capability_request":
            return None
        preferred_capability_id = str(selected_family.get("preferred_capability_id") or "").strip()
        preferred_capability_input = selected_family.get("preferred_capability_input")
        if (
            preferred_capability_id
            and isinstance(preferred_capability_input, dict)
            and self._mock_capability_available(capability_decision_view, preferred_capability_id)
        ):
            return {
                "capability_id": preferred_capability_id,
                "input": preferred_capability_input,
            }
        ongoing_action_summary = initiative_context.get("ongoing_action_summary")
        if not isinstance(ongoing_action_summary, dict):
            return None
        capability_id = str(ongoing_action_summary.get("last_capability_id") or "").strip()
        if capability_id == "vision.capture" and self._mock_capability_available(capability_decision_view, capability_id):
            return {
                "capability_id": capability_id,
                "input": {
                    "source": "desktop",
                    "mode": "still",
                },
            }
        return None

    def _selected_initiative_family_entry(self, initiative_context: dict[str, Any]) -> dict[str, Any] | None:
        candidate_families = initiative_context.get("candidate_families")
        selected_candidate_family = initiative_context.get("selected_candidate_family")
        if not isinstance(candidate_families, list):
            return None
        for family in candidate_families:
            if not isinstance(family, dict):
                continue
            if family.get("selected") is True:
                return family
            family_name = family.get("family")
            if isinstance(selected_candidate_family, str) and family_name == selected_candidate_family:
                return family
        return None

    def _mock_initiative_reply_text(
        self,
        *,
        initiative_context: dict[str, Any] | None,
        decision: dict[str, Any],
    ) -> str | None:
        if not isinstance(initiative_context, dict) or decision.get("kind") != "reply":
            return None
        if initiative_context.get("trigger_kind") not in {"wake", "background_wake"}:
            return None
        pending_intent_summaries = initiative_context.get("pending_intent_summaries", [])
        if isinstance(pending_intent_summaries, list) and pending_intent_summaries:
            return None
        selected_family = self._selected_initiative_family_entry(initiative_context)
        if isinstance(selected_family, dict) and selected_family.get("family") == "ongoing_action":
            ongoing_action_summary = initiative_context.get("ongoing_action_summary")
            if isinstance(ongoing_action_summary, dict):
                step_summary = ongoing_action_summary.get("step_summary")
                if isinstance(step_summary, str) and step_summary.strip():
                    return f"{step_summary.strip()} が前景にあるから、ここは続きとして少し前へ進めるね。"
        world_state_summary = initiative_context.get("world_state_summary", [])
        if isinstance(world_state_summary, list):
            for item in world_state_summary:
                if not isinstance(item, dict):
                    continue
                summary_text = item.get("summary_text")
                if not isinstance(summary_text, str) or not summary_text.strip():
                    continue
                if item.get("state_type") == "schedule":
                    return f"{summary_text.strip()} という前景があるから、今のうちにそっと声をかけておくね。"
                if item.get("state_type") in {"social_context", "body"}:
                    return f"{summary_text.strip()} と見えているから、今の様子に少し触れてみるね。"
        drive_summaries = initiative_context.get("drive_summaries", [])
        if isinstance(drive_summaries, list):
            for item in drive_summaries:
                if not isinstance(item, dict):
                    continue
                summary_text = item.get("summary_text")
                if isinstance(summary_text, str) and summary_text.strip():
                    return f"{summary_text.strip()} が前景にあるから、今のうちに少しだけ前へ出てみるね。"
        return "今の文脈には少し前へ出る理由があると見て、そっと声をかけるね。"

    def _should_mock_vision_capture_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict) and ongoing_action_summary.get("status") == "waiting_result":
            return False
        if not self._mock_capability_available(capability_decision_view, "vision.capture"):
            return False
        markers = (
            "画面",
            "スクリーン",
            "今見えて",
            "見えている",
            "表示",
            "ウィンドウ",
            "キャプチャ",
            "デスクトップ",
        )
        if not any(marker in normalized for marker in markers):
            return False
        action_markers = (
            "見て",
            "見える",
            "確認",
            "読んで",
            "教えて",
            "何",
            "どう",
        )
        return any(marker in normalized for marker in action_markers)

    def _should_mock_external_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "external.status"):
            return False
        if self._mock_external_status_service(normalized) is None:
            return False
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "状況",
            "状態",
            "知りたい",
        )
        return any(marker in normalized for marker in action_markers)

    def _should_mock_schedule_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "schedule.status"):
            return False
        schedule_markers = (
            "予定",
            "カレンダー",
            "スケジュール",
            "このあと",
            "今日",
            "近日",
        )
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "知りたい",
        )
        return any(marker in normalized for marker in schedule_markers) and any(
            marker in normalized for marker in action_markers
        )

    def _mock_schedule_status_input(self, normalized: str) -> dict[str, str]:
        if "今日" in normalized:
            return {"range": "today"}
        if "このあと" in normalized:
            return {"range": "upcoming"}
        return {"range": "near_future"}

    def _mock_external_status_input(self, normalized: str) -> dict[str, str]:
        service = self._mock_external_status_service(normalized)
        if service is None:
            service = "external_service"
        return {
            "service": service,
        }

    def _mock_external_status_service(self, normalized: str) -> str | None:
        lowered = normalized.lower()
        if "github" in lowered or any(token in normalized for token in ("GitHub", "プルリク", "レビュー")):
            return "github"
        if "calendar" in lowered or any(token in normalized for token in ("カレンダー", "スケジュール")):
            return "calendar"
        return None

    def _mock_capability_available(
        self,
        capability_decision_view: list[dict[str, Any]] | None,
        capability_id: str,
    ) -> bool:
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == capability_id and item.get("available") is True:
                return True
        return False

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

    def _secondary_recall_focuses(self, recall_hint: dict[str, Any]) -> set[str]:
        # 収集
        secondary_recall_focuses: set[str] = set()
        for focus in recall_hint.get("secondary_recall_focuses", []):
            if isinstance(focus, str) and focus in RECALL_FOCUS_VALUES:
                secondary_recall_focuses.add(focus)

        # 結果
        return secondary_recall_focuses

    def generate_memory_interpretation(
        self,
        role_definition: dict,
        input_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # Episode要約
        normalized = input_text.strip()
        episode = {
            "episode_type": self._mock_episode_type(recall_hint["primary_recall_focus"]),
            "episode_series_id": None,
            "primary_scope_type": self._mock_primary_scope_type(recall_hint["primary_recall_focus"]),
            "primary_scope_key": self._mock_primary_scope_key(recall_hint["primary_recall_focus"]),
            "summary_text": normalized or "空の入力だった。",
            "outcome_text": reply_text or decision["reason_summary"],
            "open_loops": self._mock_open_loops(normalized, recall_hint["primary_recall_focus"]),
            "salience": 0.72 if normalized else 0.2,
        }

        # 候補memory unit群
        candidate_memory_units = self._mock_candidate_memory_units(normalized)

        # episode affect生成
        episode_affects = self._mock_episode_affects(normalized)

        # payload作成
        payload = {
            "episode": episode,
            "candidate_memory_units": candidate_memory_units,
            "episode_affects": episode_affects,
        }
        validate_memory_interpretation_contract(payload)
        return payload

    def generate_memory_reflection_summary(
        self,
        role_definition: dict,
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # evidence pack
        scope_type = str(evidence_pack.get("scope_type") or "")
        scope_key = str(evidence_pack.get("scope_key") or "")
        counts = evidence_pack.get("evidence_counts", {})
        open_loop_count = counts.get("open_loops", 0) if isinstance(counts, dict) else 0
        summary_status = str(evidence_pack.get("summary_status_candidate") or "inferred")
        persona = evidence_pack.get("persona")
        mood_state = evidence_pack.get("mood_state")
        affect_state = evidence_pack.get("affect_state")
        theme = self._mock_reflection_theme(
            evidence_pack.get("memory_units"),
            mood_state=mood_state,
            affect_state=affect_state,
        )
        persona_lead = self._mock_reflection_persona_lead(persona)

        # 文面
        if scope_type == "topic":
            topic_label = self._mock_reflection_scope_label(scope_key)
            if int(open_loop_count) > 0:
                summary_text = f"最近は {topic_label} に関する話題が未完了の流れを含みながら続いている。"
            else:
                summary_text = f"最近は {topic_label} に関する話題が繰り返し現れている。"
        elif scope_type == "relationship":
            relation_label = (
                "あなたとのやり取り"
                if scope_key == "self|user"
                else f"{self._mock_reflection_scope_label(scope_key)} の関係文脈"
            )
            if int(open_loop_count) > 0:
                summary_text = f"最近の{relation_label}では、{persona_lead}{theme}がありつつ、続きを確かめる流れが続いている。"
            elif summary_status == "confirmed":
                summary_text = f"最近の{relation_label}では、{persona_lead}{theme}が少しずつ安定している。"
            else:
                summary_text = f"最近の{relation_label}では、{persona_lead}{theme}がゆるやかに積み上がっている。"
        elif scope_type == "self":
            if int(open_loop_count) > 0:
                summary_text = f"最近の自分側の応答では、{persona_lead}{theme}があり、継続中の確認事項も抱えている。"
            else:
                summary_text = f"最近の自分側の応答では、{persona_lead}{theme}が続いている。"
        else:
            summary_text = f"最近のあなたに関するやり取りでは、{theme}の理解が少しずつ積み上がっている。"

        # payload
        payload = {
            "summary_text": summary_text[:140].replace("\n", " ").strip(),
        }
        validate_memory_reflection_summary_contract(payload)
        return payload

    def generate_event_evidence(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # source pack
        primary_recall_focus = str(source_pack.get("primary_recall_focus") or "user")
        time_reference = str(source_pack.get("time_reference") or "none")
        selection_basis = source_pack.get("selection_basis", {})
        event = source_pack.get("event", {})
        retrieval_sections = selection_basis.get("retrieval_sections", []) if isinstance(selection_basis, dict) else []
        source_summaries = selection_basis.get("source_summaries", []) if isinstance(selection_basis, dict) else []
        kind = str(event.get("kind") or "event").strip() or "event"
        event_text = self._mock_event_evidence_text(event.get("text"))
        source_summary = self._mock_event_evidence_text(source_summaries[0] if source_summaries else None)
        reason_summary = self._mock_event_evidence_text(event.get("reason_summary"))
        result_kind = str(event.get("result_kind") or "").strip()
        section_label = self._mock_event_evidence_section_label(retrieval_sections[0] if retrieval_sections else None)

        # slot 群
        anchor_prefix = "前回の" if primary_recall_focus == "episodic" or time_reference == "past" else "そのときの"
        if kind == "decision":
            anchor = f"{anchor_prefix}{section_label}の判断場面"
        elif kind == "reply":
            anchor = f"{anchor_prefix}{section_label}への返答場面"
        elif kind == "conversation_input":
            anchor = f"{anchor_prefix}{section_label}の会話場面"
        else:
            anchor = f"{anchor_prefix}{section_label}に関する場面"

        topic = event_text or source_summary

        decision_or_result = None
        if kind == "decision":
            if reason_summary is not None:
                decision_or_result = reason_summary
            elif result_kind:
                decision_or_result = f"{result_kind} を選ぶ流れになった。"
            else:
                decision_or_result = "その場で応答方針を決めた。"
        elif kind == "reply" and event_text is not None:
            decision_or_result = f"{event_text} と返した。"

        tone_or_note = None
        if primary_recall_focus in {"user", "state"}:
            tone_or_note = "様子を確かめながら進める空気だった。"
        elif kind == "decision" and result_kind == "pending_intent":
            tone_or_note = "その場では返さず、後で触れる含みを残した。"
        elif kind == "reply":
            tone_or_note = "前の流れを受けて返していた。"

        payload = {
            "anchor": anchor,
            "topic": topic,
            "decision_or_result": decision_or_result,
            "tone_or_note": tone_or_note,
        }
        validate_event_evidence_contract(payload)
        return payload

    def generate_recall_pack_selection(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # source pack
        recall_hint = source_pack.get("recall_hint", {})
        candidate_sections = source_pack.get("candidate_sections", [])
        conflicts = source_pack.get("conflicts", [])
        ordered_section_names = self._mock_recall_pack_section_order(recall_hint, candidate_sections)

        # section selection
        section_lookup = {
            section["section_name"]: section
            for section in candidate_sections
            if isinstance(section, dict) and isinstance(section.get("section_name"), str)
        }
        section_selection: list[dict[str, Any]] = []
        used_candidate_refs: set[str] = set()
        for section_name in ordered_section_names:
            section = section_lookup.get(section_name)
            if not isinstance(section, dict):
                continue
            candidates = section.get("candidates", [])
            if not isinstance(candidates, list):
                continue
            ordered_candidates = sorted(
                (candidate for candidate in candidates if isinstance(candidate, dict)),
                key=lambda candidate: self._mock_recall_pack_candidate_score(candidate, recall_hint),
                reverse=True,
            )
            candidate_refs: list[str] = []
            for candidate in ordered_candidates:
                candidate_ref = candidate.get("candidate_ref")
                if not isinstance(candidate_ref, str) or not candidate_ref.strip():
                    continue
                normalized_ref = candidate_ref.strip()
                if normalized_ref in used_candidate_refs:
                    continue
                candidate_refs.append(normalized_ref)
                used_candidate_refs.add(normalized_ref)
            if candidate_refs:
                section_selection.append(
                    {
                        "section_name": section_name,
                        "candidate_refs": candidate_refs,
                    }
                )

        # conflict summaries
        conflict_summaries = [
            {
                "conflict_ref": conflict["conflict_ref"],
                "summary_text": self._mock_recall_pack_conflict_summary(conflict),
            }
            for conflict in conflicts
            if isinstance(conflict, dict)
            and isinstance(conflict.get("conflict_ref"), str)
        ]

        # payload
        payload = {
            "section_selection": section_selection,
            "conflict_summaries": conflict_summaries,
        }
        validate_recall_pack_selection_contract(payload, source_pack=source_pack)
        return payload

    def generate_pending_intent_selection(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # source pack
        trigger_kind = str(source_pack.get("trigger_kind") or "wake")
        candidates = [
            candidate
            for candidate in source_pack.get("candidates", [])
            if isinstance(candidate, dict)
        ]

        # 候補なし
        if not candidates:
            payload = {
                "selected_candidate_ref": "none",
                "selection_reason": "今は再評価に使える保留候補が見当たらない。",
            }
            validate_pending_intent_selection_contract(payload, source_pack=source_pack)
            return payload

        # 最良候補
        scored_candidates = sorted(
            candidates,
            key=lambda candidate: self._mock_pending_intent_candidate_score(candidate, source_pack),
            reverse=True,
        )
        best_candidate = scored_candidates[0]
        best_score = self._mock_pending_intent_candidate_score(best_candidate, source_pack)
        threshold = self._mock_pending_intent_selection_threshold(trigger_kind, len(candidates))

        # payload
        if best_score >= threshold:
            payload = {
                "selected_candidate_ref": str(best_candidate["candidate_ref"]).strip(),
                "selection_reason": self._mock_pending_intent_selection_reason(
                    candidate=best_candidate,
                    source_pack=source_pack,
                    selected=True,
                ),
            }
        else:
            payload = {
                "selected_candidate_ref": "none",
                "selection_reason": self._mock_pending_intent_selection_reason(
                    candidate=best_candidate,
                    source_pack=source_pack,
                    selected=False,
                ),
            }

        # 検証
        validate_pending_intent_selection_contract(payload, source_pack=source_pack)
        return payload

    def generate_world_state(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # source pack
        trigger_kind = str(source_pack.get("trigger_kind") or "user_message")
        client_context = source_pack.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}
        current_input_summary = str(source_pack.get("current_input_summary") or "").strip()
        capability_result_summary = source_pack.get("capability_result_summary", {})
        if not isinstance(capability_result_summary, dict):
            capability_result_summary = {}

        # 候補群
        state_candidates: list[dict[str, Any]] = []
        screen_summary = self._mock_world_state_screen_summary(
            screen_context=source_pack.get("screen_context"),
            client_context=client_context,
        )
        if screen_summary is not None:
            state_candidates.append(
                {
                    "state_type": "screen",
                    "scope": "topic:current_work",
                    "summary_text": screen_summary,
                    "confidence_hint": "high" if trigger_kind == "desktop_watch" else "medium",
                    "salience_hint": "high",
                    "ttl_hint": "short",
                }
            )

        social_summary = self._mock_world_state_structured_summary(source_pack.get("social_context_context"))
        if social_summary is None:
            social_summary = self._mock_world_state_social_summary(client_context)
        if social_summary is not None:
            state_candidates.append(
                {
                    "state_type": "social_context",
                    "scope": "relationship:self|user",
                    "summary_text": social_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "short",
                }
            )

        user_state_summary = self._mock_world_state_user_summary(current_input_summary)
        if user_state_summary is not None:
            state_candidates.append(
                {
                    "state_type": "social_context",
                    "scope": "user",
                    "summary_text": user_state_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "medium",
                }
            )

        schedule_summary = self._mock_world_state_schedule_summary(source_pack.get("schedule_context"))
        if schedule_summary is not None:
            state_candidates.append(
                {
                    "state_type": "schedule",
                    "scope": "self",
                    "summary_text": schedule_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "high" if trigger_kind in {"wake", "background_wake"} else "medium",
                    "ttl_hint": "medium",
                }
            )

        for state_type, scope, summary_text in (
            (
                "external_service",
                "world",
                self._mock_world_state_structured_summary(source_pack.get("external_service_context")),
            ),
            (
                "body",
                "self",
                self._mock_world_state_structured_summary(source_pack.get("body_context")),
            ),
            (
                "device",
                "world",
                self._mock_world_state_structured_summary(source_pack.get("device_context")),
            ),
        ):
            if summary_text is None:
                continue
            state_candidates.append(
                {
                    "state_type": state_type,
                    "scope": scope,
                    "summary_text": summary_text,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "medium",
                }
            )

        for state_type, scope, summary_text in (
            (
                "environment",
                "world",
                self._mock_world_state_structured_summary(source_pack.get("environment_context")),
            ),
            (
                "location",
                "world",
                self._mock_world_state_structured_summary(source_pack.get("location_context")),
            ),
        ):
            if summary_text is None:
                continue
            state_candidates.append(
                {
                    "state_type": state_type,
                    "scope": scope,
                    "summary_text": summary_text,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "medium",
                }
            )

        if capability_result_summary.get("image_count") and not state_candidates and trigger_kind == "desktop_watch":
            state_candidates.append(
                {
                    "state_type": "screen",
                    "scope": "topic:current_work",
                    "summary_text": "画面の前景が変化している。",
                    "confidence_hint": "low",
                    "salience_hint": "medium",
                    "ttl_hint": "short",
                }
            )

        # payload
        payload = {
            "state_candidates": state_candidates[:4],
        }
        validate_world_state_contract(payload)
        return payload

    def generate_embeddings(
        self,
        role_definition: dict,
        texts: list[str],
        embedding_dimension: int,
    ) -> list[list[float]]:
        # model確認
        self._assert_mock_model(role_definition)

        # 結果
        return [
            self._mock_embedding_vector(text, embedding_dimension)
            for text in texts
        ]

    def _mock_episode_type(self, primary_recall_focus: str) -> str:
        # マッピング
        if primary_recall_focus in {"user", "state"}:
            return "consultation"
        if primary_recall_focus == "commitment":
            return "commitment_followup"
        if primary_recall_focus == "preference":
            return "preference_talk"
        if primary_recall_focus == "relationship":
            return "relationship_check"
        return "conversation"

    def _mock_primary_scope_type(self, primary_recall_focus: str) -> str:
        # マッピング
        if primary_recall_focus in {"commitment", "relationship"}:
            return "relationship"
        return "user"

    def _mock_primary_scope_key(self, primary_recall_focus: str) -> str:
        # マッピング
        if primary_recall_focus in {"commitment", "relationship"}:
            return "self|user"
        return "user"

    def _mock_world_state_screen_summary(
        self,
        *,
        screen_context: Any,
        client_context: dict[str, Any],
    ) -> str | None:
        if isinstance(screen_context, dict):
            for key in ("summary_text", "visual_summary_text"):
                summary_text = screen_context.get(key)
                if isinstance(summary_text, str) and summary_text.strip():
                    return summary_text.strip()
        window_title = client_context.get("window_title")
        if isinstance(window_title, str) and window_title.strip():
            return f"画面では {window_title.strip()} が前景にある。"
        active_app = client_context.get("active_app")
        if isinstance(active_app, str) and active_app.strip():
            return f"画面では {active_app.strip()} が前景にある。"
        return None

    def _mock_world_state_social_summary(self, client_context: dict[str, Any]) -> str | None:
        active_app = client_context.get("active_app")
        if not isinstance(active_app, str) or not active_app.strip():
            return None
        lowered = active_app.strip().lower()
        if any(token in lowered for token in ("slack", "discord", "teams", "zoom", "meet")):
            return f"{active_app.strip()} 上のやり取りが近い判断文脈として前景にある。"
        return None

    def _mock_world_state_user_summary(self, current_input_summary: str) -> str | None:
        if any(token in current_input_summary for token in ("会議中", "打ち合わせ", "ミーティング")):
            return "ユーザーは会議や打ち合わせの文脈にいる。"
        if any(token in current_input_summary for token in ("移動中", "電車", "外出")):
            return "ユーザーは移動や外出の途中にいる。"
        if any(token in current_input_summary for token in ("眠い", "寝る", "疲れた")):
            return "ユーザーは休息が必要そうな状態にある。"
        return None

    def _mock_world_state_structured_summary(self, context: Any) -> str | None:
        if not isinstance(context, dict):
            return None
        for key in (
            "summary_text",
            "status_text",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "social_context_summary",
            "environment_summary",
            "location_summary",
        ):
            summary_text = context.get(key)
            if isinstance(summary_text, str) and summary_text.strip():
                return summary_text.strip()
        return None

    def _mock_world_state_schedule_summary(self, schedule_context: Any) -> str | None:
        if not isinstance(schedule_context, dict):
            return None
        summary_text = schedule_context.get("summary_text")
        if isinstance(summary_text, str) and summary_text.strip():
            return summary_text.strip()
        pending_intent = schedule_context.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None
        intent_summary = pending_intent.get("intent_summary")
        if isinstance(intent_summary, str) and intent_summary.strip():
            return f"近いうちに {intent_summary.strip()} を見直す予定が前景にある。"
        reason_summary = pending_intent.get("reason_summary")
        if isinstance(reason_summary, str) and reason_summary.strip():
            return f"近い予定として {reason_summary.strip()}"
        return None

    def _mock_open_loops(self, normalized: str, primary_recall_focus: str) -> list[str]:
        # ループルール
        if primary_recall_focus in {"user", "commitment", "episodic"} and normalized:
            return [normalized[:80]]
        return []

    def _mock_candidate_memory_units(self, normalized: str) -> list[dict[str, Any]]:
        # 空
        if not normalized:
            return []

        # 構築群
        candidates: list[dict[str, Any]] = []
        correction_signal = self._mock_has_correction_signal(normalized)

        # 事実
        fact_candidate = self._mock_fact_candidate(normalized, correction_signal=correction_signal)
        if fact_candidate is not None:
            candidates.append(fact_candidate)

        if any(token in normalized for token in ("好き", "食べたい", "嫌い", "苦手")):
            candidates.append(
                {
                    "memory_type": "preference",
                    "scope_type": "user",
                    "scope_key": "user",
                    "subject_ref": "user",
                    "predicate": "likes",
                    "object_ref_or_value": self._mock_preference_object(normalized),
                    "summary_text": self._mock_preference_summary(normalized),
                    "status": "confirmed",
                    "commitment_state": None,
                    "confidence": 0.86,
                    "salience": 0.78,
                    "valid_from": None,
                    "valid_to": None,
                    "qualifiers": {
                        "polarity": self._mock_preference_polarity(normalized),
                        "source": "explicit_correction" if correction_signal else "explicit_statement",
                        "negates_previous": correction_signal,
                    },
                    "reason": "発話中に好みや苦手の明示が含まれており、必要なら既存理解の訂正にもなりうるため。",
                }
            )

        if any(token in normalized for token in ("約束", "今度", "また話", "また今度", "後で")):
            candidates.append(
                {
                    "memory_type": "commitment",
                    "scope_type": "relationship",
                    "scope_key": "self|user",
                    "subject_ref": "self",
                    "predicate": "talk_again",
                    "object_ref_or_value": "topic:conversation",
                    "summary_text": "あなたと後で続きを話す流れが残っている。",
                    "status": "inferred",
                    "commitment_state": "open",
                    "confidence": 0.74,
                    "salience": 0.88,
                    "valid_from": None,
                    "valid_to": None,
                    "qualifiers": {
                        "source": "inference",
                    },
                    "reason": "後続会話や約束を示す表現が含まれていたため。",
                }
            )

        if any(token in normalized for token in ("眠れて", "疲れ", "しんど", "つらい")):
            candidates.append(
                {
                    "memory_type": "interpretation",
                    "scope_type": "user",
                    "scope_key": "user",
                    "subject_ref": "user",
                    "predicate": "seems",
                    "object_ref_or_value": "state:tired",
                    "summary_text": "あなたは最近疲れや睡眠の問題を抱えていそうだ。",
                    "status": "inferred",
                    "commitment_state": None,
                    "confidence": 0.62,
                    "salience": 0.8,
                    "valid_from": None,
                    "valid_to": None,
                    "qualifiers": {
                        "domain": "health",
                        "source": "inference",
                    },
                    "reason": "体調や睡眠に関する示唆があったため。",
                }
            )

        return [self._mock_candidate_memo(candidate) for candidate in candidates]

    def _mock_candidate_memo(self, candidate: dict[str, Any]) -> dict[str, Any]:
        # mock 内部候補を memory_interpretation の候補メモ契約へ落とす。
        qualifiers = dict(candidate.get("qualifiers", {}))
        if candidate.get("commitment_state") is not None:
            qualifiers["commitment_state"] = candidate["commitment_state"]
        subject_hint = candidate.get("subject_ref")
        if candidate.get("scope_type") == "relationship":
            subject_hint = candidate.get("scope_key")
        return {
            "memory_type": candidate["memory_type"],
            "scope": candidate["scope_type"],
            "subject_hint": subject_hint,
            "predicate_hint": candidate["predicate"],
            "object_hint": candidate.get("object_ref_or_value") or "なし",
            "qualifiers_hint": qualifiers,
            "summary_text": candidate["summary_text"],
            "evidence_text": candidate["reason"],
            "confidence_hint": self._mock_confidence_hint(candidate["confidence"]),
        }

    def _mock_confidence_hint(self, confidence: Any) -> str:
        if isinstance(confidence, (int, float)) and confidence >= 0.8:
            return "high"
        if isinstance(confidence, (int, float)) and confidence >= 0.55:
            return "medium"
        return "low"

    def _mock_fact_candidate(self, normalized: str, *, correction_signal: bool) -> dict[str, Any] | None:
        # 日次リズム
        if "朝型" in normalized or "夜型" in normalized:
            object_ref = "rhythm:morning" if "朝型" in normalized else "rhythm:night"
            summary_text = "あなたの生活リズムは朝型寄りだ。" if "朝型" in normalized else "あなたの生活リズムは夜型寄りだ。"
            reason = "生活リズムに関する明示があり、継続理解として残す価値があるため。"
            if correction_signal:
                reason = "生活リズムに関する明示訂正があり、既存理解の更新候補になるため。"
            return {
                "memory_type": "fact",
                "scope_type": "user",
                "scope_key": "user",
                "subject_ref": "user",
                "predicate": "daily_rhythm",
                "object_ref_or_value": object_ref,
                "summary_text": summary_text,
                "status": "confirmed",
                "commitment_state": None,
                "confidence": 0.9,
                "salience": 0.76,
                "valid_from": None,
                "valid_to": None,
                "qualifiers": {
                    "source": "explicit_correction" if correction_signal else "explicit_statement",
                    "negates_previous": correction_signal,
                    "temporal_scope": "current",
                },
                "reason": reason,
            }

        # 作業スタイル
        if any(token in normalized for token in ("在宅", "リモート", "出社")):
            object_ref = "work:remote" if "在宅" in normalized or "リモート" in normalized else "work:office"
            summary_text = "あなたの働き方は在宅寄りだ。" if object_ref == "work:remote" else "あなたの働き方は出社寄りだ。"
            reason = "働き方に関する明示があり、継続理解として残す価値があるため。"
            if correction_signal:
                reason = "働き方に関する明示訂正があり、既存理解の更新候補になるため。"
            return {
                "memory_type": "fact",
                "scope_type": "user",
                "scope_key": "user",
                "subject_ref": "user",
                "predicate": "work_style",
                "object_ref_or_value": object_ref,
                "summary_text": summary_text,
                "status": "confirmed",
                "commitment_state": None,
                "confidence": 0.88,
                "salience": 0.72,
                "valid_from": None,
                "valid_to": None,
                "qualifiers": {
                    "source": "explicit_correction" if correction_signal else "explicit_statement",
                    "negates_previous": correction_signal,
                    "temporal_scope": "current",
                },
                "reason": reason,
            }

        # 結果
        return None

    def _mock_has_correction_signal(self, normalized: str) -> bool:
        # トークン群
        correction_tokens = (
            "いや",
            "違う",
            "勘違い",
            "じゃなく",
            "ではなく",
            "むしろ",
        )

        # 結果
        return any(token in normalized for token in correction_tokens)

    def _mock_preference_object(self, normalized: str) -> str:
        # マッピング
        if "辛" in normalized:
            return "food:spicy"
        if "甘" in normalized:
            return "food:sweet"
        if "食べ" in normalized:
            return "topic:food"
        return "preference:stated"

    def _mock_preference_summary(self, normalized: str) -> str:
        # マッピング
        if "嫌い" in normalized or "苦手" in normalized:
            return "あなたには苦手な好みがある。"
        return "あなたにははっきりした好みがある。"

    def _mock_preference_polarity(self, normalized: str) -> str:
        # マッピング
        if "嫌い" in normalized or "苦手" in normalized:
            return "negative"
        return "positive"

    def _mock_episode_affects(self, normalized: str) -> list[dict[str, Any]]:
        # 構築群
        updates: list[dict[str, Any]] = []
        if any(token in normalized for token in ("疲れ", "しんど", "つらい", "不安")):
            updates.append(
                {
                    "target_scope_type": "self",
                    "target_scope_key": "self",
                    "affect_label": "concern",
                    "vad": {"v": -0.34, "a": 0.42, "d": -0.18},
                    "intensity": 0.72,
                    "confidence": 0.82,
                    "summary_text": "相手のしんどさに反応して少し気が張った。",
                }
            )
        if any(token in normalized for token in ("嬉しい", "楽しい", "安心")):
            updates.append(
                {
                    "target_scope_type": "self",
                    "target_scope_key": "self",
                    "affect_label": "warmth",
                    "vad": {"v": 0.48, "a": 0.18, "d": 0.22},
                    "intensity": 0.65,
                    "confidence": 0.78,
                    "summary_text": "明るいやり取りに少し気持ちがほぐれた。",
                }
            )
        return updates

    def _mock_reflection_theme(
        self,
        memory_units: Any,
        *,
        mood_state: Any = None,
        affect_state: Any = None,
    ) -> str:
        mood_summary = self._mock_reflection_support_summary(mood_state)
        if mood_summary is not None:
            return mood_summary
        affect_summary = self._mock_reflection_affect_summary(affect_state)
        if affect_summary is not None:
            return affect_summary

        # 既存 memory unit から主題を拾う。
        if isinstance(memory_units, list):
            for unit in memory_units:
                if not isinstance(unit, dict):
                    continue
                predicate = unit.get("predicate")
                if predicate == "system_status":
                    return "動作状態"
                if predicate == "daily_rhythm":
                    return "生活リズム"
                if predicate == "work_style":
                    return "働き方"
                if predicate == "likes":
                    return "好み"
                if predicate == "talk_again":
                    return "続きを話す流れ"
                if predicate == "seems":
                    return "状態理解"

                summary_text = unit.get("summary_text")
                if isinstance(summary_text, str):
                    normalized = summary_text.strip().rstrip("。")
                    if normalized:
                        return normalized[:18]

        # 既定
        return "やり取りの傾向"

    def _mock_reflection_support_summary(self, mood_state: Any) -> str | None:
        if not isinstance(mood_state, dict):
            return None
        summary_text = mood_state.get("summary_text")
        if not isinstance(summary_text, str):
            return None
        normalized = summary_text.strip().rstrip("。")
        if not normalized:
            return None
        if "緊張" in normalized or "慎重" in normalized:
            return "慎重さ"
        if "落ち着" in normalized or "前向き" in normalized:
            return "落ち着き"
        if "力を抜" in normalized or "静か" in normalized:
            return "静かな整え方"
        return normalized[:18]

    def _mock_reflection_affect_summary(self, affect_state: Any) -> str | None:
        if not isinstance(affect_state, list):
            return None
        for item in affect_state:
            if not isinstance(item, dict):
                continue
            affect_label = str(item.get("affect_label") or "").strip()
            if affect_label in {"安心", "信頼", "好意"}:
                return "信頼感"
            if affect_label in {"不安", "緊張", "concern"}:
                return "気がかり"
            summary_text = item.get("summary_text")
            if isinstance(summary_text, str):
                normalized = summary_text.strip().rstrip("。")
                if "負担" in normalized or "気にかけ" in normalized:
                    return "相手の負担への気がかり"
                if normalized:
                    return normalized[:24]
        return None

    def _mock_reflection_persona_lead(self, persona: Any) -> str:
        if not isinstance(persona, dict):
            return ""
        initiative_baseline = str(persona.get("initiative_baseline") or "").strip()
        if initiative_baseline == "low":
            return "無理を押しすぎず、"
        if initiative_baseline == "high":
            return "必要なら一歩前へ出ながら、"
        return ""

    def _mock_reflection_scope_label(self, scope_key: str) -> str:
        # 簡易表示
        normalized = scope_key.strip()
        if normalized.startswith("topic:"):
            return normalized.split(":", 1)[1]
        if normalized == "self|user":
            return "あなた"
        return normalized

    def _mock_recall_pack_section_order(
        self,
        recall_hint: dict[str, Any],
        candidate_sections: list[Any],
    ) -> list[str]:
        # 利用可能 section 群
        available_sections = [
            section.get("section_name")
            for section in candidate_sections
            if isinstance(section, dict) and section.get("section_name") in RECALL_PACK_SECTION_NAMES
        ]

        # 主順序
        primary_recall_focus = str(recall_hint.get("primary_recall_focus") or "user")
        ordered = self._mock_recall_pack_primary_section_order(primary_recall_focus)

        # 副次補正
        boosted_sections: list[str] = []
        for focus in self._secondary_recall_focuses(recall_hint):
            for section_name in self._mock_recall_pack_primary_section_order(focus)[:2]:
                if section_name not in boosted_sections:
                    boosted_sections.append(section_name)
        if recall_hint.get("time_reference") == "past" and "episodic_evidence" in available_sections:
            boosted_sections.insert(0, "episodic_evidence")

        # 統合
        merged: list[str] = []
        for section_name in [*boosted_sections, *ordered, *available_sections]:
            if section_name not in available_sections or section_name in merged:
                continue
            merged.append(section_name)
        return merged

    def _mock_recall_pack_primary_section_order(self, primary_recall_focus: str) -> list[str]:
        if primary_recall_focus == "commitment":
            return [
                "active_commitments",
                "relationship_model",
                "episodic_evidence",
                "user_model",
                "active_topics",
                "self_model",
            ]
        if primary_recall_focus == "relationship":
            return [
                "relationship_model",
                "user_model",
                "episodic_evidence",
                "active_commitments",
                "active_topics",
                "self_model",
            ]
        if primary_recall_focus == "user":
            return [
                "user_model",
                "relationship_model",
                "active_topics",
                "episodic_evidence",
                "active_commitments",
                "self_model",
            ]
        if primary_recall_focus == "episodic":
            return [
                "episodic_evidence",
                "active_topics",
                "user_model",
                "relationship_model",
                "active_commitments",
                "self_model",
            ]
        if primary_recall_focus == "state":
            return [
                "user_model",
                "active_topics",
                "relationship_model",
                "episodic_evidence",
                "active_commitments",
                "self_model",
            ]
        return [
            "user_model",
            "relationship_model",
            "active_topics",
            "active_commitments",
            "episodic_evidence",
            "self_model",
        ]

    def _mock_recall_pack_candidate_score(
        self,
        candidate: dict[str, Any],
        recall_hint: dict[str, Any],
    ) -> float:
        # 基底
        score = float(candidate.get("salience", 0.0))
        if candidate.get("retrieval_lane") == "structured":
            score += 0.04
        association_score = candidate.get("association_score")
        if isinstance(association_score, (int, float)):
            score += float(association_score) * 0.03

        # 文脈補正
        primary_recall_focus = str(recall_hint.get("primary_recall_focus") or "user")
        time_reference = str(recall_hint.get("time_reference") or "none")
        source_kind = str(candidate.get("source_kind") or "")
        scope_type = str(candidate.get("scope_type") or candidate.get("primary_scope_type") or "")
        if primary_recall_focus == "commitment":
            if candidate.get("memory_type") == "commitment":
                score += 0.12
            if candidate.get("commitment_state") in {"open", "waiting_confirmation", "on_hold"}:
                score += 0.08
            if isinstance(candidate.get("open_loops"), list) and candidate["open_loops"]:
                score += 0.06
        if primary_recall_focus == "episodic" and source_kind == "episode":
            score += 0.12
        if primary_recall_focus == "relationship" and scope_type == "relationship":
            score += 0.08
        if primary_recall_focus in {"user", "state"} and scope_type in {"user", "topic"}:
            score += 0.06
        if time_reference == "past" and source_kind == "episode":
            score += 0.05

        # 結果
        return score

    def _mock_recall_pack_conflict_summary(self, conflict: dict[str, Any]) -> str:
        # variant summary 群
        compact_summaries: list[str] = []
        for value in conflict.get("variant_summaries", []):
            compact_value = self._mock_event_evidence_text(value)
            if compact_value is None:
                continue
            compact_summaries.append(compact_value.rstrip("。!?！？"))
        if len(compact_summaries) >= 2:
            summary_text = f"{compact_summaries[0]} と {compact_summaries[1]} の理解が並んでいる。"
        elif compact_summaries:
            summary_text = f"{compact_summaries[0]} をめぐる理解が揺れている。"
        else:
            compare_key = conflict.get("compare_key", {})
            predicate = str(compare_key.get("predicate") or "").strip()
            if predicate == "talk_again":
                summary_text = "続きをどう扱うかについて異なる理解が並んでいる。"
            elif predicate == "likes":
                summary_text = "好みの理解について異なる線が並んでいる。"
            elif predicate == "seems":
                summary_text = "状態理解について異なる見立てが並んでいる。"
            else:
                summary_text = "同じ対象について異なる理解が並んでいる。"
        if len(summary_text) <= 120:
            return summary_text
        return summary_text[:119].rstrip("。 ") + "。"

    def _mock_pending_intent_candidate_score(
        self,
        candidate: dict[str, Any],
        source_pack: dict[str, Any],
    ) -> float:
        # コンテキスト特徴
        input_context = source_pack.get("input_context", {})
        recent_turns = source_pack.get("recent_turns", [])
        trigger_kind = str(source_pack.get("trigger_kind") or "wake")
        context_text = self._mock_pending_intent_context_text(input_context, recent_turns)
        candidate_text = " ".join(
            value.strip()
            for value in (
                str(candidate.get("intent_summary") or ""),
                str(candidate.get("reason_summary") or ""),
            )
            if value.strip()
        )
        context_features = self._mock_pending_intent_text_features(context_text)
        candidate_features = self._mock_pending_intent_text_features(candidate_text)

        # 基本スコア
        score = 0.0
        if candidate_features and context_features:
            overlap_count = len(candidate_features & context_features)
            score += min(overlap_count / 8.0, 1.0) * 0.8
        if str(candidate.get("intent_kind") or "") == "conversation_follow_up" and recent_turns:
            score += 0.15

        # trigger 補正
        if trigger_kind == "desktop_watch" and isinstance(input_context, dict):
            if any(
                isinstance(input_context.get(key), str) and str(input_context.get(key)).strip()
                for key in ("active_app", "window_title", "locale")
            ):
                score += 0.12
            image_count = input_context.get("image_count")
            if isinstance(image_count, int) and image_count > 0:
                score += 0.05

        # 時刻補助
        minutes_since_updated = candidate.get("minutes_since_updated")
        if isinstance(minutes_since_updated, int):
            if minutes_since_updated <= 30:
                score += 0.15
            elif minutes_since_updated <= 120:
                score += 0.08
        minutes_until_expiry = candidate.get("minutes_until_expiry")
        if isinstance(minutes_until_expiry, int) and minutes_until_expiry <= 60:
            score += 0.05

        # 結果
        return score

    def _mock_pending_intent_selection_threshold(self, trigger_kind: str, candidate_count: int) -> float:
        # base
        threshold = 0.4 if trigger_kind == "desktop_watch" else 0.5
        if candidate_count == 1:
            threshold -= 0.1
        return threshold

    def _mock_pending_intent_selection_reason(
        self,
        *,
        candidate: dict[str, Any],
        source_pack: dict[str, Any],
        selected: bool,
    ) -> str:
        # トリガー
        trigger_kind = str(source_pack.get("trigger_kind") or "wake")
        input_context = source_pack.get("input_context", {})
        has_foreground_context = isinstance(input_context, dict) and any(
            isinstance(input_context.get(key), str) and str(input_context.get(key)).strip()
            for key in ("active_app", "window_title", "locale")
        )

        # 選択
        if selected:
            if trigger_kind == "desktop_watch" and has_foreground_context:
                return "前景の文脈と保留意図の継続性が噛み合っており、今はこの候補を再評価に乗せる自然さがある。"
            return "直近のやり取りとの連続性があり、今はこの候補を再評価に乗せる自然さがある。"

        # 非選択
        if trigger_kind in {"wake", "background_wake"}:
            return "今の起床機会だけでは、この保留候補を前に出す自然さがまだ弱い。"
        if has_foreground_context:
            return "前景の文脈だけでは、この保留候補を前に出す決め手がまだ弱い。"
        return "今の入力だけでは、この保留候補を前に出す決め手がまだ弱い。"

    def _mock_pending_intent_context_text(
        self,
        input_context: Any,
        recent_turns: list[dict[str, Any]],
    ) -> str:
        # 入力文脈
        parts: list[str] = []
        if isinstance(input_context, dict):
            for key in ("source", "active_app", "window_title", "locale"):
                value = input_context.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())

        # recent_turns
        for turn in recent_turns[-4:]:
            if not isinstance(turn, dict):
                continue
            text = turn.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

        # 結果
        return " ".join(parts)

    def _mock_pending_intent_text_features(self, value: str) -> set[str]:
        # 正規化
        normalized = re.sub(r"\s+", "", value)
        normalized = re.sub(r"[。、「」『』（）()［］【】,，.．!！?？:：;；]", "", normalized)
        if len(normalized) < 2:
            return set()

        # 収集
        features: set[str] = set()
        for size in (2, 3):
            for index in range(len(normalized) - size + 1):
                features.add(normalized[index : index + size])

        # 結果
        return features

    def _mock_event_evidence_section_label(self, section_name: Any) -> str:
        if section_name == "active_commitments":
            return "約束の流れ"
        if section_name == "episodic_evidence":
            return "前の出来事"
        if section_name == "relationship_model":
            return "関係の流れ"
        if section_name == "user_model":
            return "あなたの近況"
        if section_name == "active_topics":
            return "話題の流れ"
        if section_name == "self_model":
            return "自分側の応答"
        return "やり取り"

    def _mock_event_evidence_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        if not normalized:
            return None
        for delimiter in ("。", "!", "！", "?", "？"):
            if delimiter not in normalized:
                continue
            head, _, _ = normalized.partition(delimiter)
            normalized = (head + delimiter).strip()
            break
        if len(normalized) <= 72:
            return normalized
        return normalized[:71].rstrip() + "…"

    def _mock_embedding_vector(self, text: str, embedding_dimension: int) -> list[float]:
        # 空確認
        normalized = text.strip()
        if embedding_dimension <= 0:
            raise LLMError("embedding_dimension は正の値である必要があります。")
        if not normalized:
            return [0.0] * embedding_dimension

        # 蓄積
        values = [0.0] * embedding_dimension
        tokens = [normalized]
        if len(normalized) >= 2:
            tokens.extend(normalized[index : index + 2] for index in range(len(normalized) - 1))
        if len(normalized) >= 3:
            tokens.extend(normalized[index : index + 3] for index in range(len(normalized) - 2))

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            primary_index = int.from_bytes(digest[:4], "little") % embedding_dimension
            secondary_index = int.from_bytes(digest[4:8], "little") % embedding_dimension
            primary_value = 0.5 + (digest[8] / 255.0)
            secondary_value = 0.5 + (digest[9] / 255.0)
            values[primary_index] += primary_value
            values[secondary_index] -= secondary_value * 0.25

        # 正規化
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0.0:
            return [0.0] * embedding_dimension
        return [value / norm for value in values]

    def _assert_mock_model(self, role_definition: dict) -> None:
        # モデル確認
        model = role_definition.get("model")
        if isinstance(model, str) and model.strip().startswith("mock"):
            return
        raise LLMError(f"未対応の mock model です: {model}")
