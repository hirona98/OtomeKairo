from __future__ import annotations

import re
from typing import Any

from otomekairo.llm.contracts import (
    RECALL_FOCUS_VALUES,
    RECALL_PACK_SECTION_NAMES,
    validate_answer_contract_contract,
    validate_event_evidence_contract,
    validate_initiative_entry_check_contract,
    validate_pending_intent_selection_contract,
    validate_recall_hint_contract,
    validate_recall_pack_selection_contract,
)


class LLMMockRecallMixin:
    def generate_answer_contract(
        self,
        role_definition: dict,
        input_text: str,
        recall_hint: dict[str, Any],
        current_time: str,
        *,
        persona_context: Any,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)
        _ = recall_hint
        _ = current_time
        _ = persona_context

        # モックは開発検証用の固定規則。実運用の契約判定は LLM が行う。
        text = input_text.strip()
        contract = "summary"
        boundary = "none"
        target_actor = "any"
        query_terms: list[str] = []
        reason_codes = ["general_answer"]
        if self._mock_contains_any(text, ("一字一句", "具体的な発言", "なんて言", "原文", "正確に再現")):
            contract = "exact_statement"
            reason_codes = ["verbatim_request"]
            if self._mock_contains_any(text, ("最初", "初めて", "初回", "初対面")):
                boundary = "first"
            elif self._mock_contains_any(text, ("直近", "最後", "最新", "この前")):
                boundary = "latest"
        elif self._mock_contains_any(text, ("最初", "初めて", "初回", "初対面")):
            contract = "exact_boundary"
            boundary = "first"
            reason_codes = ["boundary_request"]
        elif self._mock_contains_any(text, ("直近", "最後", "最新", "この前")):
            contract = "exact_boundary"
            boundary = "latest"
            reason_codes = ["boundary_request"]
        elif self._mock_contains_any(text, ("根拠", "なぜ", "どうして", "矛盾")):
            contract = "provenance"
            reason_codes = ["provenance_request"]

        if self._mock_contains_any(text, ("会話", "やり取り")):
            target_actor = "any"
        elif self._mock_contains_any(text, ("僕", "俺", "私の発言", "ユーザー")):
            target_actor = "user"
        elif self._mock_contains_any(text, ("君", "あなた", "人格", "AI")):
            target_actor = "assistant"

        payload = {
            "contract": contract,
            "reason_codes": reason_codes,
            "boundary": boundary,
            "target_actor": target_actor,
            "query_terms": query_terms,
        }
        validate_answer_contract_contract(payload)
        return payload

    def generate_recall_hint(
        self,
        role_definition: dict,
        input_text: str,
        recent_turns: list[dict],
        current_time: str,
        *,
        persona_context: Any,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)
        _ = persona_context

        # ヒューリスティックfocus
        normalized = input_text.strip()
        lower_text = normalized.lower()
        _ = current_time

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

    def _secondary_recall_focuses(self, recall_hint: dict[str, Any]) -> set[str]:
        # 収集
        secondary_recall_focuses: set[str] = set()
        for focus in recall_hint.get("secondary_recall_focuses", []):
            if isinstance(focus, str) and focus in RECALL_FOCUS_VALUES:
                secondary_recall_focuses.add(focus)

        # 結果
        return secondary_recall_focuses

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
        elif kind == "speech":
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
        elif kind == "speech" and event_text is not None:
            decision_or_result = f"{event_text} と返した。"

        tone_or_note = None
        if primary_recall_focus in {"user", "state"}:
            tone_or_note = "様子を確かめながら進める空気だった。"
        elif kind == "decision" and result_kind == "pending_intent":
            tone_or_note = "その場では返さず、後で触れる含みを残した。"
        elif kind == "speech":
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

    def generate_initiative_entry_check(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        activity_context = source_pack.get("activity_context")
        if self._mock_has_activity_transition(activity_context):
            payload = {
                "entry_kind": "enter",
                "entry_basis": "activity_mode_transition",
                "reason_summary": "活動が一区切りして切り替わったように見えるため、短く触れる自然さがある。",
            }
        else:
            payload = {
                "entry_kind": "skip",
                "entry_basis": "observation_only",
                "reason_summary": "定期観測だけでは外向きの自律判断へ進める理由がまだ弱い。",
            }
        validate_initiative_entry_check_contract(payload)
        return payload

    def _mock_has_activity_transition(self, activity_context: Any) -> bool:
        if not isinstance(activity_context, dict):
            return False
        current_activity = activity_context.get("current_activity")
        previous_activity = activity_context.get("previous_activity")
        if not isinstance(current_activity, dict) or not isinstance(previous_activity, dict):
            return False
        current_label = str(current_activity.get("label") or "").strip()
        previous_label = str(previous_activity.get("label") or "").strip()
        if not current_label or not previous_label:
            return False
        return current_label != previous_label

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
        memory_link_summary = candidate.get("memory_link_summary")
        if isinstance(memory_link_summary, dict):
            label_counts = memory_link_summary.get("label_counts", {})
            if isinstance(label_counts, dict):
                if int(label_counts.get("contradicts", 0) or 0) > 0:
                    score += 0.04
                if int(label_counts.get("supports", 0) or 0) > 0:
                    score += 0.03
                if int(label_counts.get("derived_from", 0) or 0) > 0:
                    score += 0.02
                if int(label_counts.get("about_same_scope", 0) or 0) > 0:
                    score += 0.02
                if int(label_counts.get("affects", 0) or 0) > 0:
                    score += 0.02

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
        threshold = 0.5
        _ = trigger_kind
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
        _ = candidate

        # 選択
        if selected:
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
