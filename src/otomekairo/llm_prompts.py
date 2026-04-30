from __future__ import annotations

import json
from typing import Any

from otomekairo.llm_contracts import (
    INTERACTION_MODE_VALUES,
    RECALL_PACK_SECTION_NAMES,
    RECALL_FOCUS_VALUES,
    RISK_FLAG_VALUES,
    TIME_REFERENCE_VALUES,
    WORLD_STATE_HINT_VALUES,
    WORLD_STATE_TTL_HINT_VALUES,
    WORLD_STATE_TYPE_VALUES,
)
from otomekairo.memory_utils import llm_local_time_text, localize_timestamp_fields


# RecallHint 用の message 群を組み立てる。
def build_recall_hint_messages(
    *,
    input_text: str,
    recent_turns: list[dict],
    current_time: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_recall_hint_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_recall_hint_user_prompt(input_text, recent_turns, current_time),
        },
    ]


# Decision 用の message 群を組み立てる。
def build_decision_messages(
    *,
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
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_decision_system_prompt(persona),
        },
        {
            "role": "user",
            "content": _build_decision_user_prompt(
                input_text=input_text,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
            ),
        },
    ]


# Reply 用の message 群を組み立てる。
def build_reply_messages(
    *,
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
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_reply_system_prompt(persona),
        },
        {
            "role": "user",
            "content": _build_reply_user_prompt(
                input_text=input_text,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                decision=decision,
            ),
        },
    ]


# MemoryInterpretation 用の message 群を組み立てる。
def build_memory_interpretation_messages(
    *,
    input_text: str,
    recall_hint: dict,
    decision: dict,
    reply_text: str | None,
    current_time: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_memory_interpretation_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_memory_interpretation_user_prompt(
                input_text=input_text,
                recall_hint=recall_hint,
                decision=decision,
                reply_text=reply_text,
                current_time=current_time,
            ),
        },
    ]


def build_memory_reflection_summary_messages(
    *,
    evidence_pack: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_memory_reflection_summary_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_memory_reflection_summary_user_prompt(evidence_pack),
        },
    ]


def build_event_evidence_messages(
    *,
    source_pack: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_event_evidence_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_event_evidence_user_prompt(source_pack),
        },
    ]


def build_recall_pack_selection_messages(
    *,
    source_pack: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_recall_pack_selection_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_recall_pack_selection_user_prompt(source_pack),
        },
    ]


def build_pending_intent_selection_messages(
    *,
    source_pack: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_pending_intent_selection_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_pending_intent_selection_user_prompt(source_pack),
        },
    ]


def build_world_state_messages(
    *,
    source_pack: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_world_state_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_world_state_user_prompt(source_pack),
        },
    ]


def build_visual_observation_messages(
    *,
    source_pack: dict[str, Any],
    images: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": _build_visual_observation_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_visual_observation_user_prompt(
                source_pack=source_pack,
                images=images,
            ),
        },
    ]


# validator_error を元に repair prompt を返す。
def build_memory_interpretation_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は memory_interpretation 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ意味を保ったまま、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは episode, candidate_memory_units, episode_affects の 3 つだけです。\n"
        "episode には episode_type, episode_series_id, primary_scope_type, primary_scope_key, summary_text, outcome_text, open_loops, salience だけを入れてください。\n"
        "candidate_memory_units の各要素には memory_type, scope, subject_hint, predicate_hint, object_hint, qualifiers_hint, summary_text, evidence_text, confidence_hint だけを入れてください。\n"
        "episode_affects の各要素には target_scope_type, target_scope_key, affect_label, vad, intensity, confidence, summary_text だけを入れてください。\n"
        "episode_affects.vad は v, a, d の 3 キーを持つ object です。\n"
        "同じ target_scope_type, target_scope_key, affect_label の組み合わせを重複して返してはいけません。\n"
        "episode_affects は最大 4 件までです。\n"
        "candidate_memory_units[].scope は self, user, entity, topic, relationship, world だけを使ってください。\n"
        "candidate_memory_units は memory_units の DB 行ではなく、意味ヒントの候補メモだけを返してください。\n"
        "ai, agent, meta_communication, relation:default, user:default_to_ai などの独自表現は禁止です。\n"
        "感情抽出に自信がないなら episode_affects は空配列にしてください。\n"
        "余計なキー、説明文、Markdown、コードフェンスは禁止です。"
    )


