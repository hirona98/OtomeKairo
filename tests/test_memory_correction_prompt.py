from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import build_persona_context
from otomekairo.llm.prompts import build_memory_interpretation_messages
from otomekairo.memory.utils import localize_timestamp_fields


CURRENT_TIME = "2026-10-17T09:00:00+09:00"
SNAPSHOT_KEYS = ("before_snapshot", "after_snapshot", "current_memory_unit")


def interpretation_arguments(targets):
    return {
        "persona_context": build_persona_context(
            {"persona_prompt": "経験の根拠を大切にする。"}, role="memory_interpretation",
        ),
        "input_text": "先ほどの報告を訂正したい。", "recall_hint": {},
        "decision": {"kind": "speech", "reason_summary": "訂正を受け止める。"},
        "speech_text": "確認します。", "memory_context": {},
        "current_time": CURRENT_TIME, "correction_targets": targets,
    }


def prompt_payload(targets):
    messages = build_memory_interpretation_messages(**interpretation_arguments(targets))
    return json.loads(messages[1]["content"].splitlines()[1])


@pytest.fixture
def targets():
    completed = {
        "memory_type": "commitment", "scope_type": "self", "scope_key": "self",
        "subject_ref": "self", "predicate": "report_result", "object_ref_or_value": None,
        "summary_text": "投稿の成功を確認して報告した。" * 50,
        "status": "confirmed", "commitment_state": "done", "confidence": 0.8,
        "qualifiers": {"autonomous_run_ids": ["run:post"], "autonomous_run_terminal_status": "completed"},
        "formed_at": CURRENT_TIME, "evidence_event_ids": ["event:report", "event:terminal"],
    }
    events = [
        {"event_id": "event:report", "cycle_id": "cycle:post", "created_at": CURRENT_TIME,
         "kind": "speech", "role": "assistant", "speaker_ref": "self",
         "participant_refs": ["person:test"], "run_id": "run:post", "text": "投稿成功を確認しました。" * 100},
        {"event_id": "event:terminal", "cycle_id": "cycle:post", "created_at": CURRENT_TIME,
         "kind": "autonomous_run_terminal", "source_kind": "autonomous_run", "run_id": "run:post",
         "terminal_status": "completed", "observed_result_summaries": ["投稿成功を確認した。"]},
    ]
    first = {
        "revision_id": "revision:post", "memory_unit_id": "memory_unit:post",
        "last_operation": "refine", "last_reason": "履行結果を反映した。", "occurred_at": CURRENT_TIME,
        "before_snapshot": {**completed, "commitment_state": "open"},
        "after_snapshot": completed, "current_memory_unit": deepcopy(completed),
        "related_memory_units": [{"memory_unit_id": "memory_unit:related", "summary_text": "関連する約束。"}],
        "source_cycle_ids": ["cycle:post"], "source_evidence_events": events,
    }
    second = {
        **deepcopy(first), "revision_id": "revision:other", "memory_unit_id": "memory_unit:other",
        "before_snapshot": None,
        "after_snapshot": dict(reversed(deepcopy(completed).items())),
        "source_evidence_events": list(reversed(deepcopy(events))),
    }
    return [first, second]


def test_shared_correction_input_round_trips_without_losing_evidence_or_roles(targets):
    original = deepcopy(targets)
    payload = prompt_payload(targets)
    context = payload["correction_context"]
    assert len(context["snapshots"]) == 2
    assert len(context["source_evidence_events"]) == 2

    restored = []
    for candidate in payload["target_candidates"]:
        target = {key: value for key, value in candidate.items()
                  if key not in (*[f"{key}_ref" for key in SNAPSHOT_KEYS], "source_evidence_event_ids")}
        for key in SNAPSHOT_KEYS:
            ref = candidate[f"{key}_ref"]
            target[key] = None if ref is None else context["snapshots"][ref]
        target["source_evidence_events"] = [
            context["source_evidence_events"][event_id] for event_id in candidate["source_evidence_event_ids"]
        ]
        restored.append(target)

    assert restored == localize_timestamp_fields(original)
    assert targets == original
    first, second = payload["target_candidates"]
    assert first["after_snapshot_ref"] == first["current_memory_unit_ref"] == second["after_snapshot_ref"]
    assert first["before_snapshot_ref"] != first["after_snapshot_ref"]
    assert second["before_snapshot_ref"] is None
    old = json.dumps({"target_candidates": original}, ensure_ascii=False, separators=(",", ":"))
    new = json.dumps({key: payload[key] for key in ("target_candidates", "correction_context")},
                     ensure_ascii=False, separators=(",", ":"))
    assert len(new) < len(old)


@pytest.mark.parametrize("change", [
    {"summary_text": "通知を完了した。"},
    {"commitment_state": "open"},
    {"qualifiers": {"autonomous_run_ids": ["run:reply"]}},
    {"formed_at": "2026-10-17T09:01:00+09:00"},
    {"confidence": True},
])
def test_different_current_understanding_keeps_a_separate_snapshot(targets, change):
    targets[0]["current_memory_unit"].update(change)
    payload = prompt_payload(targets)
    candidate = payload["target_candidates"][0]
    assert candidate["after_snapshot_ref"] != candidate["current_memory_unit_ref"]
    current = payload["correction_context"]["snapshots"][candidate["current_memory_unit_ref"]]
    assert current == localize_timestamp_fields(targets[0]["current_memory_unit"])


@pytest.mark.parametrize("value", [True, 1.0])
def test_snapshot_number_and_boolean_types_are_not_merged(targets, value):
    targets[0]["after_snapshot"]["qualifiers"] = {"value": 1}
    targets[0]["current_memory_unit"]["qualifiers"] = {"value": value}
    payload = prompt_payload(targets)
    candidate = payload["target_candidates"][0]
    assert candidate["after_snapshot_ref"] != candidate["current_memory_unit_ref"]


@pytest.mark.parametrize("targets", [None, []])
def test_no_correction_candidates_omits_shared_context(targets):
    payload = prompt_payload(targets)
    assert "target_candidates" not in payload
    assert "correction_context" not in payload


@pytest.mark.parametrize("invalid", [
    {}, {"event_id": None}, {"event_id": ""}, {"event_id": "   "},
    {"event_id": "event:report", "text": "異なる報告。"},
])
def test_invalid_evidence_fails_before_calling_the_provider(targets, invalid, monkeypatch):
    targets[1]["source_evidence_events"] = [invalid]
    original = deepcopy(targets)
    completion = Mock()
    monkeypatch.setattr("otomekairo.llm.client.complete_text", completion)
    with pytest.raises(ValueError, match="根拠イベント"):
        LLMClient().generate_memory_interpretation(model_config={"model": "provider/test"},
                                                  **interpretation_arguments(targets))
    completion.assert_not_called()
    assert targets == original
