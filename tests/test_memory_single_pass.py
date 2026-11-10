from copy import deepcopy
import json
import sqlite3
from unittest.mock import Mock

import pytest
from memory_fixtures import grounding

from otomekairo.defaults import build_default_state
from otomekairo.llm.client import LLMClient
from otomekairo.llm.contracts import LLMError, validate_memory_interpretation_contract
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.memory.utils import build_memory_unit_semantic_text, source_text_hash
from otomekairo.store.file_store import FileStore
from otomekairo.store.memory_links import MemoryWriteConflictError


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
            "qualifiers_hint": {}, "grounding": grounding(),
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
        "event_id": "event:input", "cycle_id": "cycle:single-pass", "memory_set_id": memory_set_id,
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
        "speech_payload": {"speech_text": "どうぞお大事に。"}, "events": [event, {**event, "event_id": "event:decision", "kind": "decision", "role": "system"}, {**event, "event_id": "event:speech", "kind": "speech", "role": "assistant"}],
        "memory_context": {
            "current_input": {"sender_ref": "person:test"},
            "people_context": [{"person_ref": "person:test", "display_name": "テスト人物"}],
            "person_utterances": [{"event_id": event["event_id"], "speaker_ref": "person:test", "text": event["text"]}],
        },
    }
    store.append_events(events=arguments["events"])
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


def test_evidence_uses_canonical_ids_and_merges_current_input_and_utterance(turn):
    consolidator, _, arguments, completion = turn
    consolidator.consolidate_turn(**arguments)
    request = json.loads(completion.call_args.kwargs["messages"][1]["content"].splitlines()[1])
    sources = request["memory_context"]["evidence_sources"]
    assert {source["ref"] for source in sources} == {event["event_id"] for event in arguments["events"]}
    current, = [source for source in sources if "input" in source["usage_roles"]]
    assert current["ref"] == arguments["events"][0]["event_id"]
    assert set(current["usage_roles"]) == {"event", "input", "utterance"}
    assert current["text"] == arguments["input_text"]
    assert completion.call_count == 1


def test_single_cycle_sources_do_not_offer_independent_events(turn):
    consolidator, _, arguments, completion = turn
    consolidator.consolidate_turn(**arguments)
    request = json.loads(completion.call_args.kwargs["messages"][1]["content"].splitlines()[1])
    assert all("independent_events" not in source["support_kinds"] for source in request["memory_context"]["evidence_sources"])


def test_distinct_cycle_sources_still_allow_independent_events(turn):
    consolidator, store, arguments, completion = turn
    previous = {**arguments["events"][0], "event_id": "event:previous", "cycle_id": "cycle:previous"}
    store.append_events(events=[previous])
    arguments["memory_context"]["person_utterances"].append(previous)
    payload = interpretation()
    payload["candidate_memory_units"][0]["grounding"] = grounding(
        refs=["event:input", "event:previous"], support="independent_events",
    )
    completion.return_value = provider_response(payload)
    trace, _ = consolidator.consolidate_turn(**arguments)
    assert trace["turn_consolidation_status"] == "succeeded"
    assert trace["memory_action_count"] == 1
    assert completion.call_count == 1


def test_system_notification_is_repaired_as_an_episode_without_weakening_long_term_validation(turn):
    consolidator, store, arguments, completion = turn
    arguments["events"][0].update(kind="wake_input", role="system", speaker_ref=None)
    arguments["memory_context"].update(input_event_kind="wake_input", input_event_role="system", person_utterances=[])
    store.append_events(events=arguments["events"])
    rejected = interpretation()
    rejected["candidate_memory_units"][0]["grounding"] = grounding(support="inference")
    accepted = deepcopy(rejected)
    accepted["candidate_memory_units"][0]["grounding"].update(retention="episode_only", retention_reason="今回受け取った完了通知として残す。")
    completion.side_effect = [provider_response(rejected), provider_response(accepted)]
    trace, _ = consolidator.consolidate_turn(**arguments)
    assert trace["turn_consolidation_status"] == "succeeded"
    assert trace["memory_action_count"] == 0
    assert completion.call_count == 2
    repair = completion.call_args.kwargs["messages"][-1]["content"]
    assert "selected_sources(ref, kind, role)=[('event:input', 'wake_input', 'system')]" in repair
    assert "episode_only" in repair