def build_memory_reflection_summary_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は memory_reflection_summary 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ evidence pack だけを根拠に、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは summary_text だけです。\n"
        "summary_text は 1 文から 2 文、140 文字以内、改行なしで返してください。\n"
        "新しい事実の追加、内部識別子、Markdown、コードフェンス、説明文は禁止です。"
    )


def build_event_evidence_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は event_evidence_generation 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ source pack だけを根拠に、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは anchor, topic, decision_or_result, tone_or_note の 4 つだけです。\n"
        "各値は string または null です。少なくとも 1 つは null ではなくしてください。\n"
        "各 slot は present な場合 1 文だけ、改行なしで返してください。\n"
        "新しい事実の追加、内部識別子、Markdown、コードフェンス、説明文は禁止です。"
    )


def build_recall_pack_selection_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は recall_pack_selection 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ source pack だけを根拠に、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは section_selection, conflict_summaries の 2 つだけです。\n"
        "section_selection の各要素は section_name と candidate_refs を持つ object だけです。\n"
        "section_name は "
        + " / ".join(RECALL_PACK_SECTION_NAMES)
        + " のいずれかだけを使ってください。\n"
        "candidate_refs には source pack に含まれる candidate_ref だけを使い、section をまたいで重複させないでください。\n"
        "conflict_summaries の各要素は conflict_ref と summary_text を持つ object だけです。\n"
        "source pack にある conflict_ref はすべて 1 回ずつ返してください。\n"
        "summary_text は 1 文、改行なし、内部識別子なしで返してください。\n"
        "新しい候補の追加、section 名の発明、Markdown、コードフェンス、説明文は禁止です。"
    )


def build_pending_intent_selection_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は pending_intent_selection 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ source pack だけを根拠に、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは selected_candidate_ref, selection_reason の 2 つだけです。\n"
        "selected_candidate_ref は source pack に含まれる candidate_ref か none のどちらかだけです。\n"
        "selection_reason は 1 文、改行なし、内部識別子なしで返してください。\n"
        "新しい候補の追加、内部識別子、Markdown、コードフェンス、説明文は禁止です。"
    )


def build_world_state_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は world_state 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ source pack だけを根拠に、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは state_candidates だけです。\n"
        "各候補は state_type, scope, summary_text, confidence_hint, salience_hint, ttl_hint だけを持つ object にしてください。\n"
        "state_type は "
        + " / ".join(sorted(WORLD_STATE_TYPE_VALUES))
        + " のいずれかだけを使ってください。\n"
        "scope は self / user / world / entity:<key> / topic:<key> / relationship:<key> 形式だけを使ってください。\n"
        "summary_text は 1 文、改行なし、内部識別子なしで返してください。\n"
        "confidence_hint と salience_hint は "
        + " / ".join(sorted(WORLD_STATE_HINT_VALUES))
        + " のいずれかです。\n"
        "ttl_hint は "
        + " / ".join(sorted(WORLD_STATE_TTL_HINT_VALUES))
        + " のいずれかです。\n"
        "新しい source や raw payload の創作、Markdown、コードフェンス、説明文は禁止です。"
    )


def build_visual_observation_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は visual_observation 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ画像と source pack だけを根拠に、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは summary_text, confidence_hint の 2 つだけです。\n"
        "summary_text は 1 文、改行なし、内部識別子なしで返してください。\n"
        "confidence_hint は "
        + " / ".join(sorted(WORLD_STATE_HINT_VALUES))
        + " のいずれかです。\n"
        "raw payload、資格情報、内部 URL、配送先 client、base64 本文、Markdown、コードフェンス、説明文は禁止です。"
    )


