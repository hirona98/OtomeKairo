"""欠損参照の永続修復と根拠欠損の伝達を確認する。自然文の精度は評価しない。"""
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.defaults import build_default_state
from otomekairo.llm.prompts import _compact_recall_pack
from otomekairo.recall.builder import RecallBuilder
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.store.file_store import SQLiteMemoryStore
from otomekairo.store.memory_links import MemoryWriteConflictError
from test_decision_contract import _current_input, _decision_context, _persona_context, _wire_response


STAMP = "2026-10-15T09:00:00+09:00"
MEMORY_SET = "memory_set:test"
VALID = "event:valid"
MISSING = "event:missing"


def _unit(**overrides):
    return {
        "memory_unit_id": "memory:test", "memory_set_id": MEMORY_SET,
        "memory_type": "preference", "scope_type": "entity", "scope_key": "person:other",
        "subject_ref": "person:other", "predicate": "prefers", "object_ref_or_value": "麦茶",
        "summary_text": "麦茶の好みと共有条件。", "status": "confirmed",
        "confidence": 0.8, "salience": 0.7, "formed_at": STAMP,
        "qualifiers": {"source_participant_refs": ["person:other"]},
        "evidence_event_ids": [VALID, MISSING], **overrides,
    }


def _episode(**overrides):
    return {
        "episode_id": "episode:test", "cycle_id": "cycle:source", "memory_set_id": MEMORY_SET,
        "episode_type": "conversation", "primary_scope_type": "entity", "primary_scope_key": "person:other",
        "summary_text": "好みを話した。", "open_loops": [], "salience": 0.7,
        "formed_at": STAMP, "linked_event_ids": [VALID, MISSING], **overrides,
    }


@pytest.fixture
def store(tmp_path):
    value = SQLiteMemoryStore(tmp_path)
    value.append_events(events=[{
        "event_id": VALID, "cycle_id": "cycle:source", "memory_set_id": MEMORY_SET,
        "kind": "conversation_input", "role": "person", "created_at": STAMP,
        "speaker_ref": "person:other", "participant_refs": ["person:other"],
        "interaction_ref": "interaction:other", "text": "  好みの共有範囲は確認が必要です。\n原文末尾  ",
    }])
    with value._memory_db() as conn:
        value._upsert_memory_unit(conn, _unit())
        value._insert_episode(conn, _episode())
        value._insert_revision(conn, {
            "revision_id": "revision:source", "memory_set_id": MEMORY_SET,
            "memory_unit_id": "memory:test", "operation": "create", "occurred_at": STAMP,
            "before_snapshot": None, "after_snapshot": _unit(), "evidence_event_ids": [VALID, MISSING],
            "reason": "架空の本人報告を記憶した。",
        })
    value.register_interaction_participants(
        memory_set_id=MEMORY_SET, participants=[{"person_ref": "person:other", "display_name": "架空の人物"}],
        observed_at=STAMP, evidence_event_ids=[VALID, MISSING],
    )
    return value


def _load(store, ids=None, limit=10):
    return store.load_and_repair_event_evidence(
        memory_set_id=MEMORY_SET, event_ids=[VALID, MISSING] if ids is None else ids,
        limit=limit, cycle_id="cycle:current", detected_at=STAMP,
    )


def _rows(store, table, memory_set=MEMORY_SET):
    with store._memory_db() as conn:
        return [dict(row) for row in conn.execute(
            f"SELECT * FROM {table} WHERE memory_set_id = ? ORDER BY rowid", (memory_set,),
        )]


def _audits(store, memory_set=MEMORY_SET):
    return [json.loads(row["payload_json"]) for row in _rows(store, "events", memory_set)
            if row["kind"] == "event_reference_repaired"]


