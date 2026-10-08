import json
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_virtual_time_conversation import ConversationVerification, VerificationError
from otomekairo.service.input.trace_compact import ServiceInputTraceCompactMixin


def runner(tmp_path, status="completed"):
    run = {"run_id": "run:post", "source_cycle_id": "cycle:origin", "status": status,
           "origin_interaction_ref": "interaction:a", "participant_refs": ["person:a"]}
    request = {"request_id": "request:post", "autonomous_run_id": run["run_id"]}
    trace = {"cycle_id": "cycle:origin", "result_trace": {"capability_request_summary": request}}
    value = ConversationVerification.__new__(ConversationVerification)
    value.data, value.event_lock = tmp_path, threading.RLock()
    value.websocket, value.provider_errors, value.events = None, [], []
    value.service = SimpleNamespace(store=SimpleNamespace(get_autonomous_run=lambda **kw: run,
        get_cycle_trace=lambda cycle_id: trace), _cycle_coordinator=SimpleNamespace(snapshot=lambda: {"active": False}))
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("CREATE TABLE events(payload_json TEXT)")
        if status == "completed":
            for text in ["投稿を確認しています。", "投稿は成功しました。" * 30]:
                event = {"run_id": run["run_id"], "kind": "speech", "role": "assistant",
                         "cycle_id": "cycle:origin", "text": text, "interaction_ref": "interaction:a",
                         "participant_refs": ["person:a"]}
                conn.execute("INSERT INTO events VALUES (?)", (json.dumps(event),))
                value.events.append({"type": "assistant_message", "data": {
                    "source_kind": "autonomous_run", "run_id": run["run_id"], "cycle_id": event["cycle_id"],
                    "message": text, "interaction_ref": event["interaction_ref"],
                    "recipient_person_refs": event["participant_refs"]}})
    return value, run, request, trace


def complete(value, request, trace):
    return value.complete_capability_chain({"result_kind": "capability_request"}, trace,
        interaction_ref="interaction:a", recipient_person_refs=["person:a"])


def test_autonomous_result_uses_run_id_and_preserves_every_delivered_full_speech(tmp_path):
    value, run, request, trace = runner(tmp_path)
    response, followups, deliveries = complete(value, request, trace)
    assert response["speech"]["text"] == deliveries[-1]["message"]
    assert len(deliveries) == 2
    assert followups[0]["source_capability_request"] == request
    assert followups[0]["autonomous_run"] == run
    assert value.refresh_capability_followups(followups)[0]["autonomous_run_events"] == followups[0]["autonomous_run_events"]


def test_async_capability_handoff_keeps_its_registered_run_route(tmp_path):
    value, run, request, trace = runner(tmp_path)
    compact = ServiceInputTraceCompactMixin()._compact_capability_request_summary({
        **request, "capability_id": "mcp.call_tool", "status": "dispatched",
        "arguments": {"body": "fixture content"},
    })
    trace["cycle_summary"] = {"failed": False}
    trace["result_trace"].update(result_kind="capability_request",
        capability_result_followup_summary={"followup_result_summary": {
            "result_kind": "capability_request", "followup_capability_request_summary": compact,
        }})
    queried = []
    def followup(request_id):
        queried.append(request_id)
        assert request_id == "request:initial", "A run-owned request has no ordinary followup cycle."
        return trace
    value.capability_followup_trace = followup
    initial = {"cycle_id": "cycle:previous", "result_trace": {
        "capability_request_summary": {"request_id": "request:initial"},
    }}
    response, followups, deliveries = value.complete_capability_chain(
        {"result_kind": "capability_request"}, initial,
        interaction_ref="interaction:a", recipient_person_refs=["person:a"])
    assert set(queried) == {"request:initial"}
    assert followups[-1]["autonomous_run"] == run
    assert followups[-1]["source_capability_request"]["autonomous_run_id"] == run["run_id"]
    assert "arguments" not in compact
    assert len(deliveries) == 2
    assert response["speech"]["text"] == deliveries[-1]["message"]


def test_normal_capability_summary_does_not_assign_a_run():
    summary = ServiceInputTraceCompactMixin()._compact_capability_request_summary({
        "request_id": "request:ordinary", "capability_id": "mcp.call_tool",
        "autonomous_run_id": None,
    })
    assert "autonomous_run_id" not in summary


@pytest.mark.parametrize("field,bad", [("source_cycle_id", "cycle:other"),
    ("origin_interaction_ref", "interaction:b"), ("participant_refs", ["person:b"])])
def test_autonomous_run_must_belong_to_the_request_origin_and_recipient(tmp_path, field, bad):
    value, run, request, trace = runner(tmp_path)
    run[field] = bad
    with pytest.raises(VerificationError, match="起点・宛先"):
        complete(value, request, trace)


@pytest.mark.parametrize("field,bad", [("message", "成功したという別の文。"),
    ("cycle_id", "cycle:other"), ("recipient_person_refs", ["person:b"]),
    ("interaction_ref", "interaction:b"), ("source_kind", "capability_result")])
def test_autonomous_delivery_must_match_the_primary_saved_event(tmp_path, field, bad):
    value, run, request, trace = runner(tmp_path)
    value.events[-1]["data"][field] = bad
    with pytest.raises(VerificationError, match="返答全文・起点・宛先"):
        complete(value, request, trace)


def test_duplicate_autonomous_delivery_is_an_explicit_failure(tmp_path):
    value, run, request, trace = runner(tmp_path)
    value.events.append(value.events[-1])
    with pytest.raises(VerificationError, match="配送件数"):
        complete(value, request, trace)


def test_future_wait_is_collected_as_waiting_without_claiming_completion(tmp_path):
    value, run, request, trace = runner(tmp_path, "waiting_timer")
    response, followups, deliveries = complete(value, request, trace)
    assert response["result_kind"] == "noop"
    assert response["speech"] is None
    assert followups[0]["autonomous_run"]["status"] == "waiting_timer"
    assert deliveries == []
