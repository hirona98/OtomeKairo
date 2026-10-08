import json
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_virtual_time_conversation import ConversationVerification, VerificationError


def trace(cycle_id, kind, *, source_request=None, next_request=None):
    result = {"result_kind": kind}
    if source_request is not None:
        result["capability_result_followup_summary"] = {"source_request_summary": {"request_id": source_request}}
    if next_request is not None:
        result["capability_request_summary"] = {"request_id": source_request or next_request}
        if source_request is not None:
            result["capability_result_followup_summary"]["followup_result_summary"] = {
                "result_kind": kind, "followup_capability_request_summary": {"request_id": next_request}}
    if kind == "speech":
        result["speech_summary"] = "確認が完了しました。"
    return {"cycle_id": cycle_id, "cycle_summary": {"finished_at": "2026-10-04T09:00:00+09:00", "failed": False},
            "result_trace": result}


def runner(tmp_path, traces):
    value = ConversationVerification.__new__(ConversationVerification)
    value.data = tmp_path
    value.events = []
    value.event_lock = threading.RLock()
    value.websocket = None
    value.provider_errors = []
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("CREATE TABLE cycle_traces(cycle_id TEXT, payload_json TEXT)")
        conn.execute("CREATE TABLE events(cycle_id TEXT, kind TEXT, role TEXT, text TEXT)")
        conn.executemany("INSERT INTO cycle_traces VALUES (?, ?)", [(t["cycle_id"], json.dumps(t)) for t in traces])
        conn.executemany("INSERT INTO events VALUES (?, 'speech', 'assistant', ?)",
                         [(t["cycle_id"], "確認が完了しました。") for t in traces if t["result_trace"]["result_kind"] == "speech"])
    return value


def delivered(cycle_id="cycle:final", request_id="request:second"):
    return {"type": "assistant_message", "data": {"cycle_id": cycle_id, "request_id": request_id,
            "interaction_ref": "interaction:a", "recipient_person_refs": ["person:a"],
            "message": "確認が完了しました。"}}


def test_async_chain_preserves_both_steps_and_uses_the_delivered_answer(tmp_path):
    first = trace("cycle:first", "capability_request", source_request="request:first", next_request="request:second")
    final = trace("cycle:final", "speech", source_request="request:second")
    unrelated = trace("cycle:unrelated", "speech", source_request="request:unrelated")
    value = runner(tmp_path, [unrelated, final, first])
    value.events = [delivered("cycle:unrelated", "request:unrelated"), delivered()]
    response, followups, messages = value.complete_capability_chain(
        {"result_kind": "capability_request", "speech": None},
        trace("cycle:origin", "capability_request", next_request="request:first"),
        interaction_ref="interaction:a", recipient_person_refs=["person:a"])
    assert response["source"] == "websocket"
    assert response["cycle_id"] == "cycle:final"
    assert response["speech"]["text"] == messages[0]["message"]
    assert [t["cycle_id"] for t in followups] == ["cycle:first", "cycle:final"]
    assert messages == [value.events[-1]["data"]]


@pytest.mark.parametrize("field,bad", [
    ("request_id", "request:other"), ("interaction_ref", "interaction:b"),
    ("recipient_person_refs", ["person:b"]), ("message", "別の本文。"),
])
def test_followup_delivery_requires_its_request_and_intended_recipient(tmp_path, field, bad):
    value = runner(tmp_path, [trace("cycle:final", "speech", source_request="request:second")])
    event = delivered()
    event["data"][field] = bad
    value.events = [event]
    with pytest.raises(VerificationError, match="一致しません"):
        value.complete_capability_chain({"result_kind": "capability_request"},
            trace("cycle:origin", "capability_request", next_request="request:second"),
            interaction_ref="interaction:a", recipient_person_refs=["person:a"])


def test_duplicate_followup_and_duplicate_delivery_are_explicit_failures(tmp_path):
    final = trace("cycle:final", "speech", source_request="request:second")
    value = runner(tmp_path, [final, trace("cycle:duplicate", "speech", source_request="request:second")])
    with pytest.raises(VerificationError, match="後続判断が重複"):
        value.capability_followup_trace("request:second")
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("DELETE FROM cycle_traces WHERE cycle_id = ?", ("cycle:duplicate",))
    value.events = [delivered(), delivered()]
    with pytest.raises(VerificationError, match="返答配送が重複"):
        value.complete_capability_chain({"result_kind": "capability_request"},
            trace("cycle:origin", "capability_request", next_request="request:second"))


def test_repeated_request_id_is_not_accepted_as_an_endless_chain(tmp_path):
    value = runner(tmp_path, [trace("cycle:first", "capability_request", source_request="request:first", next_request="request:first")])
    with pytest.raises(VerificationError, match="循環"):
        value.complete_capability_chain({"result_kind": "capability_request"},
            trace("cycle:origin", "capability_request", next_request="request:first"))


def test_missing_next_request_is_an_explicit_failure(tmp_path):
    first = trace("cycle:first", "capability_request", source_request="request:first", next_request="request:second")
    first["result_trace"]["capability_result_followup_summary"]["followup_result_summary"].pop("followup_capability_request_summary")
    value = runner(tmp_path, [first])
    with pytest.raises(VerificationError, match="次の能力要求"):
        value.complete_capability_chain({"result_kind": "capability_request"},
            trace("cycle:origin", "capability_request", next_request="request:first"))


def test_failed_followup_is_not_a_successful_response(tmp_path):
    final = trace("cycle:final", "speech", source_request="request:second")
    final["cycle_summary"]["failed"] = True
    value = runner(tmp_path, [final])
    with pytest.raises(VerificationError, match="後続判断が失敗"):
        value.complete_capability_chain({"result_kind": "capability_request"},
            trace("cycle:origin", "capability_request", next_request="request:second"))


def test_full_delivered_speech_is_compared_with_the_primary_event_not_its_summary(tmp_path):
    final = trace("cycle:final", "speech", source_request="request:second")
    final["result_trace"]["speech_summary"] = "確認が完了…"
    value = runner(tmp_path, [final])
    full_text = "観測結果の全文です。" * 50
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("UPDATE events SET text = ?", (full_text,))
    event = delivered()
    event["data"]["message"] = full_text
    value.events = [event]
    response, _, _ = value.complete_capability_chain({"result_kind": "capability_request"},
        trace("cycle:origin", "capability_request", next_request="request:second"))
    assert response["speech"]["text"] == full_text


@pytest.mark.parametrize("count", [0, 2])
def test_followup_requires_one_primary_saved_speech(tmp_path, count):
    value = runner(tmp_path, [trace("cycle:final", "speech", source_request="request:second")])
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("DELETE FROM events")
        for _ in range(count):
            conn.execute("INSERT INTO events VALUES ('cycle:final', 'speech', 'assistant', '確認が完了しました。')")
    value.events = [delivered()]
    with pytest.raises(VerificationError, match="保存済み発話が一意"):
        value.complete_capability_chain({"result_kind": "capability_request"},
            trace("cycle:origin", "capability_request", next_request="request:second"))