def test_repair_removes_only_missing_references_from_all_current_owners(store):
    with store._memory_db() as conn:
        store._upsert_memory_unit(conn, _unit(memory_unit_id="memory:unselected"))
        store._upsert_memory_unit(conn, _unit(memory_unit_id="memory:other-set", memory_set_id="memory_set:other"))
    historical = _rows(store, "revisions")
    other_set = _rows(store, "memory_units", "memory_set:other")
    before = {table: _rows(store, table) for table in ("memory_units", "episodes", "entity_registry")}
    result = _load(store)
    assert result["missing_event_ids"] == [MISSING]
    assert result["events"][0]["text"] == "  好みの共有範囲は確認が必要です。\n原文末尾  "
    assert len(result["repaired_sources"]) == 4
    for table, reference_key in (("memory_units", "evidence_event_ids"), ("episodes", "linked_event_ids"),
                                 ("entity_registry", "evidence_event_ids")):
        for original, updated in zip(before[table], _rows(store, table), strict=True):
            payload = json.loads(updated["payload_json"])
            assert payload[reference_key] == json.loads(updated[f"{reference_key}_json"]) == [VALID]
            assert payload["event_reference_integrity"] == {"removed_event_ids": [MISSING], "repaired_at": STAMP}
            payload.pop("event_reference_integrity")
            payload[reference_key] = [VALID, MISSING]
            assert payload == json.loads(original["payload_json"])
    assert _rows(store, "revisions") == historical
    assert _rows(store, "memory_units", "memory_set:other") == other_set
    audit = _audits(store)
    assert len(audit) == 1
    assert audit[0]["cycle_id"] == "cycle:current"
    assert audit[0]["missing_event_ids"] == [MISSING]
    assert audit[0]["repaired_sources"] == result["repaired_sources"]
    assert "text" not in audit[0]
    _load(store)  # 古い取得候補が残っても同じ修復監査を重複させない。
    assert len(_audits(store)) == 1


@pytest.mark.parametrize("all_missing", [False, True])
def test_same_topic_uses_repaired_references_without_losing_missing_evidence_context(store, all_missing):
    if all_missing:
        with store._memory_db() as conn:
            conn.execute("DELETE FROM events WHERE event_id = ?", (VALID,))
    service = ServiceInputPipelineMixin()
    service.store = store
    builder = RecallBuilder.__new__(RecallBuilder)
    builder.store = store
    for index in range(2):
        unit = store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=["memory:test"])[0]
        pack = {"person_model": [builder._to_memory_item(unit)]}
        with patch.object(store, "load_and_repair_event_evidence", wraps=store.load_and_repair_event_evidence) as loading:
            disclosure = service._build_disclosure_context(
                memory_set_id=MEMORY_SET, current_input=_current_input(), recall_pack=pack,
                cycle_id=f"cycle:current-{index}", detected_at=STAMP,
            )
        if index == 1:
            for call in loading.call_args_list:
                assert MISSING not in call.kwargs["event_ids"]
                assert not all_missing or not call.kwargs["event_ids"]
        projected = _compact_recall_pack(pack)["person_model"][0]
        assert projected["event_reference_integrity"]["missing_event_count"] == (2 if all_missing else 1)
        assert "removed_event_ids" not in projected["event_reference_integrity"]
        assert pack["person_model"][0]["evidence_event_ids"] == ([] if all_missing else [VALID])
        assert len(disclosure["person_utterances"]) == (0 if all_missing else 1)
        candidate = {
            "kind": "speech", "speech_text": "確認できる範囲で応答します。",
            "reason_code": "source_scope", "reason_summary": "根拠の確認範囲を伝える。",
            "requires_confirmation": False, "pending_intent": None, "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {"primary_factor_ref": None, "supporting_factor_refs": [], "suppressed_factors": [],
                                     "summary_text": "確認できる根拠で判断する。"},
            "target_stances": [{"target": "outward_speech", "stance": "advance", "reason_summary": "問いに答える。"}],
        }
        context = replace(_decision_context([]), recall_pack=pack, disclosure_context=disclosure)
        with patch("otomekairo.llm.client.complete_text", return_value=_wire_response(candidate)) as completion:
            assert LLMClient().generate_decision(
                model_config={"model": "provider/test"}, persona_context=_persona_context(), context=context,
            ) == candidate
        completion.assert_called_once()
        assert "event_reference_integrity" in json.dumps(completion.call_args.kwargs["messages"])
    assert len(_audits(store)) == 1


