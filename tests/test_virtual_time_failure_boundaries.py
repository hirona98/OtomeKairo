from datetime import datetime
from pathlib import Path
import sys
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_virtual_time_conversation import ConversationVerification, VerificationError, VirtualClock


@pytest.mark.parametrize('memory_status', ['failed', 'succeeded'])
def test_drain_detects_turn_consolidation_failure_without_a_failed_postprocess_job(tmp_path, memory_status):
    with sqlite3.connect(tmp_path / 'memory.db') as db:
        db.execute('CREATE TABLE cycle_traces (selected_memory_set_id TEXT, payload_json TEXT)')
        for memory_set, status in [('memory:test', memory_status), ('memory:other', 'failed')]:
            db.execute('INSERT INTO cycle_traces VALUES (?, ?)',
                       (memory_set, json.dumps({'memory_trace': {'turn_consolidation_status': status}})))
    runner = ConversationVerification.__new__(ConversationVerification)
    runner.data, runner.memory_set = tmp_path, 'memory:test'
    runner.api = Mock()
    runner.api.get.return_value = {'runtime_summary': {'pending_memory_job_count': 0, 'memory_job_in_progress': False}}
    runner.service = SimpleNamespace(store=Mock(), _cycle_coordinator=Mock())
    runner.service.store.list_memory_postprocess_jobs.return_value = []
    runner.service._cycle_coordinator.snapshot.return_value = {'active': False}
    runner.wait = lambda predicate, label: predicate()
    if memory_status == 'failed':
        with pytest.raises(VerificationError, match='記憶統合'):
            runner.drain()
    else:
        runner.drain()


def test_provider_failure_stops_wait_before_accepting_a_completed_operation():
    runner = ConversationVerification.__new__(ConversationVerification)
    runner.clock = VirtualClock(datetime.fromisoformat("2026-10-04T09:00:00+09:00"))
    runner.websocket = None
    runner.provider_errors = [{"operation": "complete_text", "causes": [{"http_status": 402}]}]
    with pytest.raises(VerificationError, match="外部LLM API"):
        runner.wait(lambda: True, "provider failure")


@pytest.mark.parametrize("operation", ["complete_text", "generate_embeddings"])
def test_provider_error_is_published_before_the_run_finishes(tmp_path, monkeypatch, operation):
    from otomekairo.llm import transport
    runner = ConversationVerification.__new__(ConversationVerification)
    runner.clock = VirtualClock(datetime.fromisoformat("2026-10-04T09:00:00+09:00"))
    runner.artifacts = tmp_path
    runner.secret_values = []
    runner.provider_errors, runner.usage_metrics = [], []
    runner.api_attempts = {"complete_text": 0, "generate_embeddings": 0}
    failure = RuntimeError("upstream unavailable")
    monkeypatch.setattr(transport, operation, Mock(side_effect=failure))
    # Restore observer bindings after this test, including aliases in loaded modules.
    import contextlib
    from unittest.mock import patch
    with contextlib.ExitStack() as stack:
        for module_name, module in list(sys.modules.items()):
            if module_name.startswith("otomekairo.") and module is not None:
                for attribute in ("complete_text", "generate_embeddings", "_log_completion_metrics"):
                    if hasattr(module, attribute):
                        stack.enter_context(patch.object(module, attribute, getattr(module, attribute)))
        runner._install_transport_observers()
        with pytest.raises(RuntimeError) as raised:
            getattr(transport, operation)(model_config={"model": "fixture/model"})
        assert raised.value is failure
        published = json.loads((tmp_path / "provider-errors.json").read_text())
        assert published == runner.provider_errors
        assert published[0]["operation"] == operation
        assert published[0]["causes"] == [{"type": "RuntimeError", "http_status": None, "error_code": None}]
        assert runner.api_attempts[operation] == 1


def test_credential_values_are_removed_from_nested_artifacts_and_log_text():
    # These values are invented fixtures, never actual credentials.
    source = {"api_key": "fixture-api-credential", "mcp": {"headers": {"Authorization": "Bearer fixture-mcp-credential"}}}
    runner = ConversationVerification.__new__(ConversationVerification)
    runner.secret_values = runner._secrets(source)
    cleaned = runner.scrub({"headers": source["mcp"]["headers"], "records": [
        {"api_key": source["api_key"], "message": "Failed for fixture-api-credential; Bearer fixture-mcp-credential"}]})
    assert cleaned["headers"] == "[REDACTED]"
    assert cleaned["records"][0]["api_key"] == "[REDACTED]"
    assert cleaned["records"][0]["message"] == "Failed for [REDACTED]; [REDACTED]"


def test_visual_evaluator_receives_the_selected_image_and_rejects_missing_fixture(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from otomekairo.llm import transport
    completion = Mock(return_value='{"verdict":"pass","reason":"画像に対応。","evidence":[]}')
    monkeypatch.setattr(transport, "complete_text", completion)
    runner = ConversationVerification.__new__(ConversationVerification)
    runner.args = SimpleNamespace(mock=False)
    runner.rows = []
    runner.prior_history = []
    runner.evaluations = []
    runner.state = {"selected_persona_id": "persona:test", "personas": {"persona:test": {"persona_prompt": "テスト人格。"}}}
    runner.judge_model = {}
    runner.camera_available = True
    runner.vision_image = 'data:image/jpeg;base64,Y3VycmVudA=='
    selected = 'data:image/jpeg;base64,c2VsZWN0ZWQ='
    runner.image_fixtures = [{"fixture_id": "selected", "data_uri": selected}]
    runner.evaluation_extra = lambda row: {}
    runner.save = Mock()
    row = {"case_id": "visual-evaluation", "person": "a", "text": "今の映像を教えて。",
           "expected": "画像の内容を確認。", "virtual_time": "2026-10-04T09:00:00+09:00",
           "response": {}, "state": {}, "trace": {}, "evaluation_fixture_id": "selected"}
    runner.evaluate(row)
    content = completion.call_args.kwargs["messages"][1]["content"]
    assert content[1] == {"type": "image_url", "image_url": {"url": selected}}
    completion.reset_mock()
    with pytest.raises(VerificationError, match="画像fixture"):
        runner.evaluate({**row, "evaluation_fixture_id": "missing"})
    completion.assert_not_called()
