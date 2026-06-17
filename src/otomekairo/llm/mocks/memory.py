from __future__ import annotations

from typing import Any

from otomekairo.llm.contracts import (
    validate_memory_correction_reconciliation_contract,
    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
)


class LLMMockMemoryMixin:
    def generate_memory_interpretation(
        self,
        role_definition: dict,
        input_text: str,
        recall_hint: dict,
        decision: dict,
        speech_text: str | None,
        memory_context: dict[str, Any] | None = None,
        *,
        persona_context: Any,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)
        _ = memory_context
        _ = persona_context

        # Episode要約
        normalized = input_text.strip()
        episode = {
            "episode_type": self._mock_episode_type(recall_hint["primary_recall_focus"]),
            "episode_series_id": None,
            "primary_scope_type": self._mock_primary_scope_type(recall_hint["primary_recall_focus"]),
            "primary_scope_key": self._mock_primary_scope_key(recall_hint["primary_recall_focus"]),
            "summary_text": normalized or "空の入力だった。",
            "outcome_text": speech_text or decision["reason_summary"],
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
        persona = evidence_pack.get("persona_context")
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

    def generate_memory_correction_reconciliation(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)
        _ = source_pack

        # mock は訂正 reconciliation を自動選定しない。
        payload = {
            "correction_status": "no_correction",
            "selected_targets": [],
        }
        validate_memory_correction_reconciliation_contract(payload)
        return payload

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

        preference_dormant_request = (
            "辛" in normalized
            and any(token in normalized for token in ("扱わない", "出さない", "触れない", "忘れて"))
        )
        if any(token in normalized for token in ("好き", "食べたい", "嫌い", "苦手")) or preference_dormant_request:
            candidates.append(
                {
                    "memory_type": "preference",
                    "scope_type": "user",
                    "scope_key": "user",
                    "subject_ref": "user",
                    "predicate": "likes",
                    "object_ref_or_value": self._mock_preference_object(normalized),
                    "summary_text": self._mock_preference_summary(normalized),
                    "status": "dormant" if preference_dormant_request else "confirmed",
                    "commitment_state": None,
                    "confidence": 0.86,
                    "salience": 0.78,
                    "valid_from": None,
                    "valid_to": None,
                    "qualifiers": {
                        "polarity": self._mock_preference_polarity(normalized),
                        "source": "explicit_correction" if correction_signal else "explicit_statement",
                        "negates_previous": correction_signal,
                        "dormant": preference_dormant_request,
                    },
                    "reason": "発話中に好みや苦手の明示が含まれており、必要なら既存理解の訂正にもなりうるため。",
                }
            )

        if any(token in normalized for token in ("約束", "今度", "また話", "また今度", "後で")):
            commitment_state = "open"
            commitment_summary = "あなたと後で続きを話す流れが残っている。"
            if any(token in normalized for token in ("完了", "終わり", "済んだ")):
                commitment_state = "done"
                commitment_summary = "あなたと後で続きを話す流れは完了している。"
            elif any(token in normalized for token in ("キャンセル", "取り消し", "やめる")):
                commitment_state = "cancelled"
                commitment_summary = "あなたと後で続きを話す流れは取り消されている。"
            elif any(token in normalized for token in ("保留", "待って")):
                commitment_state = "on_hold"
                commitment_summary = "あなたと後で続きを話す流れは保留されている。"
            elif "確認待ち" in normalized:
                commitment_state = "waiting_confirmation"
                commitment_summary = "あなたと後で続きを話す流れは確認待ちで残っている。"
            candidates.append(
                {
                    "memory_type": "commitment",
                    "scope_type": "relationship",
                    "scope_key": "self|user",
                    "subject_ref": "self",
                    "predicate": "talk_again",
                    "object_ref_or_value": "topic:conversation",
                    "summary_text": commitment_summary,
                    "status": "inferred",
                    "commitment_state": commitment_state,
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
        # mock でも訂正検出を語彙一致には寄せない。
        _ = normalized
        return False

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
            updates.append(
                {
                    "target_scope_type": "relationship",
                    "target_scope_key": "self|user",
                    "affect_label": "concern",
                    "vad": {"v": -0.2, "a": 0.34, "d": -0.08},
                    "intensity": 0.62,
                    "confidence": 0.78,
                    "summary_text": "相手の負荷を気にかける関係上の反応が出た。",
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
            updates.append(
                {
                    "target_scope_type": "relationship",
                    "target_scope_key": "self|user",
                    "affect_label": "warmth",
                    "vad": {"v": 0.42, "a": 0.16, "d": 0.18},
                    "intensity": 0.58,
                    "confidence": 0.74,
                    "summary_text": "安心したやり取りから関係上の親しみが出た。",
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
        initiative_baseline_payload = persona.get("initiative_baseline")
        if isinstance(initiative_baseline_payload, dict):
            initiative_baseline = str(initiative_baseline_payload.get("level") or "").strip()
        else:
            initiative_baseline = str(initiative_baseline_payload or "").strip()
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