def test_unknown_evidence_id_is_repaired_with_the_offered_canonical_ids(turn):
    consolidator, _, arguments, completion = turn
    rejected = interpretation()
    rejected["candidate_memory_units"][0]["grounding"]["evidence_refs"] = ["event:unoffered"]
    completion.side_effect = [provider_response(rejected), provider_response(interpretation())]
    trace, _ = consolidator.consolidate_turn(**arguments)
    assert trace["turn_consolidation_status"] == "succeeded"
    assert completion.call_count == 2
    repair = completion.call_args.kwargs["messages"][-1]["content"]
    assert "unknown_refs=['event:unoffered']" in repair
    assert "allowed_refs=['event:input', 'event:decision', 'event:speech']" in repair


def test_recent_turns_preserve_event_and_cycle_ids_for_primary_evidence(turn):
    from otomekairo.memory.grounding import memory_evidence_sources
    _, store, arguments, _ = turn
    event = {**arguments["events"][0], "interaction_ref": "interaction:test"}
    store.append_events(events=[event])
    turns = store.load_recent_turns(
        memory_set_id=event["memory_set_id"], interaction_ref="interaction:test", since_iso=CURRENT_TIME, limit=8,
    )
    assert turns[0]["event_id"] == event["event_id"]
    assert turns[0]["cycle_id"] == event["cycle_id"]
    sources = memory_evidence_sources(
        input_text=arguments["input_text"], speech_text=None, decision=arguments["decision"],
        memory_context={"events": [event], "person_utterances": turns},
    )
    assert len(sources) == 1
    assert sources[0]["ref"] == event["event_id"]


def test_clone_remaps_canonical_grounding_and_snapshots_without_rewriting_text(turn):
    consolidator, store, arguments, completion = turn
    payload = interpretation()
    payload["candidate_memory_units"][0]["summary_text"] = "event:input"
    completion.return_value = provider_response(payload)
    consolidator.consolidate_turn(**arguments)
    target = "memory_set:copy"
    store.clone_memory_set_records(
        source_memory_set_id=arguments["state"]["selected_memory_set_id"], target_memory_set_id=target,
    )
    with sqlite3.connect(store.memory_db_path) as conn:
        unit = json.loads(conn.execute("SELECT payload_json FROM memory_units WHERE memory_set_id = ?", (target,)).fetchone()[0])
        revision = json.loads(conn.execute("SELECT payload_json FROM revisions WHERE memory_set_id = ?", (target,)).fetchone()[0])
        events = {row[0] for row in conn.execute("SELECT event_id FROM events WHERE memory_set_id = ?", (target,))}
    refs = unit["qualifiers"]["grounding"]["evidence_refs"]
    assert set(refs) <= events
    assert "event:input" not in refs
    assert unit["qualifiers"]["evidence_sources"][0]["ref"] == refs[0]
    assert unit["qualifiers"]["evidence_sources"][0]["event_id"] == refs[0]
    assert revision["after_snapshot"]["qualifiers"]["grounding"]["evidence_refs"] == refs
    assert unit["summary_text"] == "event:input"


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
def test_generated_correction_selection_is_persisted_and_checks_offered_ids(turn, offered_revision):
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

    trace, job = consolidator.consolidate_turn(**arguments)

    assert completion.call_count == 1
    prompt = json.loads(completion.call_args.kwargs["messages"][1]["content"].splitlines()[1])
    candidate = next(item for item in prompt["target_candidates"] if item["revision_id"] == revision_id)
    snapshot = prompt["correction_context"]["snapshots"][candidate["after_snapshot_ref"]]
    assert snapshot["summary_text"] == interpretation()["candidate_memory_units"][0]["summary_text"]
    correction_trace = trace["correction_reconciliation"]
    assert job["correction_reconciliation"] == correction_trace
    saved = store.list_memory_units_by_id(
        memory_set_id=arguments["state"]["selected_memory_set_id"], memory_unit_ids=[memory_id],
    )[0]
    if offered_revision:
        assert correction_trace["selected_target_count"] == 1
        assert correction_trace["selected_revision_ids"] == [revision_id]
        assert job["memory_actions"][0]["after_snapshot"]["status"] == "revoked"
        assert saved["status"] == "revoked"
    else:
        assert correction_trace["result_status"] == "failed"
        assert job["memory_actions"] == []
        assert saved["status"] == "confirmed"


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
    correction_trace = trace["correction_reconciliation"]

    assert completion.call_count == 1
    assert trace["turn_consolidation_status"] == "succeeded"
    assert correction_trace["result_status"] == "failed"
    assert "提示候補" in correction_trace["failure_reason"]
    assert all(action["operation"] != "correct" for action in job["memory_actions"])