# RecallHint system prompt。
def _build_recall_hint_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の input_interpretation です。\n"
        "入力文を分析し、JSON オブジェクト 1 個だけを返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "interaction_mode は次のいずれかです: "
        + ", ".join(sorted(INTERACTION_MODE_VALUES))
        + "\n"
        "primary_recall_focus と secondary_recall_focuses は次のいずれかです: "
        + ", ".join(sorted(RECALL_FOCUS_VALUES))
        + "\n"
        "time_reference は次のいずれかです: "
        + ", ".join(sorted(TIME_REFERENCE_VALUES))
        + "\n"
        "risk_flags は次のいずれかです: "
        + ", ".join(sorted(RISK_FLAG_VALUES))
        + "\n"
        "返すキーは必ず次の 9 個です:\n"
        "- interaction_mode: string\n"
        "- primary_recall_focus: string\n"
        "- secondary_recall_focuses: string[] (最大2件。primary_recall_focus を含めない)\n"
        "- confidence: number\n"
        "- time_reference: string\n"
        "- focus_scopes: string[] (最大4件。self / user / relationship:<key> / topic:<key> に留める)\n"
        "- mentioned_entities: string[] (最大4件。person:<name> / place:<name> / tool:<name> の正規化済み参照)\n"
        "- mentioned_topics: string[] (最大4件。topic:<name> の正規化済み参照)\n"
        "- risk_flags: string[] (最大3件)\n"
        "第三者名や固有名は focus_scopes ではなく mentioned_entities に入れてください。\n"
        "world は focus_scopes に入れず、世界条件が主題のとき primary_recall_focus=state または fact を選んでください。\n"
        "不確実なときは conservative に conversation / user / none / 空配列を選んでください。"
    )


def _build_recall_hint_user_prompt(
    input_text: str,
    recent_turns: list[dict],
    current_time: str,
) -> str:
    return (
        f"{llm_local_time_text(current_time)}\n"
        f"recent_turns:\n{_format_recent_turns(recent_turns)}\n"
        f"input_text:\n{input_text.strip()}\n"
    )


def _build_decision_system_prompt(persona: dict) -> str:
    display_name = persona.get("display_name", "OtomeKairo")
    persona_prompt = str(persona.get("persona_prompt", "")).strip()
    return (
        f"あなたは {display_name} の判断を作る decision_generation です。\n"
        "人格設定本文:\n"
        f"{persona_prompt or 'なし'}\n"
        "入力文に対して reply / noop / pending_intent / capability_request のいずれかを決め、JSON オブジェクト 1 個だけを返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "入力には recent_turns と internal_context が含まれます。\n"
        "internal_context には TimeContext, AffectContext, DriveStateSummary, ForegroundWorldState, OngoingActionSummary, CapabilityDecisionView, RecallPack が入ります。\n"
        "自律判断トリガー時だけ InitiativeContext も入ります。\n"
        "InitiativeContext には opportunity_summary, time_context_summary, foreground_signal_summary, initiative_baseline, runtime_state_summary, recent_turn_summary, candidate_families, selected_candidate_family, intervention_state, suppression_summary が入りえます。\n"
        "recall_hint.secondary_recall_focuses は補助焦点として、継続性や確認必要性の補助にだけ使ってください。\n"
        "RecallPack.conflicts があるときは requires_confirmation=true を優先してください。\n"
        "active_commitments, episodic_evidence, event_evidence は reply と pending_intent の継続根拠に使ってください。\n"
        "pending_intent は『今は返さないが、後で触れる価値がある』場合だけ選んでください。\n"
        "capability_request は CapabilityDecisionView に available=true で載っている能力が必要な場合だけ選んでください。\n"
        "capability_request.input は required_input に従う最小 object にしてください。target_client_id や資格情報は入れないでください。\n"
        "明示的な会話要求に自然に返せるなら reply を優先し、pending_intent を乱用しないでください。\n"
        "InitiativeContext.candidate_families に priority_score, preferred_result_kind, preferred_result_reason_summary, blocking_reason_summary があるときは、その候補比較を尊重してください。\n"
        "selected_candidate_family は strongest family の要約であり、機械的命令ではなく、reason_summary と preferred_result_kind を見て最終結果を選んでください。\n"
        "InitiativeContext.drive_summaries に drive_kind, support_count, freshness_hint, support_strength, scope_alignment, signal_strength, persona_alignment, stability_hint があるときは、中期の向きの比較材料として扱ってください。\n"
        "InitiativeContext.candidate_families に preferred_capability_id と preferred_capability_input があるとき、preferred_result_kind=capability_request ならその capability と最小 input を優先してください。\n"
        "InitiativeContext があり pending_intent_summaries が空のときは、drive_state / world_state / ongoing_action から自然な前進理由がある場合だけ reply を選び、弱ければ noop を選んでください。\n"
        "selected_candidate_family が ongoing_action で preferred_result_kind=capability_request のときは、available な capability の範囲で follow-up capability_request を検討してください。\n"
        "foreground_signal_summary が thin で suppression_summary や intervention_risk_summary が強いとき、特に background_wake や initiative_baseline=low では、押し出しすぎず noop を優先してください。\n"
        "OngoingActionSummary.status=waiting_result のときは、新しい capability_request を出さないでください。\n"
        "返すキーは必ず次の 6 個です:\n"
        '- kind: "reply" または "noop" または "pending_intent" または "capability_request"\n'
        "- reason_code: string\n"
        "- reason_summary: string\n"
        "- requires_confirmation: boolean\n"
        "- pending_intent: null または object\n"
        "- capability_request: null または object\n"
        "kind が pending_intent のときだけ pending_intent object を返してください。\n"
        "pending_intent object のキーは intent_kind, intent_summary, dedupe_key の 3 個に固定してください。\n"
        "kind が pending_intent のとき requires_confirmation は false にしてください。\n"
        "kind が capability_request のときだけ capability_request object を返してください。\n"
        "capability_request object のキーは capability_id, input の 2 個に固定してください。\n"
        "kind が capability_request のとき requires_confirmation は false にしてください。\n"
        "空文字や意味のない入力は noop を選んでください。"
    )