def test_standard_recall_and_update_source_reports_share_the_repair_result(store):
    builder = RecallBuilder.__new__(RecallBuilder)
    builder.store = store
    builder.llm = Mock(spec=[])
    pack = {"episodic_evidence": [builder._to_episode_item(_episode())]}
    evidence = builder._build_event_evidence(
        memory_set_id=MEMORY_SET, cycle_id="cycle:recall", detected_at=STAMP,
        primary_recall_focus="episodic", recall_hint={"time_reference": "past", "risk_flags": []}, sections=pack,
    )
    assert len(evidence["event_evidence"]) == 1
    assert evidence["event_evidence_loading"]["failed_items"][0]["event_id"] == MISSING
    assert pack["episodic_evidence"][0]["linked_event_ids"] == [VALID]
    assert _compact_recall_pack(pack)["episodic_evidence"][0]["event_reference_integrity"]["missing_event_count"] == 1
    prior = store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=["memory:test"])[0]
    prior["status"] = "superseded"
    links = [{"label": "derived_from", "source_memory_unit_id": "memory:selected", "target_memory_unit": prior}]
    reports = builder._build_memory_link_source_reports(
        memory_set_id=MEMORY_SET, cycle_id="cycle:recall", detected_at=STAMP,
        memory_links=links, selected_memory_ids=["memory:selected"],
    )
    assert reports["missing_event_ids"] == []
    assert "event_id" not in reports["reports"][0]
    assert reports["reports"][0]["text"] == evidence["event_evidence"][0]["text"]
    assert builder.llm.mock_calls == []
    assert len(_audits(store)) == 1


def test_another_recall_branch_can_refresh_a_snapshot_loaded_before_repair(store):
    builder = RecallBuilder.__new__(RecallBuilder)
    stale = {"person_model": [builder._to_memory_item(_unit())]}
    _load(store)
    service = ServiceInputPipelineMixin()
    service.store = store
    disclosure = service._build_disclosure_context(
        memory_set_id=MEMORY_SET, cycle_id="cycle:another-branch", detected_at=STAMP,
        current_input=_current_input(), recall_pack=stale,
    )
    assert disclosure["missing_event_ids"] == [MISSING]
    assert stale["person_model"][0]["evidence_event_ids"] == [VALID]
    assert _compact_recall_pack(stale)["person_model"][0]["event_reference_integrity"]["missing_event_count"] == 1
    assert len(_audits(store)) == 1


def test_recall_builder_preserves_missing_update_source_in_links_and_next_recall(store, monkeypatch):
    with store._memory_db() as conn:
        store._upsert_memory_unit(conn, _unit(status="superseded"))
        store._upsert_memory_unit(conn, _unit(memory_unit_id="memory:selected", evidence_event_ids=[VALID]))
        store._upsert_memory_link(conn, {
            "memory_link_id": "memory_link:test", "memory_set_id": MEMORY_SET,
            "source_memory_unit_id": "memory:selected", "target_memory_unit_id": "memory:test",
            "label": "derived_from", "confidence": 0.8, "evidence_revision_id": "revision:source",
            "created_at": STAMP, "updated_at": STAMP,
        })
    state = build_default_state()
    state["memory_sets"][MEMORY_SET] = deepcopy(state["memory_sets"][state["selected_memory_set_id"]])
    state["selected_memory_set_id"] = MEMORY_SET
    state["memory_sets"][MEMORY_SET]["embedding"]["model"] = "mock"
    state["model_presets"][state["selected_model_preset_id"]]["model"] = "mock"
    llm = LLMClient()

    def select(**kwargs):
        return {"section_selection": [{"section_name": section["section_name"],
                "candidate_refs": [item["candidate_ref"] for item in section["candidates"]]}
                for section in kwargs["source_pack"]["candidate_sections"]], "conflict_summaries": []}

    monkeypatch.setattr(LLMClient, "generate_recall_pack_selection", Mock(side_effect=select))
    builder = RecallBuilder(store=store, llm=llm)
    for index in range(2):
        with patch.object(store, "load_and_repair_event_evidence", wraps=store.load_and_repair_event_evidence) as loading:
            pack = builder.build_recall_pack(
                current_visual_observations=[], state=state, cycle_id=f"cycle:recall-{index}", current_time=STAMP,
                augmented_query_text="以前の報告と現在の理解を教えて。", current_person_ref="person:other",
                recall_hint={"primary_recall_focus": "fact", "secondary_recall_focuses": [], "time_reference": "past",
                             "focus_scopes": [], "mentioned_entities": [], "mentioned_topics": [], "risk_flags": []},
            )
        context = _compact_recall_pack(pack)["memory_link_context"]
        target = next(item["target_claim"] for item in context["representative_links"] if item["label"] == "derived_from")
        assert target["event_reference_integrity"]["missing_event_count"] == 1
        assert context["source_report_loading"]["missing_event_count"] == (1 if index == 0 else 0)
        assert context["source_reports"][0]["text"].endswith("原文末尾  ")
        if index == 1:
            assert all(MISSING not in call.kwargs["event_ids"] for call in loading.call_args_list)
    assert len(_audits(store)) == 1


