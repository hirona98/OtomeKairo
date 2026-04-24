from __future__ import annotations

import json
from typing import Any

from otomekairo.llm_contracts import (
    INTENT_VALUES,
    RECALL_PACK_SECTION_NAMES,
    TIME_REFERENCE_VALUES,
)
from otomekairo.memory_utils import display_local_iso, localize_timestamp_fields


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
    ongoing_action_summary: dict[str, Any] | None,
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
                ongoing_action_summary=ongoing_action_summary,
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
    ongoing_action_summary: dict[str, Any] | None,
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
                ongoing_action_summary=ongoing_action_summary,
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


# validator_error を元に repair prompt を返す。
def build_memory_interpretation_repair_prompt(validation_error: str) -> str:
    return (
        "前回の出力は memory_interpretation 契約を満たしていませんでした。\n"
        f"validator_error: {validation_error}\n"
        "同じ意味を保ったまま、JSON オブジェクト 1 個だけを返し直してください。\n"
        "トップレベルキーは episode, candidate_memory_units, episode_affects の 3 つだけです。\n"
        "episode には episode_type, episode_series_id, primary_scope_type, primary_scope_key, summary_text, outcome_text, open_loops, salience だけを入れてください。\n"
        "candidate_memory_units の各要素には memory_type, scope_type, scope_key, subject_ref, predicate, object_ref_or_value, summary_text, status, commitment_state, confidence, salience, valid_from, valid_to, qualifiers, reason だけを入れてください。\n"
        "episode_affects の各要素には target_scope_type, target_scope_key, affect_label, vad, intensity, confidence, summary_text だけを入れてください。\n"
        "episode_affects.vad は v, a, d の 3 キーを持つ object です。\n"
        "同じ target_scope_type, target_scope_key, affect_label の組み合わせを重複して返してはいけません。\n"
        "episode_affects は最大 4 件までです。\n"
        "scope_type は self, user, entity, topic, relationship, world だけを使ってください。\n"
        "scope_type=self なら scope_key=self、scope_type=user なら scope_key=user、scope_type=relationship なら scope_key は self|user のような正規化済みキーです。\n"
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


# RecallHint system prompt。
def _build_recall_hint_system_prompt() -> str:
    return (
        "あなたは OtomeKairo の input_interpretation です。\n"
        "入力文を分析し、JSON オブジェクト 1 個だけを返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "primary_intent は次のいずれかです: "
        + ", ".join(sorted(INTENT_VALUES))
        + "\n"
        "time_reference は次のいずれかです: "
        + ", ".join(sorted(TIME_REFERENCE_VALUES))
        + "\n"
        "返すキーは必ず次の 7 個です:\n"
        "- primary_intent: string\n"
        "- secondary_intents: string[] (最大2件。primary_intent を含めない)\n"
        "- confidence: number\n"
        "- time_reference: string\n"
        "- focus_scopes: string[] (最大4件。self / user / relationship:<key> / topic:<key> に留める)\n"
        "- mentioned_entities: string[] (最大4件。person:<name> / place:<name> / tool:<name> の正規化済み参照)\n"
        "- mentioned_topics: string[] (最大4件。topic:<name> の正規化済み参照)\n"
        "第三者名や固有名は focus_scopes ではなく mentioned_entities に入れてください。\n"
        "不確実なときは conservative に smalltalk / none / 空配列を選んでください。"
    )


def _build_recall_hint_user_prompt(
    input_text: str,
    recent_turns: list[dict],
    current_time: str,
) -> str:
    return (
        f"current_time: {display_local_iso(current_time)}\n"
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
        "入力文に対して reply / noop / pending_intent のいずれかを決め、JSON オブジェクト 1 個だけを返してください。\n"
        "Markdown、コードフェンス、説明文は禁止です。\n"
        "入力には recent_turns と internal_context が含まれます。\n"
        "internal_context には TimeContext, AffectContext, DriveStateSummary, OngoingActionSummary, RecallPack が入ります。\n"
        "recall_hint.secondary_intents は補助意図として、継続性や確認必要性の補助にだけ使ってください。\n"
        "RecallPack.conflicts があるときは requires_confirmation=true を優先してください。\n"
        "active_commitments, episodic_evidence, event_evidence は reply と pending_intent の継続根拠に使ってください。\n"
        "pending_intent は『今は返さないが、後で触れる価値がある』場合だけ選んでください。\n"
        "明示的な会話要求に自然に返せるなら reply を優先し、pending_intent を乱用しないでください。\n"
        "返すキーは必ず次の 5 個です:\n"
        '- kind: "reply" または "noop" または "pending_intent"\n'
        "- reason_code: string\n"
        "- reason_summary: string\n"
        "- requires_confirmation: boolean\n"
        "- pending_intent: null または object\n"
        "kind が pending_intent のときだけ pending_intent object を返してください。\n"
        "pending_intent object のキーは intent_kind, intent_summary, dedupe_key の 3 個に固定してください。\n"
        "kind が pending_intent のとき requires_confirmation は false にしてください。\n"
        "空文字や意味のない入力は noop を選んでください。"
    )


def _build_decision_user_prompt(
    *,
    input_text: str,
    recent_turns: list[dict],
    time_context: dict[str, Any],
    affect_context: dict[str, Any],
    drive_state_summary: list[dict[str, Any]] | None,
    ongoing_action_summary: dict[str, Any] | None,
    recall_hint: dict,
    recall_pack: dict[str, Any],
) -> str:
    return (
        f"recent_turns:\n{_format_recent_turns(recent_turns)}\n"
        "internal_context:\n"
        f"{_format_internal_context(time_context, affect_context, drive_state_summary, ongoing_action_summary, recall_pack)}\n"
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
        "internal_context には TimeContext, AffectContext, DriveStateSummary, OngoingActionSummary, RecallPack が入ります。\n"
        "recall_hint.secondary_intents は話題継続や温度調整の補助にだけ使い、主方針は primary_intent に従ってください。\n"
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
    ongoing_action_summary: dict[str, Any] | None,
    recall_hint: dict,
    recall_pack: dict[str, Any],
    decision: dict,
) -> str:
    return (
        f"recent_turns:\n{_format_recent_turns(recent_turns)}\n"
        "internal_context:\n"
        f"{_format_internal_context(time_context, affect_context, drive_state_summary, ongoing_action_summary, recall_pack)}\n"
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
        "明示訂正で以前の理解を置き換えるなら、replacement 候補を返し qualifiers.negates_previous=true を付けてください。\n"
        "否定だけで置換内容がない場合だけ status=revoked を使ってください。\n"
        "false ではないが前面に出さない理解だけを status=dormant にしてください。\n"
        "弱い単発推測や event に留めるべき断片は candidate_memory_units に入れず、結果として noop になってよいです。\n"
        "qualifiers には必要なら source=explicit_statement|explicit_correction|inference, negates_previous, replace_prior, allow_parallel を入れてください。\n"
        "memory_type は fact, preference, relation, commitment, interpretation, summary のいずれかです。\n"
        "status は inferred, confirmed, superseded, revoked, dormant のいずれかです。\n"
        "primary_scope_type, candidate_memory_units[].scope_type, episode_affects[].target_scope_type は self, user, entity, topic, relationship, world のいずれかだけを使ってください。\n"
        "scope_type=self のとき scope_key は self、scope_type=user のとき scope_key は user、scope_type=world のとき scope_key は world に固定してください。\n"
        "scope_type=topic のとき scope_key は topic:<normalized_name> にしてください。\n"
        "scope_type=relationship のとき scope_key は self|user や self|person:tanaka のような正規化済みキーにしてください。user|self, relation:default, user:default_to_ai のような独自キーは禁止です。\n"
        "自分自身の対話姿勢や自己認識は self / self / subject_ref=self を使ってください。\n"
        "自分とユーザーの距離感、信頼、安心感、話しやすさ、支え方は relationship / self|user を使ってください。\n"
        "ai, agent, meta_communication などの独自 scope_type は使ってはいけません。\n"
        "commitment_state は commitment のときだけ open, waiting_confirmation, on_hold, done, cancelled のいずれかを使い、それ以外では null にしてください。\n"
        "episode は episode_type, episode_series_id, primary_scope_type, primary_scope_key, summary_text, outcome_text, open_loops, salience の 8 キーだけを持つ object にしてください。\n"
        "candidate_memory_units の各要素は memory_type, scope_type, scope_key, subject_ref, predicate, object_ref_or_value, summary_text, status, commitment_state, confidence, salience, valid_from, valid_to, qualifiers, reason の 15 キーだけを持つ object にしてください。\n"
        "episode_affects の各要素は target_scope_type, target_scope_key, affect_label, vad, intensity, confidence, summary_text の 7 キーだけを持つ object にしてください。\n"
        "episode_affects[].vad は v, a, d の 3 キーだけを持つ object にしてください。\n"
        "同じ target_scope_type, target_scope_key, affect_label の組み合わせを重複して返してはいけません。\n"
        "episode_affects は最大 4 件までにしてください。\n"
        "感情抽出に自信がない場合や、軽い雑談で瞬間反応が読めない場合は episode_affects を空配列にしてください。\n"
        "episode.episode_series_id は通常 null にし、episode.open_loops は短い文字列の配列にしてください。\n"
        "outcome_text, object_ref_or_value, valid_from, valid_to は不要なら null を入れてください。\n"
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
        "primary_intent=commitment_check では決定や継続性を優先しやすくし、primary_intent=reminisce や time_reference=past では anchor と topic を残しやすくしてください。\n"
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
        "primary_intent を主軸にし、secondary_intents は軽い補助に留めてください。\n"
        "association 候補は使えても、構造候補より無条件に優先してはいけません。\n"
        "commitment_check では open loop や active commitment を重く見やすくし、reminisce や time_reference=past では episodic_evidence を前へ置きやすくしてください。\n"
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


def _build_memory_interpretation_user_prompt(
    *,
    input_text: str,
    recall_hint: dict,
    decision: dict,
    reply_text: str | None,
    current_time: str,
) -> str:
    return (
        f"current_time: {display_local_iso(current_time)}\n"
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


# internal_context は token を増やしすぎないよう compact して渡す。
def _format_internal_context(
    time_context: dict[str, Any],
    affect_context: dict[str, Any],
    drive_state_summary: list[dict[str, Any]] | None,
    ongoing_action_summary: dict[str, Any] | None,
    recall_pack: dict[str, Any],
) -> str:
    payload = {
        "time_context": time_context,
        "affect_context": affect_context,
        "recall_pack": _compact_recall_pack(recall_pack),
    }
    if drive_state_summary:
        payload["drive_state_summary"] = drive_state_summary
    if ongoing_action_summary:
        payload["ongoing_action_summary"] = ongoing_action_summary
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