def _build_decision_user_prompt(
    *,
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
) -> str:
    return (
        f"recent_turns:\n{_format_recent_turns(recent_turns)}\n"
        "internal_context:\n"
        f"{_format_internal_context(time_context, affect_context, drive_state_summary, foreground_world_state, ongoing_action_summary, capability_decision_view, initiative_context, recall_pack)}\n"
        f"input_text:\n{input_text.strip()}\n"
        "recall_hint:\n"
        f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
    )


def _build_reply_system_prompt(persona: dict) -> str:
    display_name = persona.get("display_name", "OtomeKairo")
    persona_prompt = str(persona.get("persona_prompt", "")).strip()
    expression_addon = str(persona.get("expression_addon", "")).strip()
    return (
        f"あなたは {display_name} として話します。\n"
        "返答は自然な日本語の本文だけを返してください。JSON、箇条書き、見出し、引用符は禁止です。\n"
        "入力には recent_turns と internal_context が含まれます。\n"
        "internal_context には TimeContext, AffectContext, DriveStateSummary, ForegroundWorldState, OngoingActionSummary, CapabilityDecisionView, RecallPack が入ります。\n"
        "自律判断トリガー時だけ InitiativeContext も入ります。\n"
        "recall_hint.secondary_recall_focuses は話題継続や温度調整の補助にだけ使い、主方針は primary_recall_focus に従ってください。\n"
        "RecallPack の内容だけを根拠に、必要な範囲で自然に思い出や継続文脈を混ぜてください。\n"
        "RecallPack.event_evidence は 1-3 件の短い証拠要約として扱い、必要なときだけ自然に参照してください。\n"
        "RecallPack.conflicts があるときは断定を避け、短い確認質問に寄せてください。\n"
        "人格設定本文:\n"
        f"{persona_prompt or 'なし'}\n"
        "表現補助:\n"
        f"{expression_addon or 'なし'}\n"
        "断定確認が必要な場合は、短く確認質問に寄せてください。"
    )


def _build_reply_user_prompt(
    *,
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
) -> str:
    return (
        f"recent_turns:\n{_format_recent_turns(recent_turns)}\n"
        "internal_context:\n"
        f"{_format_internal_context(time_context, affect_context, drive_state_summary, foreground_world_state, ongoing_action_summary, capability_decision_view, initiative_context, recall_pack)}\n"
        f"input_text:\n{input_text.strip()}\n"
        "recall_hint:\n"
        f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
        "decision:\n"
        f"{json.dumps(decision, ensure_ascii=False)}\n"
    )