def test_old_updates_cannot_restore_repaired_references_and_clone_preserves_integrity(store):
    _load(store)
    action = {
        "operation": "reinforce", "revision_id": "revision:stale", "memory_set_id": MEMORY_SET,
        "memory_unit_id": "memory:test", "occurred_at": STAMP, "reason": "記憶を再確認した。",
        "memory_unit": _unit(), "before_snapshot": _unit(), "after_snapshot": _unit(), "evidence_event_ids": [VALID],
    }
    with pytest.raises(MemoryWriteConflictError):
        store.persist_memory_actions(memory_actions=[action])
    action["before_snapshot"] = store.list_memory_units_by_id(
        memory_set_id=MEMORY_SET, memory_unit_ids=["memory:test"],
    )[0]
    store.persist_memory_actions(memory_actions=[action])
    with store._memory_db() as conn:
        store._insert_episode(conn, _episode())
        store._upsert_memory_unit(conn, _unit(memory_unit_id="memory:new-from-stale"))
    store.register_interaction_participants(
        memory_set_id=MEMORY_SET, participants=[{"person_ref": "person:other", "display_name": "架空の人物"}],
        observed_at=STAMP, evidence_event_ids=[VALID, MISSING],
    )
    for row in _rows(store, "memory_units"):
        assert json.loads(row["payload_json"])["evidence_event_ids"] == [VALID]
    revision = json.loads(_rows(store, "revisions")[-1]["payload_json"])
    assert revision["after_snapshot"]["evidence_event_ids"] == [VALID]
    assert json.loads(_rows(store, "episodes")[0]["payload_json"])["linked_event_ids"] == [VALID]
    assert json.loads(_rows(store, "entity_registry")[0]["payload_json"])["evidence_event_ids"] == [VALID]
    store.clone_memory_set_records(source_memory_set_id=MEMORY_SET, target_memory_set_id="memory_set:clone")
    for table, reference_key in (("memory_units", "evidence_event_ids"), ("episodes", "linked_event_ids"),
                                 ("entity_registry", "evidence_event_ids")):
        for row in _rows(store, table, "memory_set:clone"):
            cloned = json.loads(row["payload_json"])
            assert cloned["event_reference_integrity"]["removed_event_ids"] == [MISSING]
            assert cloned[reference_key] != [VALID]
            assert len(cloned[reference_key]) == 1


def test_limits_and_speaker_roles_do_not_create_false_missing_events(store):
    assistant = {"event_id": "event:assistant", "cycle_id": "cycle:source", "memory_set_id": MEMORY_SET,
                 "kind": "speech", "role": "assistant", "text": "記録済みの発話。", "created_at": STAMP}
    store.append_events(events=[assistant])
    with store._memory_db() as conn:
        store._upsert_memory_unit(conn, _unit(evidence_event_ids=[VALID, MISSING, "event:unqueried"]))
    result = _load(store, [MISSING, MISSING, VALID, "event:assistant", "event:unqueried"], limit=3)
    assert result["missing_event_ids"] == [MISSING]
    assert {event["role"] for event in result["events"]} == {"person", "assistant"}
    unit = store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=["memory:test"])[0]
    assert unit["evidence_event_ids"] == [VALID, "event:unqueried"]


@pytest.mark.parametrize("failure", ["load", "audit", "malformed_event", "inconsistent_source"])
def test_infrastructure_or_corruption_errors_leave_references_and_audit_unchanged(store, failure):
    if failure == "malformed_event":
        with store._memory_db() as conn:
            conn.execute("UPDATE events SET payload_json = ? WHERE event_id = ?", ("{invalid", VALID))
    if failure == "inconsistent_source":
        with store._memory_db() as conn:
            conn.execute("UPDATE episodes SET linked_event_ids_json = ?", (json.dumps([MISSING]),))
    before = {table: deepcopy(_rows(store, table)) for table in ("memory_units", "episodes", "entity_registry", "events")}
    if failure in {"load", "audit"}:
        method = "_load_evidence_events" if failure == "load" else "_insert_event"
        with patch.object(store, method, side_effect=sqlite3.OperationalError("simulated DB failure")):
            with pytest.raises(sqlite3.OperationalError):
                _load(store)
    else:
        with pytest.raises(ValueError):
            _load(store)
    assert {table: _rows(store, table) for table in before} == before
