from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from otomekairo.llm_contracts import (
    INTENT_VALUES,
    RECALL_PACK_SECTION_NAMES,
    LLMError,
    validate_decision_contract,
    validate_event_evidence_contract,
    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
    validate_recall_pack_selection_contract,
    validate_recall_hint_contract,
)


# モッククライアント
@dataclass(slots=True)
class MockLLMClient:
    def generate_recall_hint(
        self,
        role_definition: dict,
        observation_text: str,
        recent_turns: list[dict],
        current_time: str,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # ヒューリスティックintent
        normalized = observation_text.strip()
        lower_text = normalized.lower()

        primary_intent = "smalltalk"
        secondary_intents: list[str] = []
        time_reference = "none"

        if any(token in normalized for token in ("この前", "昨日", "前に", "続き")):
            primary_intent = "reminisce"
            time_reference = "past"
        elif any(token in normalized for token in ("約束", "今度", "また話", "また今度")):
            primary_intent = "commitment_check"
            time_reference = "future"
        elif any(token in normalized for token in ("相談", "どうしたら", "悩", "困って")):
            primary_intent = "consult"
            time_reference = "recent"
        elif any(token in normalized for token in ("元気", "大丈夫", "調子", "眠れて")):
            primary_intent = "check_state"
            time_reference = "recent"
        elif any(token in normalized for token in ("好き", "嫌い", "食べたい", "食べ")):
            primary_intent = "preference_query"
            time_reference = "persistent"
        elif any(token in normalized for token in ("関係", "距離", "話しにく")):
            primary_intent = "meta_relationship"
            time_reference = "recent"
        elif lower_text.endswith("?") or "?" in lower_text:
            primary_intent = "fact_query"

        # 副次intent
        if primary_intent in {"consult", "check_state"} and recent_turns:
            secondary_intents.append("reminisce")

        # focus scope判定
        focus_scopes = ["user"]
        if primary_intent == "meta_relationship":
            focus_scopes.append("relationship:self|user")
        if primary_intent == "preference_query":
            focus_scopes.append("topic:preference")
        if primary_intent == "commitment_check":
            focus_scopes.append("relationship:self|user")

        # 言及hint群
        mentioned_entities = self._mock_mentioned_entities(normalized)
        mentioned_topics = self._mock_mentioned_topics(normalized)

        # payload作成
        payload = {
            "primary_intent": primary_intent,
            "secondary_intents": secondary_intents[:2],
            "confidence": 0.7 if normalized else 0.1,
            "time_reference": time_reference,
            "focus_scopes": focus_scopes[:4],
            "mentioned_entities": mentioned_entities[:4],
            "mentioned_topics": mentioned_topics[:4],
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
            topics.append(topic_name)
            if len(topics) >= 4:
                break

        # 結果
        return topics

    def generate_decision(
        self,
        role_definition: dict,
        persona: dict,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        _ = persona
        self._assert_mock_model(role_definition)

        # コンテキスト
        normalized = observation_text.strip()
        primary_intent = recall_hint["primary_intent"]
        secondary_intents = self._secondary_intents(recall_hint)
        conflicts = recall_pack.get("conflicts", [])
        active_commitments = recall_pack.get("active_commitments", [])
        episodic_evidence = recall_pack.get("episodic_evidence", [])
        event_evidence = recall_pack.get("event_evidence", [])
        active_topics = recall_pack.get("active_topics", [])
        surface_affects = affect_context.get("surface", [])

        # decisionルール
        if not normalized:
            payload = {
                "kind": "noop",
                "reason_code": "empty_observation",
                "reason_summary": "Observation text was empty after normalization.",
                "requires_confirmation": False,
                "pending_intent": None,
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
                    primary_intent=primary_intent,
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
        elif primary_intent == "commitment_check" and active_commitments:
            payload = {
                "kind": "reply",
                "reason_code": "active_commitment",
                "reason_summary": "進行中の約束や保留があり、継続会話として返答する。",
                "requires_confirmation": False,
                "pending_intent": None,
            }
        elif "reminisce" in secondary_intents and episodic_evidence:
            payload = {
                "kind": "reply",
                "reason_code": "secondary_reminisce",
                "reason_summary": "補助意図として回想があり、関連エピソードを踏まえて返答する。",
                "requires_confirmation": False,
                "pending_intent": None,
            }
        elif surface_affects and surface_affects[0]["affect_label"] in {"不安", "緊張", "迷い", "concern"}:
            payload = {
                "kind": "reply",
                "reason_code": "affect_caution",
                "reason_summary": "AffectContext に慎重さを要する感情があり、確認寄りに返す。",
                "requires_confirmation": True,
                "pending_intent": None,
            }
        else:
            payload = {
                "kind": "reply",
                "reason_code": f"intent:{primary_intent}",
                "reason_summary": "A normal conversation reply is appropriate for the current observation.",
                "requires_confirmation": primary_intent in {"fact_query", "meta_relationship"},
                "pending_intent": None,
            }

        # 検証
        validate_decision_contract(payload)
        return payload

    def generate_reply(
        self,
        role_definition: dict,
        persona: dict,
        observation_text: str,
        recent_turns: list[dict],
        time_context: dict[str, Any],
        affect_context: dict[str, list[dict[str, Any]]],
        recall_hint: dict,
        recall_pack: dict[str, Any],
        decision: dict,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # コンテキスト
        persona_prompt = str(persona.get("persona_prompt", "")).strip()
        primary_intent = recall_hint["primary_intent"]
        secondary_intents = self._secondary_intents(recall_hint)
        text = observation_text.strip()
        conflict_items = recall_pack.get("conflicts", [])
        commitment_items = recall_pack.get("active_commitments", [])
        relationship_items = recall_pack.get("relationship_model", [])
        user_items = recall_pack.get("user_model", [])
        topic_items = recall_pack.get("active_topics", [])
        episode_items = recall_pack.get("episodic_evidence", [])
        event_items = recall_pack.get("event_evidence", [])
        surface_affects = affect_context.get("surface", [])
        conflict_item = conflict_items[0] if conflict_items else None
        commitment_item = commitment_items[0] if commitment_items else None
        relationship_item = relationship_items[0] if relationship_items else None
        user_item = user_items[0] if user_items else None
        topic_item = topic_items[0] if topic_items else None
        episode_item = episode_items[0] if episode_items else None
        event_item = event_items[0] if event_items else None
        surface_affect = surface_affects[0] if surface_affects else None
        event_basis = self._event_evidence_basis_text(event_item)

        # 注意プレフィックス
        caution_prefix = ""
        if conflict_item is not None:
            caution_prefix = "今は少し慎重に受け取っている。"
        elif surface_affect is not None and surface_affect["affect_label"] in {"不安", "緊張", "迷い", "concern"}:
            caution_prefix = "少し慎重に聞いているよ。"

        # 継続プレフィックス
        continuity_prefix = ""
        if primary_intent != "reminisce" and "reminisce" in secondary_intents:
            if episode_item is not None or event_basis is not None or recent_turns:
                continuity_prefix = "前の流れも踏まえると、"

        # 返信ルール
        if decision["requires_confirmation"]:
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
        elif primary_intent == "consult":
            if user_item is not None:
                reply_text = f"{caution_prefix}{continuity_prefix}{user_item['summary_text']} も踏まえて聞くね。{text} の中で、今いちばん困っている点をもう少し教えて。"
            else:
                reply_text = f"{caution_prefix}{continuity_prefix}状況は受け取ったよ。{text} の中で、今いちばん困っている点をもう少し教えて。"
        elif primary_intent == "commitment_check":
            if commitment_item is not None:
                if "どこまで" in text:
                    reply_text = f"{commitment_item['summary_text']} の続きとして受け取ったよ。いまはどの範囲まで進めたい？"
                else:
                    reply_text = f"{commitment_item['summary_text']} の続きとして受け取ったよ。{text} について、今回はどこまで進めたい？"
            elif event_basis is not None:
                reply_text = f"{event_basis} の続きとして受け取ったよ。{text} について、今回はどこまで進めたい？"
            else:
                reply_text = f"{caution_prefix}その流れは覚えている前提で話すね。{text} に関して、今回どこまで進めたい？"
        elif primary_intent == "reminisce":
            if episode_item is not None:
                reply_text = f"{episode_item['summary_text']} の流れとして受け取ったよ。{text} のどの部分からつなげたい？"
            elif event_basis is not None:
                reply_text = f"{event_basis} の場面として受け取ったよ。{text} のどの部分からつなげたい？"
            else:
                reply_text = f"{caution_prefix}その続きとして受け取ったよ。{text} のどの部分からつなげたい？"
        elif primary_intent == "preference_query":
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
        primary_intent: str,
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
            "dedupe_key": f"pending_intent:intent:{primary_intent}",
        }

    def _secondary_intents(self, recall_hint: dict[str, Any]) -> set[str]:
        # 収集
        secondary_intents: set[str] = set()
        for intent in recall_hint.get("secondary_intents", []):
            if isinstance(intent, str) and intent in INTENT_VALUES:
                secondary_intents.add(intent)

        # 結果
        return secondary_intents

    def generate_memory_interpretation(
        self,
        role_definition: dict,
        observation_text: str,
        recall_hint: dict,
        decision: dict,
        reply_text: str | None,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # Episode要約
        normalized = observation_text.strip()
        episode = {
            "episode_type": self._mock_episode_type(recall_hint["primary_intent"]),
            "episode_series_id": None,
            "primary_scope_type": self._mock_primary_scope_type(recall_hint["primary_intent"]),
            "primary_scope_key": self._mock_primary_scope_key(recall_hint["primary_intent"]),
            "summary_text": normalized or "空の観測だった。",
            "outcome_text": reply_text or decision["reason_summary"],
            "open_loops": self._mock_open_loops(normalized, recall_hint["primary_intent"]),
            "salience": 0.72 if normalized else 0.2,
        }

        # 候補memory unit群
        candidate_memory_units = self._mock_candidate_memory_units(normalized)

        # affectUpdates生成
        affect_updates = self._mock_affect_updates(normalized)

        # payload作成
        payload = {
            "episode": episode,
            "candidate_memory_units": candidate_memory_units,
            "affect_updates": affect_updates,
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
        theme = self._mock_reflection_theme(evidence_pack.get("memory_units"))

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
                summary_text = f"最近の{relation_label}では、{theme}を気にかけながら続きを確かめる流れが続いている。"
            elif summary_status == "confirmed":
                summary_text = f"最近の{relation_label}では、{theme}に関する理解が少しずつ安定している。"
            else:
                summary_text = f"最近の{relation_label}では、{theme}に関する流れがゆるやかに積み上がっている。"
        elif scope_type == "self":
            if int(open_loop_count) > 0:
                summary_text = f"最近の自分側の応答では、{theme}を保ちながら継続中の確認事項も抱えている。"
            else:
                summary_text = f"最近の自分側の応答では、{theme}に一定の傾向が見えている。"
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
        primary_intent = str(source_pack.get("primary_intent") or "smalltalk")
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
        anchor_prefix = "前回の" if primary_intent == "reminisce" or time_reference == "past" else "そのときの"
        if kind == "decision":
            anchor = f"{anchor_prefix}{section_label}の判断場面"
        elif kind == "reply":
            anchor = f"{anchor_prefix}{section_label}への返答場面"
        elif kind == "observation":
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
        if primary_intent in {"consult", "check_state"}:
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

    def _mock_episode_type(self, primary_intent: str) -> str:
        # マッピング
        if primary_intent in {"consult", "check_state"}:
            return "consultation"
        if primary_intent == "commitment_check":
            return "commitment_followup"
        if primary_intent == "preference_query":
            return "preference_talk"
        if primary_intent == "meta_relationship":
            return "relationship_check"
        return "conversation"

    def _mock_primary_scope_type(self, primary_intent: str) -> str:
        # マッピング
        if primary_intent in {"commitment_check", "meta_relationship"}:
            return "relationship"
        return "user"

    def _mock_primary_scope_key(self, primary_intent: str) -> str:
        # マッピング
        if primary_intent in {"commitment_check", "meta_relationship"}:
            return "self|user"
        return "user"

    def _mock_open_loops(self, normalized: str, primary_intent: str) -> list[str]:
        # ループルール
        if primary_intent in {"consult", "commitment_check", "reminisce"} and normalized:
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

        return candidates

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

    def _mock_affect_updates(self, normalized: str) -> list[dict[str, Any]]:
        # 構築群
        updates: list[dict[str, Any]] = []
        if any(token in normalized for token in ("疲れ", "しんど", "つらい", "不安")):
            updates.append(
                {
                    "layer": "surface",
                    "target_scope_type": "user",
                    "target_scope_key": "user",
                    "affect_label": "concern",
                    "intensity": 0.72,
                }
            )
        if any(token in normalized for token in ("嬉しい", "楽しい", "安心")):
            updates.append(
                {
                    "layer": "surface",
                    "target_scope_type": "user",
                    "target_scope_key": "user",
                    "affect_label": "warmth",
                    "intensity": 0.65,
                }
            )
        return updates

    def _mock_reflection_theme(self, memory_units: Any) -> str:
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
        primary_intent = str(recall_hint.get("primary_intent") or "smalltalk")
        ordered = self._mock_recall_pack_primary_section_order(primary_intent)

        # 副次補正
        boosted_sections: list[str] = []
        for intent in self._secondary_intents(recall_hint):
            for section_name in self._mock_recall_pack_primary_section_order(intent)[:2]:
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

    def _mock_recall_pack_primary_section_order(self, primary_intent: str) -> list[str]:
        if primary_intent == "commitment_check":
            return [
                "active_commitments",
                "relationship_model",
                "episodic_evidence",
                "user_model",
                "active_topics",
                "self_model",
            ]
        if primary_intent == "meta_relationship":
            return [
                "relationship_model",
                "user_model",
                "episodic_evidence",
                "active_commitments",
                "active_topics",
                "self_model",
            ]
        if primary_intent == "consult":
            return [
                "user_model",
                "relationship_model",
                "active_topics",
                "episodic_evidence",
                "active_commitments",
                "self_model",
            ]
        if primary_intent == "reminisce":
            return [
                "episodic_evidence",
                "active_topics",
                "user_model",
                "relationship_model",
                "active_commitments",
                "self_model",
            ]
        if primary_intent == "check_state":
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
        primary_intent = str(recall_hint.get("primary_intent") or "smalltalk")
        time_reference = str(recall_hint.get("time_reference") or "none")
        source_kind = str(candidate.get("source_kind") or "")
        scope_type = str(candidate.get("scope_type") or candidate.get("primary_scope_type") or "")
        if primary_intent == "commitment_check":
            if candidate.get("memory_type") == "commitment":
                score += 0.12
            if candidate.get("commitment_state") in {"open", "waiting_confirmation", "on_hold"}:
                score += 0.08
            if isinstance(candidate.get("open_loops"), list) and candidate["open_loops"]:
                score += 0.06
        if primary_intent == "reminisce" and source_kind == "episode":
            score += 0.12
        if primary_intent == "meta_relationship" and scope_type == "relationship":
            score += 0.08
        if primary_intent in {"consult", "check_state"} and scope_type in {"user", "topic"}:
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
            raise LLMError("embedding_dimension must be positive.")
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
        raise LLMError(f"Unsupported mock model: {model}")