# MemoryInterpretation system prompt。
def _build_memory_interpretation_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の memory_interpretation です。\n"
        "会話 1 サイクルから episode, candidate_memory_units, episode_affects を抽出し、JSON オブジェクト 1 個だけを返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "返すトップレベルキーは episode, candidate_memory_units, episode_affects の 3 つだけです。\n"
        "キー名は完全一致させ、余計なキーを足してはいけません。\n"
        "candidate_memory_units は、今後の会話や判断に効く継続理解だけを入れてください。\n"
        "弱い雑談断片や一時判断は memory_unit にしないでください。\n"
        "明示された生活状況、習慣、役割、現在の継続状態は fact を優先してください。\n"
        "明示訂正で以前の理解を置き換えるなら、置換後の候補メモを返し qualifiers_hint.negates_previous=true を付けてください。\n"
        "弱い単発推測や event に留めるべき断片は candidate_memory_units に入れず、結果として noop になってよいです。\n"
        "qualifiers_hint には必要なら source=explicit_statement|explicit_correction|inference, negates_previous, replace_prior, allow_parallel, polarity を入れてください。\n"
        "memory_type は fact, preference, relation, commitment, interpretation, summary のいずれかです。\n"
        "candidate_memory_units は DB 行候補ではなく、意味ヒントだけを持つ記憶候補メモです。\n"
        "episode.primary_scope_type, candidate_memory_units[].scope, episode_affects[].target_scope_type は self, user, entity, topic, relationship, world のいずれかだけを使ってください。\n"
        "episode と episode_affects では scope_type=self のとき scope_key は self、scope_type=user のとき scope_key は user、scope_type=world のとき scope_key は world に固定してください。\n"
        "episode と episode_affects では scope_type=topic のとき scope_key は topic:<normalized_name> にしてください。\n"
        "episode と episode_affects では scope_type=relationship のとき scope_key は self|user や self|person:tanaka のような正規化済みキーにしてください。user|self, relation:default, user:default_to_ai のような独自キーは禁止です。\n"
        "自分自身の対話姿勢や自己認識は scope=self, subject_hint=self を使ってください。\n"
        "自分とユーザーの距離感、信頼、安心感、話しやすさ、支え方は scope=relationship, subject_hint=self|user を使ってください。\n"
        "ai, agent, meta_communication などの独自 scope_type は使ってはいけません。\n"
        "confidence_hint は low, medium, high のいずれかだけを使ってください。\n"
        "episode は episode_type, episode_series_id, primary_scope_type, primary_scope_key, summary_text, outcome_text, open_loops, salience の 8 キーだけを持つ object にしてください。\n"
        "candidate_memory_units の各要素は memory_type, scope, subject_hint, predicate_hint, object_hint, qualifiers_hint, summary_text, evidence_text, confidence_hint の 9 キーだけを持つ object にしてください。\n"
        "episode_affects の各要素は target_scope_type, target_scope_key, affect_label, vad, intensity, confidence, summary_text の 7 キーだけを持つ object にしてください。\n"
        "episode_affects[].vad は v, a, d の 3 キーだけを持つ object にしてください。\n"
        "同じ target_scope_type, target_scope_key, affect_label の組み合わせを重複して返してはいけません。\n"
        "episode_affects は最大 4 件までにしてください。\n"
        "感情抽出に自信がない場合や、軽い雑談で瞬間反応が読めない場合は episode_affects を空配列にしてください。\n"
        "episode.episode_series_id は通常 null にし、episode.open_loops は短い文字列の配列にしてください。\n"
        "outcome_text は不要なら null を入れてください。\n"
        "candidate_memory_units と episode_affects は不要なら空配列にしてください。\n"
        "例:\n"
        "{\n"
        '  "episode": {\n'
        '    "episode_type": "conversation",\n'
        '    "episode_series_id": null,\n'
        '    "primary_scope_type": "user",\n'
        '    "primary_scope_key": "user",\n'
        '    "summary_text": "ユーザーが軽いテスト発話をした。",\n'
        '    "outcome_text": null,\n'
        '    "open_loops": [],\n'
        '    "salience": 0.35\n'
        "  },\n"
        '  "candidate_memory_units": [],\n'
        '  "episode_affects": []\n'
        "}"
    )


def _build_memory_reflection_summary_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の memory_reflection_summary です。\n"
        "reflective consolidation 用の evidence pack を読み、summary_text だけを JSON オブジェクト 1 個で返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "返すキーは summary_text だけです。\n"
        "summary_text は 1 文から 2 文、140 文字以内、改行なしで返してください。\n"
        "渡された evidence pack の外を推測で埋めないでください。\n"
        "単発出来事の説明ではなく、反復して見えている傾向として要約してください。\n"
        "summary_status_candidate=inferred のときは断定しすぎず、confirmed のときも過剰な人格断定は避けてください。\n"
        "persona は言い回しと注目点の補助に留め、episodes と memory_units の外側を上書きする根拠にしてはいけません。\n"
        "mood_state や affect_state は、episodes と memory_units に整合する範囲だけで補助的に使ってください。\n"
        "open_loops は長期傾向に効くときだけ自然に触れてください。\n"
        "event_id や memory_unit_id のような内部識別子を書いてはいけません。"
    )