@pytest.mark.parametrize("operation", ["create", "refine"])
@pytest.mark.parametrize("replacement", [False, True])
def test_correction_and_new_understanding_share_one_ordered_save(turn, operation, replacement):
    consolidator, store, arguments, completion = turn
    first_trace, _ = consolidator.consolidate_turn(**arguments)
    memory_set_id = arguments["state"]["selected_memory_set_id"]
    memory_id = first_trace["updated_memory_unit_ids"][0]
    original, = store.list_memory_units_by_id(memory_set_id=memory_set_id, memory_unit_ids=[memory_id])
    if operation == "refine":
        erroneous = {**original, "summary_text": "普段は緑茶を好む。", "object_ref_or_value": "緑茶"}
        store.persist_memory_actions(memory_actions=[consolidator.action_resolver.build_memory_action(
            operation="refine", memory_set_id=memory_set_id, finished_at=CURRENT_TIME,
            memory_unit=erroneous, related_memory_unit_ids=[], before_snapshot=original,
            after_snapshot=erroneous, reason="検証用の誤更新。", event_ids=[arguments["events"][0]["event_id"]],
        )])
    with sqlite3.connect(store.memory_db_path) as conn:
        revision_id = conn.execute(
            "SELECT revision_id FROM revisions WHERE memory_unit_id = ? ORDER BY rowid DESC LIMIT 1",
            (memory_id,),
        ).fetchone()[0]
    payload = interpretation()
    payload["correction_status"] = "selected"
    payload["selected_targets"] = [{
        "revision_id": revision_id, "memory_unit_id": memory_id, "reason_summary": "先ほどの誤りを訂正した。",
    }]
    candidate = payload["candidate_memory_units"][0]
    candidate["qualifiers_hint"]["replace_prior"] = True
    if replacement:
        candidate.update(object_hint={"kind": "value", "value": "紅茶"}, summary_text="普段は紅茶を好む。")
    event = {**arguments["events"][0], "event_id": "event:correction", "cycle_id": "cycle:correction",
             "created_at": "2026-10-17T09:01:00+09:00", "text": "先ほどの申告を訂正する。"}
    store.append_events(events=[event])
    arguments.update(cycle_id=event["cycle_id"], finished_at=event["created_at"], input_text=event["text"],
                     events=[event], correction_candidate_memory_unit_ids=[memory_id])
    completion.reset_mock()
    completion.return_value = provider_response(payload)

    trace, job = consolidator.consolidate_turn(**arguments)

    assert completion.call_count == 1
    assert trace["correction_reconciliation"]["result_status"] == "succeeded"
    correction, *normal = job["memory_actions"]
    assert correction["operation"] == "correct"
    assert correction["correction"]["corrects_revision_id"] == revision_id
    assert correction["after_snapshot"]["status"] == ("revoked" if operation == "create" else "confirmed")
    if operation == "refine":
        assert normal[0]["before_snapshot"] == correction["after_snapshot"]
        assert normal[0]["before_snapshot"]["summary_text"] == original["summary_text"]
    active = store.find_memory_units_for_compare(
        **{key: original[key] for key in (
            "memory_set_id", "memory_type", "scope_type", "scope_key", "subject_ref", "predicate",
        )},
    )
    assert len(active) == 1
    assert active[0]["object_ref_or_value"] == candidate["object_hint"]["value"]
    entries = consolidator.vector_indexer._build_vector_index_entries(
        finished_at=event["created_at"], episode=job["episode"], memory_actions=job["memory_actions"],
    )
    for entry in entries:
        if entry["source_kind"] != "memory_unit":
            continue
        saved, = store.list_memory_units_by_id(memory_set_id=memory_set_id, memory_unit_ids=[entry["source_id"]])
        assert entry["status"] == saved["status"]
        assert entry["salience"] == saved["salience"]
        assert entry["text_hash"] == source_text_hash(build_memory_unit_semantic_text(saved))

    with sqlite3.connect(store.memory_db_path) as conn:
        revision_count = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
    consolidator.correction.run = Mock(side_effect=AssertionError("保存済み訂正を再実行しない。"))
    consolidator.vector_indexer.sync = Mock()
    consolidator.reflective.run = Mock(return_value={"result_status": "no_change"})
    result = consolidator.run_postprocess_job(job=job)
    assert result["correction_reconciliation"] == trace["correction_reconciliation"]
    assert result["vector_index_sync"]["result_status"] == "succeeded"
    assert result["relation_index_sync"]["result_status"] == "succeeded"
    with sqlite3.connect(store.memory_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == revision_count


def test_external_change_still_rolls_back_correction_and_new_turn(turn, monkeypatch):
    consolidator, store, arguments, completion = turn
    first_trace, _ = consolidator.consolidate_turn(**arguments)
    memory_id = first_trace["updated_memory_unit_ids"][0]
    with sqlite3.connect(store.memory_db_path) as conn:
        revision_id = conn.execute("SELECT revision_id FROM revisions WHERE memory_unit_id = ?", (memory_id,)).fetchone()[0]
    payload = interpretation()
    payload.update(correction_status="selected", selected_targets=[{
        "revision_id": revision_id, "memory_unit_id": memory_id, "reason_summary": "誤りを訂正した。",
    }])
    completion.return_value = provider_response(payload)
    arguments.update(cycle_id="cycle:correction", finished_at="2026-10-17T09:01:00+09:00",
                     correction_candidate_memory_unit_ids=[memory_id])
    resolve = consolidator.action_resolver.resolve_memory_actions_batch
    tables = ("memory_units", "revisions", "memory_links", "episodes", "episode_affects", "mood_state")
    after_external_update = {}

    def change_before_save(**kwargs):
        actions = resolve(**kwargs)
        unit, = store.list_memory_units_by_id(memory_set_id=kwargs["memory_set_id"], memory_unit_ids=[memory_id])
        changed = {**unit, "salience": 0.9}
        store.persist_memory_actions(memory_actions=[consolidator.action_resolver.build_memory_action(
            operation="reinforce", memory_set_id=kwargs["memory_set_id"], finished_at=arguments["finished_at"],
            memory_unit=changed, related_memory_unit_ids=[], before_snapshot=unit, after_snapshot=changed,
            reason="別処理による更新。", event_ids=[],
        )])
        with sqlite3.connect(store.memory_db_path) as conn:
            after_external_update.update({table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables})
        return actions

    monkeypatch.setattr(consolidator.action_resolver, "resolve_memory_actions_batch", change_before_save)
    with pytest.raises(MemoryWriteConflictError):
        consolidator.consolidate_turn(**arguments)
    with sqlite3.connect(store.memory_db_path) as conn:
        assert {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables} == after_external_update


@pytest.mark.parametrize("open_loops", [[None], [""], ["   "], [42]])
def test_interpretation_rejects_invalid_open_loops(open_loops):
    payload = interpretation()
    payload["episode"]["open_loops"] = open_loops
    with pytest.raises(LLMError, match="open_loops"):
        validate_memory_interpretation_contract(payload)


@pytest.mark.parametrize('bad_grounding', [
    grounding(refs=['missing']),
    grounding(refs=['event:input', 'event:input']),
    grounding(refs=['event:speech']),
    grounding(refs=['event:speech'], support='inference'),
    grounding(refs=['event:decision'], support='verified_result'),
    grounding(refs=['event:input', 'event:speech'], support='independent_events'),
    grounding(support='unsupported'),
])
def test_invalid_primary_evidence_is_repaired_then_fails_without_writes(turn, bad_grounding):
    consolidator, store, arguments, completion = turn
    payload = interpretation()
    payload['candidate_memory_units'][0]['grounding'] = bad_grounding
    completion.return_value = provider_response(payload)
    with pytest.raises(LLMError, match='grounding'):
        consolidator.consolidate_turn(**arguments)
    assert completion.call_count == 2
    with sqlite3.connect(store.memory_db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM memory_units').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 0


def test_assistant_example_is_kept_as_an_episode_without_becoming_a_person_fact(turn):
    consolidator, store, arguments, completion = turn
    arguments['input_text'] = 'こだわりかぁ～難しいな。具体的などんな事？'
    arguments['speech_payload'] = {'speech_text': '例えば可読性とパフォーマンスなどです。'}
    payload = interpretation()
    payload['episode']['summary_text'] = '本人が具体例を求め、自己が可読性や性能を例に挙げた。'
    candidate = payload['candidate_memory_units'][0]
    candidate.update(summary_text='本人は可読性と性能にこだわる。', evidence_text=arguments['speech_payload']['speech_text'],
                     grounding=grounding(refs=['event:speech'], support='unsupported', retention='episode_only'))
    completion.return_value = provider_response(payload)
    trace, job = consolidator.consolidate_turn(**arguments)
    assert trace['memory_action_count'] == 0
    assert job['memory_actions'] == []
    assert trace['candidate_grounding'][0]['grounding']['retention'] == 'episode_only'
    with sqlite3.connect(store.memory_db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM memory_units').fetchone()[0] == 0
        assert conn.execute('SELECT summary_text FROM episodes').fetchone()[0] == payload['episode']['summary_text']


def test_high_confidence_inference_is_not_confirmed_or_treated_as_explicit(turn):
    consolidator, store, arguments, completion = turn
    payload = interpretation()
    payload['candidate_memory_units'][0]['grounding'] = grounding(support='inference')
    completion.return_value = provider_response(payload)
    trace, _ = consolidator.consolidate_turn(**arguments)
    unit, = store.list_memory_units_by_id(memory_set_id=arguments['state']['selected_memory_set_id'],
                                          memory_unit_ids=trace['updated_memory_unit_ids'])
    assert unit['status'] == 'inferred'
    assert unit['confidence'] == 0.86
    assert unit['last_confirmed_at'] is None
    assert not consolidator.action_resolver._candidate_is_explicit(unit)
    assert not consolidator.action_resolver._candidate_is_explicit({**unit, 'status': 'confirmed'})


def test_transient_visual_state_is_not_saved_as_a_long_term_fact(turn):
    consolidator, store, arguments, completion = turn
    arguments['memory_context']['current_visual_observations'] = [{'visual_observation_id': 'visual_observation:current', 'summary_text': 'PCにGit Graphが表示されている。'}]
    payload = interpretation()
    payload['candidate_memory_units'][0].update(
        memory_type='fact', summary_text='本人は現在PC作業中である。',
        grounding=grounding(refs=['visual_observation:current'], support='verified_result', retention='episode_only'))
    completion.return_value = provider_response(payload)
    trace, _ = consolidator.consolidate_turn(**arguments)
    assert trace['memory_action_count'] == 0
    assert trace['turn_consolidation_status'] == 'succeeded'


def test_registered_self_commitment_keeps_its_confirmed_status_and_execution_time(turn):
    consolidator, store, arguments, completion = turn
    arguments['speech_payload'] = {'speech_text': '明日の9時にお知らせします。'}
    arguments['memory_context']['autonomous_run_summary'] = {'run_id': 'autonomous_run:reminder', 'status': 'waiting_timer'}
    payload = interpretation()
    candidate = payload['candidate_memory_units'][0]
    candidate.update(memory_type='commitment', scope='self', subject_hint='self', predicate_hint='notify',
                     summary_text='明日の9時に一度通知する約束を引き受けた。',
                     qualifiers_hint={'commitment_actor': 'self'},
                     grounding=grounding(refs=['event:speech', 'autonomous_run:reminder'], support='self_commitment'))
    completion.return_value = provider_response(payload)
    trace, _ = consolidator.consolidate_turn(**arguments)
    unit, = store.list_memory_units_by_id(memory_set_id=arguments['state']['selected_memory_set_id'],
                                          memory_unit_ids=trace['updated_memory_unit_ids'])
    assert unit['status'] == 'confirmed'
    assert unit['valid_to'] is None
    assert unit['qualifiers']['grounding']['support_kind'] == 'self_commitment'


def test_person_statement_cannot_use_another_persons_identity(turn):
    consolidator, _, arguments, completion = turn
    arguments['memory_context']['people_context'].append({'person_ref': 'person:other', 'display_name': '別人'})
    payload = interpretation()
    payload['candidate_memory_units'][0]['subject_hint'] = 'person:other'
    completion.return_value = provider_response(payload)
    with pytest.raises(LLMError, match='speaker_ref'):
        consolidator.consolidate_turn(**arguments)


def test_assistant_example_cannot_be_saved_by_calling_it_a_relationship_inference(turn):
    consolidator, _, arguments, completion = turn
    invalid = interpretation()
    invalid['candidate_memory_units'][0].update(
        memory_type='interpretation', scope='relationship', subject_hint='self|person:test',
        summary_text='本人はコードの可読性や性能にこだわりを持っている。',
        grounding=grounding(refs=['event:speech'], support='inference'))
    repaired = deepcopy(invalid)
    repaired['candidate_memory_units'][0]['grounding']['retention'] = 'episode_only'
    completion.side_effect = [provider_response(invalid), provider_response(repaired)]
    trace, _ = consolidator.consolidate_turn(**arguments)
    assert trace['turn_consolidation_status'] == 'succeeded'
    assert trace['memory_action_count'] == 0
    assert completion.call_count == 2
