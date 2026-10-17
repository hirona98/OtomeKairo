from copy import deepcopy
import json
import sqlite3
from unittest.mock import Mock

import pytest

from otomekairo.defaults import build_default_state
from otomekairo.llm.client import LLMClient
from otomekairo.llm.contracts import LLMError, validate_memory_interpretation_contract
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.store.file_store import FileStore


CURRENT_TIME = "2026-10-17T09:00:00+09:00"
SELF_CONCERN = {
    "target_scope_type": "self", "target_scope_key": "self", "affect_label": "concern",
    "vad": {"v": -0.4, "a": 0.4, "d": -0.1}, "intensity": 0.6, "confidence": 0.8,
    "summary_text": "友人の発熱を聞いて心配した。",
}
RELATIONSHIP_CARE = {
    "target_scope_type": "relationship", "target_scope_key": "self|person:test", "affect_label": "care",
    "vad": {"v": 0.3, "a": 0.1, "d": 0.1}, "intensity": 0.5, "confidence": 0.8,
    "summary_text": "相手を気遣った。",
}


def interpretation():
    return {
        "episode": {
            "episode_type": "conversation", "episode_series_id": None,
            "primary_scope_type": "entity", "primary_scope_key": "person:test",
            "summary_text": "普段の好みと友人の発熱について聞いた。", "outcome_text": "相手を気遣った。",
            "open_loops": [], "salience": 0.6,
        },
        "candidate_memory_units": [{
            "memory_type": "preference", "scope": "entity", "subject_hint": "person:test",
            "predicate_hint": "preferred_drink", "object_hint": {"kind": "value", "value": "ほうじ茶"},
            "qualifiers_hint": {"source": "explicit_statement"},
            "summary_text": "普段はほうじ茶を好む。", "evidence_text": "本人が普段の好みを述べた。",
            "confidence_hint": "high",
        }],
        "episode_affects": [deepcopy(SELF_CONCERN), deepcopy(RELATIONSHIP_CARE)],
        "correction_status": "no_correction", "selected_targets": [],
    }


def provider_response(payload):
    wire = deepcopy(payload)
    for candidate in wire["candidate_memory_units"]:
        candidate["qualifiers_hint"] = json.dumps(candidate["qualifiers_hint"], ensure_ascii=False)
    return json.dumps(wire, ensure_ascii=False)


@pytest.fixture
def turn(tmp_path, monkeypatch):
    store = FileStore(tmp_path)
    state = build_default_state()
    state["model_presets"][state["selected_model_preset_id"]]["model"] = "provider/test"
    memory_set_id = state["selected_memory_set_id"]
    event = {
        "event_id": "event:single-pass", "cycle_id": "cycle:single-pass", "memory_set_id": memory_set_id,
        "kind": "conversation_input", "role": "person", "created_at": CURRENT_TIME,
        "speaker_ref": "person:test", "participant_refs": ["person:test"],
        "text": "普段はほうじ茶が好き。友人が発熱して心配だ。",
    }
    store.append_events(events=[event])
    store.register_interaction_participants(
        memory_set_id=memory_set_id, participants=[{"person_ref": "person:test", "display_name": "テスト人物"}],
        observed_at=CURRENT_TIME, evidence_event_ids=[event["event_id"]],
    )
    completion = Mock(return_value=provider_response(interpretation()))
    monkeypatch.setattr("otomekairo.llm.client.complete_text", completion)
    consolidator = MemoryConsolidator(store=store, llm=LLMClient())
    arguments = {
        "state": state, "cycle_id": event["cycle_id"], "finished_at": CURRENT_TIME,
        "input_text": event["text"], "recall_hint": {}, "correction_candidate_memory_unit_ids": [],
        "decision": {"kind": "speech", "reason_summary": "近況を受け止める。"},
        "speech_payload": {"speech_text": "どうぞお大事に。"}, "events": [event],
        "memory_context": {
            "current_input": {"sender_ref": "person:test"},
            "people_context": [{"person_ref": "person:test", "display_name": "テスト人物"}],
            "person_utterances": [{"speaker_ref": "person:test", "text": event["text"]}],
        },
    }
    return consolidator, store, arguments, completion