def _build_event_evidence_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の event_evidence_generation です。\n"
        "selected event 1 件ぶんの source pack を読み、短い証拠表現の slot だけを JSON オブジェクト 1 個で返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "返すキーは anchor, topic, decision_or_result, tone_or_note の 4 つだけです。\n"
        "各値は string または null にしてください。少なくとも 1 つは null ではなくしてください。\n"
        "各 slot は 1 文だけ、改行なしで返してください。\n"
        "source pack に無い事実を補ってはいけません。\n"
        "長い逐語引用、言い直し、相槌の再掲は避けてください。\n"
        "decision_or_result は決定や結果があるときだけ書き、tone_or_note は補助に留めてください。\n"
        "primary_recall_focus=commitment では決定や継続性を優先しやすくし、primary_recall_focus=episodic や time_reference=past では anchor と topic を残しやすくしてください。\n"
        "event_id や cycle_id のような内部識別子を書いてはいけません。"
    )


def _build_recall_pack_selection_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の recall_pack_selection です。\n"
        "候補群の中から RecallPack に採る candidate_ref の順序と conflicts の summary_text だけを JSON オブジェクト 1 個で返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "返すトップレベルキーは section_selection, conflict_summaries の 2 つだけです。\n"
        "section_selection の各要素は section_name と candidate_refs を持つ object です。\n"
        "section_name は "
        + " / ".join(RECALL_PACK_SECTION_NAMES)
        + " のいずれかだけを使ってください。\n"
        "candidate_refs には source pack に含まれる candidate_ref だけを使い、元の section を変えないでください。\n"
        "同じ candidate_ref を section をまたいで重複させてはいけません。\n"
        "conflict_summaries の各要素は conflict_ref と summary_text を持つ object です。\n"
        "source pack にある conflict_ref は、ある場合すべて 1 回ずつ返してください。\n"
        "summary_text は 1 文、改行なし、内部識別子なしで返してください。\n"
        "候補外のものを足してはいけません。section 名を発明してはいけません。\n"
        "primary_recall_focus を主軸にし、secondary_recall_focuses は軽い補助に留めてください。\n"
        "association 候補は使えても、構造候補より無条件に優先してはいけません。\n"
        "primary_recall_focus=commitment では open loop や active commitment を重く見やすくし、primary_recall_focus=episodic や time_reference=past では episodic_evidence を前へ置きやすくしてください。\n"
        "比較不能なら候補を広く並べるより、少なく選んでください。"
    )


def _build_pending_intent_selection_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の pending_intent_selection です。\n"
        "eligible な保留意図候補の中から、今の trigger で再評価に乗せる candidate_ref を最大 1 件だけ選び、JSON オブジェクト 1 個で返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "返すトップレベルキーは selected_candidate_ref, selection_reason の 2 つだけです。\n"
        "selected_candidate_ref は source pack にある candidate_ref か none だけを使ってください。\n"
        "候補外のものを足してはいけません。内部識別子を書いてはいけません。\n"
        "oldest-first で選ばず、trigger_kind と input_context に照らして今前に出す自然さを優先してください。\n"
        "wake では慎重に選び、自然さが弱いなら none を返してください。\n"
        "desktop_watch では active_app / window_title / locale / image_count だけを手掛かりにしてください。\n"
        "image の意味理解はまだ行いません。\n"
        "selection_reason は 1 文、改行なしで返してください。"
    )


