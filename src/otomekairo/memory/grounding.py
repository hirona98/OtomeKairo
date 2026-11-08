from __future__ import annotations

from copy import deepcopy
from typing import Any


SUPPORT_KINDS = {
    "person_statement", "person_confirmation", "person_correction",
    "verified_result", "independent_events", "self_commitment", "inference", "unsupported",
}
CONFIRMED_SUPPORT_KINDS = SUPPORT_KINDS - {"inference", "unsupported"}
RETENTION_KINDS = {"long_term", "episode_only"}
PERSON_SUPPORT_KINDS = {"person_statement", "person_confirmation", "person_correction"}
SOURCE_BY_SUPPORT = {
    "person_statement": "explicit_statement", "person_confirmation": "explicit_confirmation",
    "person_correction": "explicit_correction", "verified_result": "verified_result",
    "independent_events": "independent_events", "self_commitment": "assistant_response",
    "inference": "inference", "unsupported": "inference",
}


def memory_evidence_sources(
    *, input_text: str, speech_text: str | None, decision: dict[str, Any],
    memory_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """入力の出所をコードで確定し、生成内で参照する資料表を作る。"""
    context = memory_context or {}
    current = context.get("current_input") or {}
    speaker = current.get("sender_ref")
    role = context.get("input_event_role") or current.get("sender_kind")
    if role is None and isinstance(speaker, str) and speaker.startswith("person:"):
        role = "person"
    sources = [{"ref": "input", "kind": context.get("input_event_kind", "input"), "role": role or "unknown",
                "speaker_ref": speaker, "text": input_text}]
    input_events = [event for event in context.get("events", [])
                    if event.get("kind") == context.get("input_event_kind", "conversation_input")]
    if len(input_events) == 1:
        sources[0].update({key: input_events[0][key] for key in ("event_id", "cycle_id") if key in input_events[0]})
    if speech_text:
        sources.append({"ref": "speech", "kind": "speech", "role": "assistant", "text": speech_text})
        speech_events = [event for event in context.get("events", []) if event.get("kind") == "speech"]
        if len(speech_events) == 1:
            sources[-1].update({key: speech_events[0][key] for key in ("event_id", "cycle_id") if key in speech_events[0]})
    sources.append({"ref": "decision", "kind": "decision", "role": "system", "payload": decision})
    for index, event in enumerate(context.get("events", []), 1):
        sources.append({"ref": f"event:{index}", **{k: event[k] for k in (
            "event_id", "cycle_id", "kind", "role", "speaker_ref", "created_at", "text_summary",
            "terminal_status", "run_id",
        ) if k in event}})
    for index, turn in enumerate(context.get("person_utterances", []), 1):
        sources.append({"ref": f"utterance:{index}", "kind": "person_utterance", "role": "person",
                        **{k: turn[k] for k in ("event_id", "cycle_id", "speaker_ref", "text", "created_at") if k in turn}})
    for index, observation in enumerate(context.get("current_visual_observations") or [], 1):
        sources.append({"ref": f"observation:{index}", "kind": "observation", "role": "observation",
                        "payload": observation, **{key: observation[key] for key in (
                            "visual_observation_id", "observation_id", "vision_source_id", "request_id", "observed_at",
                        ) if key in observation}})
    for key in ("observation_summary", "autonomous_run_summary"):
        if context.get(key):
            sources.append({"ref": key, "kind": "execution", "role": "system", "payload": context[key],
                            **{k: context[key][k] for k in ("run_id", "status") if k in context[key]}})
    for source in sources:
        allowed = {"inference", "unsupported"}
        if source.get("role") == "person":
            allowed.update(PERSON_SUPPORT_KINDS)
        if source.get("kind") == "speech" or source["ref"] == "autonomous_run_summary":
            allowed.add("self_commitment")
        if source.get("kind") in {"observation", "execution", "capability_result", "autonomous_run_terminal"}:
            allowed.add("verified_result")
        if source.get("cycle_id") and source.get("kind") not in {"decision", "event_evidence_precise_check"}:
            allowed.add("independent_events")
        source["support_kinds"] = sorted(allowed)
    return sources


def validate_memory_grounding(candidate: dict[str, Any], sources: list[dict[str, Any]] | None = None) -> None:
    grounding = candidate["grounding"]
    if not isinstance(grounding, dict) or set(grounding) != {
        "evidence_refs", "support_kind", "retention", "retention_reason",
    }:
        raise ValueError("grounding は evidence_refs/support_kind/retention/retention_reason を持つ object です。")
    refs = grounding["evidence_refs"]
    if (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ValueError("grounding.evidence_refs は重複のない非空の参照配列です。")
    kind = grounding["support_kind"]
    if not isinstance(kind, str) or kind not in SUPPORT_KINDS:
        raise ValueError("grounding.support_kind が不正です。")
    if not isinstance(grounding["retention"], str) or grounding["retention"] not in RETENTION_KINDS:
        raise ValueError("grounding.retention が不正です。")
    if not isinstance(grounding["retention_reason"], str) or not grounding["retention_reason"].strip():
        raise ValueError("grounding.retention_reason は非空文字列です。")
    if kind == "unsupported" and grounding["retention"] != "episode_only":
        raise ValueError("unsupported の候補は episode_only に置きます。")
    if "source" in candidate["qualifiers_hint"]:
        raise ValueError("根拠分類は grounding.support_kind に置きます。qualifiers_hint.source は使いません。")
    if sources is None:
        return
    by_ref = {source["ref"]: source for source in sources}
    if any(ref not in by_ref for ref in refs):
        raise ValueError("grounding.evidence_refs は evidence_sources に提示された参照を使います。")
    selected = [by_ref[ref] for ref in refs]
    if (kind not in {"self_commitment", "independent_events"}
            and grounding["retention"] == "long_term" and not any(
                source.get("role") in {"person", "observation", "capability"}
                or source.get("kind") in {"capability_result", "autonomous_run_terminal"}
                or source["ref"] == "observation_summary"
                for source in selected
            )):
        raise ValueError("単回の自己の発話・判断だけを根拠にした候補は episode_only に置きます。長期理解は本人発話・観測・実行結果などの一次経験に結び付けます。")
    if not any(kind in source["support_kinds"] for source in selected):
        raise ValueError("grounding.support_kind は参照資料の support_kinds と整合させます。")
    if kind in PERSON_SUPPORT_KINDS:
        person_sources = [source for source in selected if source.get("role") == "person"]
        if not person_sources:
            raise ValueError("本人発言の支持には role=person の根拠参照が必要です。")
        if candidate["scope"] == "entity" and candidate["subject_hint"].startswith("person:"):
            if not any(source.get("speaker_ref") == candidate["subject_hint"] for source in person_sources):
                raise ValueError("本人発言の speaker_ref と候補の subject_hint が一致しません。")
    if kind == "self_commitment":
        if (candidate["memory_type"] != "commitment"
                or candidate["qualifiers_hint"].get("commitment_actor") != "self"
                or not any(source.get("kind") == "speech" or source["ref"] == "autonomous_run_summary"
                           for source in selected)):
            raise ValueError("self_commitment は自己の応答・実行登録を根拠とする commitment_actor=self の commitment です。約束以外の単回の自己表現は episode_only に置きます。")
    if kind == "verified_result" and not any(
        source.get("kind") in {"observation", "execution", "capability_result", "autonomous_run_terminal"}
        for source in selected
    ):
        raise ValueError("verified_result には観測または実行結果の根拠が必要です。")
    if kind == "independent_events":
        cycles = {source["cycle_id"] for source in selected if source.get("cycle_id")}
        if len(cycles) < 2:
            raise ValueError("independent_events は異なる cycle の一次根拠を2件以上参照します。")


def grounded_candidate(candidate: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """採用済み候補へ、検証した根拠とコードが導出した出所を保持する。"""
    result = deepcopy(candidate)
    grounding = result["grounding"]
    by_ref = {source["ref"]: source for source in sources}
    result["qualifiers_hint"]["source"] = SOURCE_BY_SUPPORT[grounding["support_kind"]]
    result["qualifiers_hint"]["grounding"] = deepcopy(grounding)
    result["qualifiers_hint"]["evidence_sources"] = [
        {key: value for key, value in by_ref[ref].items() if key not in {"payload", "text", "text_summary", "support_kinds"}}
        for ref in grounding["evidence_refs"]
    ]
    return result