def test_one_generation_persists_memory_and_both_affects_and_updates_mood(turn):
    consolidator, store, arguments, completion = turn
    trace, job = consolidator.consolidate_turn(**arguments)

    assert completion.call_count == 1
    assert completion.call_args.kwargs["response_format"]["json_schema"]["name"] == "memory_interpretation"
    assert trace["turn_consolidation_status"] == "succeeded"
    assert trace["memory_action_count"] == 1
    assert trace["episode_affect_count"] == 2
    assert job["episode"]["summary_text"] == interpretation()["episode"]["summary_text"]
    with sqlite3.connect(store.memory_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memory_units").fetchone()[0] == 1
        affects = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM episode_affects")]
    assert {affect["target_scope_type"] for affect in affects} == {"self", "relationship"}
    assert next(affect for affect in affects if affect["target_scope_type"] == "self")["vad"] == SELF_CONCERN["vad"]
    mood = store.get_mood_state(memory_set_id=arguments["state"]["selected_memory_set_id"], current_time=CURRENT_TIME)
    assert mood["current_vad"]["v"] < 0


def test_episode_only_result_needs_one_generation_and_preserves_empty_memory_and_affects(turn):
    consolidator, store, arguments, completion = turn
    payload = interpretation()
    payload["candidate_memory_units"] = []
    payload["episode_affects"] = []
    completion.return_value = provider_response(payload)

    trace, job = consolidator.consolidate_turn(**arguments)

    assert completion.call_count == 1
    assert trace["memory_action_count"] == 0
    assert trace["episode_affect_count"] == 0
    assert job["memory_actions"] == []
    with sqlite3.connect(store.memory_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memory_units").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM episode_affects").fetchone()[0] == 0


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_invalid_affect_is_repaired_before_any_memory_is_saved(turn, repair_succeeds):
    consolidator, store, arguments, completion = turn
    invalid = interpretation()
    invalid["episode_affects"][0]["vad"]["v"] = 1.2
    completion.side_effect = [provider_response(invalid), provider_response(interpretation() if repair_succeeds else invalid)]

    if repair_succeeds:
        trace, _ = consolidator.consolidate_turn(**arguments)
        assert trace["episode_affect_count"] == 2
    else:
        with pytest.raises(LLMError, match="vad"):
            consolidator.consolidate_turn(**arguments)
        with sqlite3.connect(store.memory_db_path) as conn:
            for table in ("episodes", "memory_units", "episode_affects"):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert completion.call_count == 2


def test_interpretation_receives_response_registered_run_and_original_evidence_in_one_call(turn):
    consolidator, _, arguments, completion = turn
    run = {"run_id": "autonomous_run:notification", "status": "waiting_timer"}
    arguments["decision"] = {"kind": "autonomous_run", "reason_summary": "指定時刻に一度通知する。"}
    arguments["speech_payload"] = {"speech_text": "指定時刻に一度お知らせします。"}
    arguments["memory_context"]["autonomous_run_summary"] = run

    consolidator.consolidate_turn(**arguments)

    assert completion.call_count == 1
    payload = json.loads(completion.call_args.kwargs["messages"][1]["content"].splitlines()[1])
    assert payload["decision"] == arguments["decision"]
    assert payload["speech_text"] == arguments["speech_payload"]["speech_text"]
    assert payload["memory_context"]["autonomous_run_summary"] == run
    assert payload["memory_context"]["person_utterances"] == arguments["memory_context"]["person_utterances"]


@pytest.mark.parametrize("offered_revision", [True, False])
def test_generated_correction_selection_reaches_postprocess_and_checks_offered_ids(turn, offered_revision):
    consolidator, store, arguments, completion = turn
    first_trace, _ = consolidator.consolidate_turn(**arguments)
    memory_id = first_trace["updated_memory_unit_ids"][0]
    with sqlite3.connect(store.memory_db_path) as conn:
        revision_id = conn.execute(
            "SELECT revision_id FROM revisions WHERE memory_unit_id = ?", (memory_id,),
        ).fetchone()[0]
    payload = interpretation()
    payload["candidate_memory_units"] = []
    payload["episode_affects"] = []
    payload["correction_status"] = "selected"
    payload["selected_targets"] = [{
        "revision_id": revision_id if offered_revision else "revision:unoffered",
        "memory_unit_id": memory_id, "reason_summary": "先ほどの好みの申告を訂正した。",
    }]
    completion.reset_mock()
    completion.return_value = provider_response(payload)
    event = {**arguments["events"][0], "event_id": "event:correction", "cycle_id": "cycle:correction",
             "created_at": "2026-10-17T09:01:00+09:00", "text": "さっきの好みの申告は誤りだった。"}
    store.append_events(events=[event])
    arguments.update(cycle_id=event["cycle_id"], finished_at=event["created_at"], input_text=event["text"],
                     events=[event], correction_candidate_memory_unit_ids=[memory_id])

    _, job = consolidator.consolidate_turn(**arguments)

    assert completion.call_count == 1
    context = job["correction_reconciliation"]
    assert context["selection"]["selected_targets"] == payload["selected_targets"]
    actions, trace = consolidator._run_correction_reconciliation(job=job, finished_at=event["created_at"])
    if offered_revision:
        assert trace["selected_target_count"] == 1
        assert actions[0]["after_snapshot"]["status"] == "revoked"
    else:
        assert trace["result_status"] == "failed"
        assert actions == []


@pytest.mark.parametrize("invalid_affects", [
    [SELF_CONCERN, {**SELF_CONCERN, "affect_label": "relief"}],
    [RELATIONSHIP_CARE, RELATIONSHIP_CARE],
    [RELATIONSHIP_CARE] * 5,
    [{**SELF_CONCERN, "vad": {"v": 1.2, "a": 0, "d": 0}}],
    [{**SELF_CONCERN, "intensity": True}],
])
def test_interpretation_validates_affect_limits_duplicates_and_numeric_ranges(invalid_affects):
    payload = interpretation()
    payload["episode_affects"] = deepcopy(invalid_affects)
    with pytest.raises(LLMError):
        validate_memory_interpretation_contract(payload)


def test_correction_without_any_offered_revision_is_an_explicit_failure(turn):
    consolidator, _, arguments, completion = turn
    payload = interpretation()
    payload["correction_status"] = "selected"
    payload["selected_targets"] = [{
        "revision_id": "revision:unoffered", "memory_unit_id": "memory_unit:unoffered",
        "reason_summary": "提示されていない訂正対象。",
    }]
    completion.return_value = provider_response(payload)

    trace, job = consolidator.consolidate_turn(**arguments)
    actions, correction_trace = consolidator._run_correction_reconciliation(job=job, finished_at=CURRENT_TIME)

    assert completion.call_count == 1
    assert trace["turn_consolidation_status"] == "succeeded"
    assert correction_trace["result_status"] == "failed"
    assert "提示候補" in correction_trace["failure_reason"]
    assert actions == []


@pytest.mark.parametrize("open_loops", [[None], [""], ["   "], [42]])
def test_interpretation_rejects_invalid_open_loops(open_loops):
    payload = interpretation()
    payload["episode"]["open_loops"] = open_loops
    with pytest.raises(LLMError, match="open_loops"):
        validate_memory_interpretation_contract(payload)