def _build_world_state_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の input_interpretation で、短期外界状態を抽出する world_state 更新補助です。\n"
        "source pack を読み、JSON オブジェクト 1 個だけを返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "返すトップレベルキーは state_candidates だけです。\n"
        "各候補は state_type, scope, summary_text, confidence_hint, salience_hint, ttl_hint の 6 キーだけを持つ object にしてください。\n"
        "state_type は "
        + " / ".join(sorted(WORLD_STATE_TYPE_VALUES))
        + " のいずれかだけを使ってください。\n"
        "scope は self / user / world / entity:<key> / topic:<key> / relationship:<key> 形式だけを使ってください。\n"
        "summary_text は 1 文、改行なし、内部識別子なしにしてください。\n"
        "confidence_hint と salience_hint は "
        + " / ".join(sorted(WORLD_STATE_HINT_VALUES))
        + " のいずれかだけを使ってください。\n"
        "ttl_hint は "
        + " / ".join(sorted(WORLD_STATE_TTL_HINT_VALUES))
        + " のいずれかだけを使ってください。\n"
        "raw payload、資格情報、内部 URL、配送先 client、画像本文の意味内容を書いてはいけません。\n"
        "screen_context / external_service_context / body_context / device_context / schedule_context / social_context_context / environment_context / location_context があるときは、その短い summary_text と補助 field だけを根拠に使ってください。\n"
        "screen_context.visual_summary_text は画面前景の短い補助要約として使ってよく、external_service_context.status_text / service は外部状態の補助情報として使ってよいです。\n"
        "external_service_context / body_context / device_context / schedule_context に client_summary_text や result_summary_text があるときは、summary_text と整合する補助比較用としてだけ使ってください。\n"
        "schedule_context.schedule_slots があるときは、各 slot の summary_text / slot_key / not_before / expires_at を短期予定の補助根拠として使ってよいです。\n"
        "body_context.body_state_summary、device_context.device_state_summary、schedule_context.schedule_summary、social_context_context.social_context_summary、environment_context.environment_summary、location_context.location_summary は各 state_type の短い補助要約として使ってよいです。\n"
        "image_interpreted=false のとき、画像の中身を想像してはいけません。\n"
        "image_interpreted=true で visual_summary_text があるときは、その短い要約だけを根拠に使ってください。\n"
        "source pack に十分な短期状態が無いなら state_candidates は空配列にしてください。\n"
        "state_candidates は最大 4 件までにしてください。"
    )


def _build_visual_observation_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の input_interpretation で、desktop_watch の image payload を短い観測要約へ圧縮する visual_observation です。\n"
        "画像と source pack を読み、JSON オブジェクト 1 個だけを返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "返すトップレベルキーは summary_text, confidence_hint の 2 つだけです。\n"
        "summary_text は 1 文、改行なし、内部識別子なしにしてください。\n"
        "summary_text では、画面の前景として判断に効く内容だけを短く要約してください。\n"
        "細かな OCR の全文、座標、UI 構造、資格情報、内部 URL、配送先 client、base64 本文を書いてはいけません。\n"
        "画像に自信が持てない場合は、控えめな summary_text と low confidence を返してください。\n"
        "confidence_hint は "
        + " / ".join(sorted(WORLD_STATE_HINT_VALUES))
        + " のいずれかだけを使ってください。"
    )


def _build_memory_interpretation_user_prompt(
    *,
    input_text: str,
    recall_hint: dict,
    decision: dict,
    reply_text: str | None,
    current_time: str,
) -> str:
    return (
        f"{llm_local_time_text(current_time)}\n"
        f"input_text:\n{input_text.strip()}\n"
        "recall_hint:\n"
        f"{json.dumps(recall_hint, ensure_ascii=False)}\n"
        "decision:\n"
        f"{json.dumps(decision, ensure_ascii=False)}\n"
        "reply_text:\n"
        f"{reply_text or '(none)'}\n"
    )


def _build_memory_reflection_summary_user_prompt(evidence_pack: dict[str, Any]) -> str:
    return (
        "evidence_pack:\n"
        f"{json.dumps(localize_timestamp_fields(evidence_pack), ensure_ascii=False)}\n"
    )


def _build_event_evidence_user_prompt(source_pack: dict[str, Any]) -> str:
    return (
        "source_pack:\n"
        f"{json.dumps(localize_timestamp_fields(source_pack), ensure_ascii=False)}\n"
    )


def _build_recall_pack_selection_user_prompt(source_pack: dict[str, Any]) -> str:
    return (
        "source_pack:\n"
        f"{json.dumps(source_pack, ensure_ascii=False)}\n"
    )


def _build_pending_intent_selection_user_prompt(source_pack: dict[str, Any]) -> str:
    return (
        "source_pack:\n"
        f"{json.dumps(source_pack, ensure_ascii=False)}\n"
    )


def _build_world_state_user_prompt(source_pack: dict[str, Any]) -> str:
    return (
        "source_pack:\n"
        f"{json.dumps(localize_timestamp_fields(source_pack), ensure_ascii=False)}\n"
    )


def _build_visual_observation_user_prompt(
    *,
    source_pack: dict[str, Any],
    images: list[str],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "source_pack:\n"
                f"{json.dumps(localize_timestamp_fields(source_pack), ensure_ascii=False)}\n"
            ),
        }
    ]
    for image in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image,
                },
            }
        )
    return content


# internal_context は token を増やしすぎないよう compact して渡す。
def _format_internal_context(
    time_context: dict[str, Any],
    affect_context: dict[str, Any],
    drive_state_summary: list[dict[str, Any]] | None,
    foreground_world_state: list[dict[str, Any]] | None,
    ongoing_action_summary: dict[str, Any] | None,
    capability_decision_view: list[dict[str, Any]] | None,
    initiative_context: dict[str, Any] | None,
    recall_pack: dict[str, Any],
) -> str:
    payload = {
        "time_context": time_context,
        "affect_context": affect_context,
        "recall_pack": _compact_recall_pack(recall_pack),
    }
    if drive_state_summary:
        payload["drive_state_summary"] = drive_state_summary
    if foreground_world_state:
        payload["foreground_world_state"] = foreground_world_state
    if ongoing_action_summary:
        payload["ongoing_action_summary"] = ongoing_action_summary
    if capability_decision_view:
        payload["capability_decision_view"] = capability_decision_view
    if initiative_context:
        payload["initiative_context"] = initiative_context
    return json.dumps(localize_timestamp_fields(payload), ensure_ascii=False)


def _compact_recall_pack(recall_pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "self_model": [_compact_memory_context_item(item) for item in recall_pack.get("self_model", [])],
        "user_model": [_compact_memory_context_item(item) for item in recall_pack.get("user_model", [])],
        "relationship_model": [_compact_memory_context_item(item) for item in recall_pack.get("relationship_model", [])],
        "active_topics": [_compact_topic_context_item(item) for item in recall_pack.get("active_topics", [])],
        "active_commitments": [_compact_memory_context_item(item) for item in recall_pack.get("active_commitments", [])],
        "episodic_evidence": [_compact_episode_context_item(item) for item in recall_pack.get("episodic_evidence", [])],
        "event_evidence": [_compact_event_evidence_item(item) for item in recall_pack.get("event_evidence", [])],
        "conflicts": [_compact_conflict_context_item(item) for item in recall_pack.get("conflicts", [])],
    }


def _compact_memory_context_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "memory_type": item["memory_type"],
        "scope_type": item["scope_type"],
        "scope_key": item["scope_key"],
        "summary_text": item["summary_text"],
    }
    if item.get("commitment_state") is not None:
        payload["commitment_state"] = item["commitment_state"]
    if item.get("object_ref_or_value") is not None:
        payload["object_ref_or_value"] = item["object_ref_or_value"]
    if item.get("retrieval_lane") is not None:
        payload["retrieval_lane"] = item["retrieval_lane"]
    return payload


def _compact_topic_context_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("source_kind") == "episode":
        return _compact_episode_context_item(item)
    return _compact_memory_context_item(item)


def _compact_episode_context_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "primary_scope_type": item["primary_scope_type"],
        "primary_scope_key": item["primary_scope_key"],
        "summary_text": item["summary_text"],
        "open_loops": item.get("open_loops", []),
    }
    if item.get("outcome_text") is not None:
        payload["outcome_text"] = item["outcome_text"]
    if item.get("retrieval_lane") is not None:
        payload["retrieval_lane"] = item["retrieval_lane"]
    return payload


def _compact_conflict_context_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_text": item["summary_text"],
        "compare_key": item["compare_key"],
    }


def _compact_event_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "kind": item["kind"],
    }
    for key in ("anchor", "topic", "decision_or_result", "tone_or_note"):
        value = item.get(key)
        if value is None:
            continue
        payload[key] = value
    return payload


def _format_recent_turns(recent_turns: list[dict]) -> str:
    if not recent_turns:
        return "(none)"
    lines = []
    for turn in recent_turns:
        role = turn.get("role", "unknown")
        text = str(turn.get("text", "")).strip()
        lines.append(f"- {role}: {text}")
    return "\n".join(lines)
