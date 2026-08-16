import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from otomekairo.llm.contexts import CurrentInput
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.autonomous_run import AUTONOMOUS_PRE_SEND_CHECK_RETRY_FEEDBACK
from otomekairo.service.autonomous_run import AUTONOMOUS_COMPLETION_REVIEW_RETRY_FEEDBACK
from otomekairo.service.capability import PreSendCheckWithheldError


class AutonomousRunRecoveryTests(unittest.TestCase):
    def _use_mock_model(self, service: OtomeKairoService, state: dict) -> dict:
        preset_id = state["selected_model_preset_id"]
        state["model_presets"][preset_id]["model"] = "mock-test"
        service.store.write_state(state)
        return service.store.read_state()

    def test_pre_send_check_withhold_regenerates_autonomous_step_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            service.store.upsert_autonomous_run(autonomous_run=run)
            step_context = SimpleNamespace(
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=step_context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            initial_step = {
                "action": {
                    "kind": "capability_request",
                    "capability_request": {
                        "capability_id": "mcp.call_tool",
                        "input": {
                            "mcp_server_id": "e-stat",
                            "tool_name": "create_post",
                            "arguments": {"content": "candidate"},
                        },
                    },
                    "speech": None,
                },
                "transition": {"kind": "continue", "next_run_at": None},
                "run_update": {"current_step_summary": "投稿する", "history_summary": "投稿する"},
            }
            retry_step = {
                "action": {"kind": "none", "capability_request": None, "speech": None},
                "transition": {"kind": "cancel", "next_run_at": None},
                "run_update": {"current_step_summary": "送らない", "history_summary": "送らない"},
            }
            generate_autonomous_step = Mock(side_effect=[initial_step, retry_step])
            service.llm = SimpleNamespace(generate_autonomous_step=generate_autonomous_step)
            first_audit = {
                "mcp_server_id": "e-stat",
                "tool_name": "create_post",
                "result_status": "withheld",
                "outcome": "withhold",
                "reason_code": "reviewer_withheld",
                "review_attempt": 1,
            }
            service._dispatch_autonomous_run_capability_request = Mock(
                side_effect=PreSendCheckWithheldError(audit_summary=first_audit)
            )
            service._record_autonomous_pre_send_check_terminal = Mock()
            cancelled = {**run, "status": "cancelled", "completed_at": "2026-08-11T12:00:01+09:00"}
            service._apply_autonomous_step_transition = Mock(return_value=cancelled)
            service._finalize_autonomous_run_commitments = Mock(return_value=cancelled)

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-11T12:00:00+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=False,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(generate_autonomous_step.call_count, 2)
            self.assertEqual(
                service._build_autonomous_step_context.call_args.kwargs[
                    "pre_send_check_feedback"
                ],
                AUTONOMOUS_PRE_SEND_CHECK_RETRY_FEEDBACK,
            )
            service._record_autonomous_pre_send_check_terminal.assert_called_once()

    def test_unperformed_future_speech_is_replaced_by_capability_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run.update(
                {
                    "objective_summary": "ELYTHに投稿を1件作成する。",
                    "current_step_summary": "投稿内容を決める。",
                    "history_summary": "タイムラインと自分の投稿を確認した。",
                    "observed_result_summaries": [
                        {
                            "capability_id": "mcp.call_tool",
                            "tool_name": "get_my_posts",
                            "summary_text": "自分の投稿履歴を確認した。",
                        }
                    ],
                }
            )
            run["source_current_input"]["text"] = "ELYTHに投稿して。"
            service.store.upsert_autonomous_run(autonomous_run=run)
            context = SimpleNamespace(
                run=service._autonomous_run_prompt_summary(run),
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            future_speech_step = {
                "action": {
                    "kind": "speech",
                    "capability_request": None,
                    "speech": {
                        "reason_code": "announce_post",
                        "reason_summary": "これから投稿することを伝える。",
                    },
                },
                "transition": {"kind": "complete", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "投稿の準備を整えた。",
                    "history_summary": "投稿へ移る準備を整えた。",
                },
            }
            create_post_step = {
                "action": {
                    "kind": "capability_request",
                    "capability_request": {
                        "capability_id": "mcp.call_tool",
                        "input": {
                            "mcp_server_id": "elyth",
                            "tool_name": "create_post",
                            "arguments": {"content": "朝の空気を言葉にする。"},
                        },
                    },
                    "speech": None,
                },
                "transition": {"kind": "continue", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "create_post の結果を待つ。",
                    "history_summary": "投稿作成を要求した。",
                },
            }
            generate_step = Mock(side_effect=[future_speech_step, create_post_step])
            review = Mock(
                return_value={
                    "outcome": "continue_run",
                    "reason_summary": "外界への投稿作用がまだ実行されていない。",
                }
            )
            service.llm = SimpleNamespace(
                generate_autonomous_step=generate_step,
                generate_autonomous_completion_review=review,
            )
            service._generate_autonomous_run_speech = Mock(
                return_value={"speech_text": "さて、そろそろ私も何か投稿してみるとします。"}
            )
            service._dispatch_autonomous_run_capability_request = Mock(
                return_value={
                    "request_id": "mcp_call_tool_request:create-post",
                    "capability_id": "mcp.call_tool",
                }
            )
            service._emit_autonomous_run_assistant_message_event = Mock()
            service._persist_autonomous_run_speech_event = Mock()

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-16T09:44:30+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=True,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "waiting_result")
            self.assertIsNone(result["speech_payload"])
            self.assertEqual(result["step"], create_post_step)
            self.assertEqual(generate_step.call_count, 2)
            self.assertEqual(review.call_count, 1)
            self.assertEqual(
                service._build_autonomous_step_context.call_args.kwargs[
                    "completion_review_feedback"
                ],
                AUTONOMOUS_COMPLETION_REVIEW_RETRY_FEEDBACK,
            )
            service._emit_autonomous_run_assistant_message_event.assert_not_called()
            service._persist_autonomous_run_speech_event.assert_not_called()

    def test_completed_effect_report_is_reviewed_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run.update(
                {
                    "objective_summary": "ELYTHに投稿を1件作成する。",
                    "observed_result_summaries": [
                        {
                            "capability_id": "mcp.call_tool",
                            "tool_name": "create_post",
                            "result_status": "completed",
                            "is_error": False,
                            "summary_text": "投稿を作成し、投稿IDを受け取った。",
                        }
                    ],
                }
            )
            service.store.upsert_autonomous_run(autonomous_run=run)
            context = SimpleNamespace(
                run=service._autonomous_run_prompt_summary(run),
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            complete_step = {
                "action": {
                    "kind": "speech",
                    "capability_request": None,
                    "speech": {
                        "reason_code": "post_completed",
                        "reason_summary": "投稿が完了したことを報告する。",
                    },
                },
                "transition": {"kind": "complete", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "投稿作成を完了した。",
                    "history_summary": "create_post の成功結果を確認した。",
                },
            }
            call_order: list[str] = []
            review = Mock(
                side_effect=lambda **_: (
                    call_order.append("review")
                    or {
                        "outcome": "allow_complete",
                        "reason_summary": "投稿作成の成功結果と完了報告が一致する。",
                    }
                )
            )
            service.llm = SimpleNamespace(
                generate_autonomous_step=Mock(return_value=complete_step),
                generate_autonomous_completion_review=review,
            )
            service._generate_autonomous_run_speech = Mock(
                return_value={"speech_text": "投稿が完了しました。"}
            )
            service._emit_autonomous_run_assistant_message_event = Mock(
                side_effect=lambda **_: call_order.append("delivery")
            )
            service._persist_autonomous_run_speech_event = Mock(return_value=None)
            service._finalize_autonomous_run_commitments = Mock(
                side_effect=lambda **kwargs: kwargs["run"]
            )

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-16T09:45:00+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=True,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["speech_payload"]["speech_text"], "投稿が完了しました。")
            self.assertEqual(call_order, ["review", "delivery"])
            review_context = review.call_args.kwargs["review_context"]
            self.assertEqual(
                review_context["run"]["observed_result_summaries"][0]["tool_name"],
                "create_post",
            )
            self.assertFalse(
                review_context["run"]["observed_result_summaries"][0]["is_error"]
            )
            self.assertEqual(
                review_context["candidate"]["speech_text"],
                "投稿が完了しました。",
            )

    def test_second_completion_rejection_cancels_without_speech_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["objective_summary"] = "ELYTHに投稿を1件作成する。"
            service.store.upsert_autonomous_run(autonomous_run=run)
            context = SimpleNamespace(
                run=service._autonomous_run_prompt_summary(run),
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            incomplete_step = {
                "action": {
                    "kind": "speech",
                    "capability_request": None,
                    "speech": {
                        "reason_code": "announce_post",
                        "reason_summary": "これから投稿することを伝える。",
                    },
                },
                "transition": {"kind": "complete", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "投稿準備を終えた。",
                    "history_summary": "投稿はまだ実行していない。",
                },
            }
            service.llm = SimpleNamespace(
                generate_autonomous_step=Mock(return_value=incomplete_step),
                generate_autonomous_completion_review=Mock(
                    return_value={
                        "outcome": "continue_run",
                        "reason_summary": "投稿作用がまだ実行されていない。",
                    }
                ),
            )
            service._generate_autonomous_run_speech = Mock(
                side_effect=[
                    {"speech_text": "投稿してみるとします。"},
                    {"speech_text": "これから投稿へ移ります。"},
                ]
            )
            service._emit_autonomous_run_assistant_message_event = Mock()
            service._persist_autonomous_run_speech_event = Mock()
            service._finalize_autonomous_run_commitments = Mock(
                side_effect=lambda **kwargs: kwargs["run"]
            )

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-16T09:45:30+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=True,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "cancelled")
            self.assertIn("2回続けて", result["error"])
            service._emit_autonomous_run_assistant_message_event.assert_not_called()
            service._persist_autonomous_run_speech_event.assert_not_called()

    def test_links_autonomous_run_to_commitment_created_by_source_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            memory_set_id = state["selected_memory_set_id"]
            run = self._commitment_run_record(memory_set_id=memory_set_id)
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:linked",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service._link_autonomous_run_source_commitments(
                state=state,
                pipeline={
                    "decision": {"kind": "autonomous_run"},
                    "autonomous_run_summary": {"run_id": run["run_id"]},
                },
                memory_trace={
                    "turn_consolidation_status": "succeeded",
                    "updated_memory_unit_ids": [commitment["memory_unit_id"]],
                },
                current_time="2026-06-20T11:00:10+09:00",
            )

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertEqual(updated["source_commitment_memory_unit_ids"], [commitment["memory_unit_id"]])

    def test_terminal_run_resolves_commitment_after_late_source_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            memory_set_id = state["selected_memory_set_id"]
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:late_link",
            )
            run = self._commitment_run_record(memory_set_id=memory_set_id, status="completed")
            run["commitment_resolution"] = {
                "result_status": "skipped",
                "reason": "no_linked_commitments",
                "terminal_status": "completed",
            }
            service.store.upsert_autonomous_run(autonomous_run=run)
            service.memory.vector_indexer.sync = lambda **_: None

            service._link_autonomous_run_source_commitments(
                state=state,
                pipeline={
                    "decision": {"kind": "autonomous_run"},
                    "autonomous_run_summary": {"run_id": run["run_id"]},
                },
                memory_trace={
                    "turn_consolidation_status": "succeeded",
                    "updated_memory_unit_ids": [commitment["memory_unit_id"]],
                },
                current_time="2026-06-20T11:03:00+09:00",
            )

            updated_commitment = service.store.list_memory_units_by_id(
                memory_set_id=memory_set_id,
                memory_unit_ids=[commitment["memory_unit_id"]],
            )[0]
            updated_run = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertEqual(updated_commitment["commitment_state"], "done")
            self.assertEqual(updated_run["commitment_resolution"]["result_status"], "updated")

    def test_completed_autonomous_run_marks_linked_commitment_done(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            memory_set_id = state["selected_memory_set_id"]
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:done",
            )
            run = self._commitment_run_record(
                memory_set_id=memory_set_id,
                status="completed",
                source_commitment_memory_unit_ids=[commitment["memory_unit_id"]],
            )
            service.store.upsert_autonomous_run(autonomous_run=run)
            service.memory.vector_indexer.sync = lambda **_: None

            updated_run = service._finalize_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status="completed",
                current_time="2026-06-20T11:03:00+09:00",
                evidence_events=[],
            )

            updated_commitment = service.store.list_memory_units_by_id(
                memory_set_id=memory_set_id,
                memory_unit_ids=[commitment["memory_unit_id"]],
            )[0]
            active_commitments = service.store.list_memory_units_for_recall(
                memory_set_id=memory_set_id,
                current_time="2026-06-20T11:04:00+09:00",
                include_memory_types=["commitment"],
                statuses=["inferred", "confirmed"],
                commitment_states=["open", "waiting_confirmation", "on_hold"],
                limit=20,
            )

            self.assertEqual(updated_commitment["commitment_state"], "done")
            self.assertEqual(updated_run["commitment_resolution"]["result_status"], "updated")
            self.assertNotIn(
                commitment["memory_unit_id"],
                [unit["memory_unit_id"] for unit in active_commitments],
            )

    def test_cancelled_autonomous_run_marks_linked_commitment_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            memory_set_id = state["selected_memory_set_id"]
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:cancelled",
            )
            run = self._commitment_run_record(
                memory_set_id=memory_set_id,
                status="cancelled",
                source_commitment_memory_unit_ids=[commitment["memory_unit_id"]],
            )
            service.store.upsert_autonomous_run(autonomous_run=run)
            service.memory.vector_indexer.sync = lambda **_: None

            service._finalize_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status="cancelled",
                current_time="2026-06-20T11:03:00+09:00",
                evidence_events=[],
            )

            updated_commitment = service.store.list_memory_units_by_id(
                memory_set_id=memory_set_id,
                memory_unit_ids=[commitment["memory_unit_id"]],
            )[0]
            self.assertEqual(updated_commitment["commitment_state"], "cancelled")

    def test_timeout_returns_waiting_result_run_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="waiting_result",
                waiting_request_id="vision_capture_request:timeout",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service._pending_capability_requests["vision_capture_request:timeout"] = {
                "request_record": self._request_record(run),
            }
            service._prune_pending_capability_requests(current_time="2026-06-20T12:00:00+09:00")

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "active")
            self.assertIsNone(updated["waiting_request_id"])
            self.assertEqual(updated["next_run_at"], "2026-06-20T12:00:00+09:00")
            self.assertEqual(updated["last_result_context"]["source_capability_id"], "vision.capture")
            self.assertEqual(updated["last_result_context"]["observation_summary"]["error_kind"], "request_timeout")
            self.assertIn("timeout", updated["history_summary"])

    def test_timeout_preserves_paused_run_and_sets_resume_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="paused",
                waiting_request_id="vision_capture_request:paused_timeout",
                pause_reason="manual_pause",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service._pending_capability_requests["vision_capture_request:paused_timeout"] = {
                "request_record": self._request_record(run),
            }
            service._prune_pending_capability_requests(current_time="2026-06-20T12:00:00+09:00")

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "paused")
            self.assertEqual(updated["pause_reason"], "manual_pause")
            self.assertEqual(updated["resume_status"], "active")
            self.assertIsNone(updated["waiting_request_id"])
            self.assertEqual(updated["last_result_context"]["observation_summary"]["error_kind"], "request_timeout")

    def test_startup_recovery_returns_orphaned_waiting_result_run_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="waiting_result",
                waiting_request_id="vision_capture_request:orphan",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service.recover_autonomous_run_runtime_state_after_startup()

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "active")
            self.assertIsNone(updated["waiting_request_id"])
            self.assertEqual(updated["last_result_context"]["source_capability_id"], "vision.capture")
            self.assertEqual(updated["last_result_context"]["observation_summary"]["error_kind"], "orphaned_after_startup")

    def test_recovery_ignores_terminal_and_request_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            terminal = self._run_record(
                run_id="autonomous_run:terminal",
                status="cancelled",
                waiting_request_id="vision_capture_request:terminal",
            )
            mismatched = self._run_record(
                run_id="autonomous_run:mismatched",
                status="waiting_result",
                waiting_request_id="vision_capture_request:expected",
            )
            service.store.upsert_autonomous_run(autonomous_run=terminal)
            service.store.upsert_autonomous_run(autonomous_run=mismatched)

            terminal_result = service._mark_autonomous_run_capability_wait_interrupted(
                request_record=self._request_record(terminal),
                current_time="2026-06-20T12:00:00+09:00",
                reason_code="request_timeout",
                reason_summary="request timeout",
            )
            mismatch_record = self._request_record(mismatched)
            mismatch_record["request_id"] = "vision_capture_request:actual"
            mismatch_result = service._mark_autonomous_run_capability_wait_interrupted(
                request_record=mismatch_record,
                current_time="2026-06-20T12:00:00+09:00",
                reason_code="request_timeout",
                reason_summary="request timeout",
            )

            self.assertIsNone(terminal_result)
            self.assertIsNone(mismatch_result)
            self.assertEqual(
                service.store.get_autonomous_run(run_id=terminal["run_id"])["status"],
                "cancelled",
            )
            self.assertEqual(
                service.store.get_autonomous_run(run_id=mismatched["run_id"])["waiting_request_id"],
                "vision_capture_request:expected",
            )

    def test_capability_result_is_persisted_and_last_result_survives_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["source_cycle_id"] = "cycle:source"
            run["origin_interaction_ref"] = "interaction:test"
            run["participant_refs"] = ["person:test"]
            run["last_result_context"] = {
                "source_capability_id": "mcp.call_tool",
                "observation_summary": {"mcp_result_summary": "返信を投稿した。"},
            }
            service.store.upsert_autonomous_run(autonomous_run=run)

            event = service._persist_autonomous_capability_result_event(
                run=run,
                capability_id="mcp.call_tool",
                observation_summary={
                    "mcp_server_id": "elyth",
                    "tool_name": "get_notifications",
                    "mcp_result_summary": '{"data":{"items":[]}}',
                    "observed_persons": [
                        {
                            "person_ref": "person:mcp:elyth:rin_ichinose",
                            "display_name": "一ノ瀬 凜",
                        }
                    ],
                },
                input_text="MCP tool は elyth/get_notifications。",
                created_at="2026-08-13T20:52:46+09:00",
            )
            self.assertEqual(event["kind"], "capability_result")
            self.assertEqual(event["tool_name"], "get_notifications")
            self.assertEqual(event["observed_person_refs"], ["person:mcp:elyth:rin_ichinose"])

            updated = service._apply_autonomous_step_transition(
                run={
                    **run,
                    "last_result_context": {"source_capability_id": "mcp.call_tool"},
                },
                step={
                    "action": {
                        "kind": "speech",
                        "capability_request": None,
                        "speech": {
                            "reason_code": "done",
                            "reason_summary": "対応した。",
                        },
                    },
                    "transition": {"kind": "complete", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "既読処理を完了した。",
                        "history_summary": "既読処理を実行し完了しました。",
                    },
                },
                action_kind="speech",
                current_time="2026-08-13T20:53:14+09:00",
                capability_request_summary=None,
            )
            self.assertEqual(updated["status"], "completed")
            self.assertEqual(updated["last_result_context"]["source_capability_id"], "mcp.call_tool")

    def test_observed_result_summary_keeps_completion_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            summaries = service._append_autonomous_observed_result_summaries(
                run={"observed_result_summaries": []},
                capability_id="mcp.call_tool",
                observation_summary={
                    "tool_name": "create_post",
                    "status": "completed",
                    "is_error": False,
                    "mcp_result_summary": "投稿を作成した。",
                },
                result_payload={"status": "completed", "is_error": False},
                created_at="2026-08-16T09:45:00+09:00",
            )

            self.assertEqual(
                summaries,
                [
                    {
                        "capability_id": "mcp.call_tool",
                        "tool_name": "create_post",
                        "result_status": "completed",
                        "is_error": False,
                        "summary_text": "投稿を作成した。",
                        "created_at": "2026-08-16T09:45:00+09:00",
                    }
                ],
            )

    def test_twentieth_continue_step_starts_five_minute_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="active",
                waiting_request_id=None,
            )
            run["consecutive_step_count"] = 19

            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {"kind": "none", "capability_request": None, "speech": None},
                    "transition": {"kind": "continue", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "確認を続ける。",
                        "history_summary": "確認を続けた。",
                    },
                },
                action_kind="none",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary=None,
            )

            self.assertEqual(updated["status"], "waiting_timer")
            self.assertEqual(updated["consecutive_step_count"], 0)
            self.assertEqual(updated["cooldown_until"], "2026-06-20T12:05:00+09:00")
            self.assertEqual(updated["next_run_at"], updated["cooldown_until"])
            public_summary = service._autonomous_run_public_summary(
                updated,
                current_time="2026-06-20T12:00:00+09:00",
            )
            self.assertEqual(public_summary["consecutive_step_count"], 0)
            self.assertEqual(public_summary["cooldown_until"], updated["cooldown_until"])

    def test_nineteenth_continue_step_keeps_normal_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(status="active", waiting_request_id=None)
            run["consecutive_step_count"] = 18

            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {"kind": "none", "capability_request": None, "speech": None},
                    "transition": {"kind": "continue", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "確認を続ける。",
                        "history_summary": "19回目の確認を続けた。",
                    },
                },
                action_kind="none",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary=None,
            )

            self.assertEqual(updated["consecutive_step_count"], 19)
            self.assertIsNone(updated["cooldown_until"])
            self.assertEqual(updated["next_run_at"], "2026-06-20T12:00:05+09:00")

    def test_explicit_wait_resets_continuous_step_count_without_forced_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="active",
                waiting_request_id=None,
            )
            run["consecutive_step_count"] = 19

            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {"kind": "none", "capability_request": None, "speech": None},
                    "transition": {
                        "kind": "wait_until",
                        "next_run_at": "2026-06-20T12:01:00+09:00",
                    },
                    "run_update": {
                        "current_step_summary": "1分待つ。",
                        "history_summary": "次の確認まで待つ。",
                    },
                },
                action_kind="none",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary=None,
            )

            self.assertEqual(updated["consecutive_step_count"], 0)
            self.assertIsNone(updated["cooldown_until"])
            self.assertEqual(updated["next_run_at"], "2026-06-20T12:01:00+09:00")

    def test_twentieth_capability_step_waits_only_for_remaining_cooldown_after_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="active",
                waiting_request_id=None,
            )
            run["consecutive_step_count"] = 19
            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {
                        "kind": "capability_request",
                        "capability_request": {
                            "capability_id": "vision.capture",
                            "input": {"vision_source_id": "vision_source:main", "mode": "still"},
                        },
                        "speech": None,
                    },
                    "transition": {"kind": "continue", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "結果を待つ。",
                        "history_summary": "20回目の観測を依頼した。",
                    },
                },
                action_kind="capability_request",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary={"request_id": "vision_capture_request:20"},
            )

            self.assertEqual(updated["status"], "waiting_result")
            self.assertEqual(updated["cooldown_until"], "2026-06-20T12:05:00+09:00")
            self.assertEqual(
                service._autonomous_run_after_result_schedule(
                    run=updated,
                    current_time="2026-06-20T12:02:00+09:00",
                ),
                ("waiting_timer", "2026-06-20T12:05:00+09:00", "2026-06-20T12:05:00+09:00"),
            )
            self.assertEqual(
                service._autonomous_run_after_result_schedule(
                    run=updated,
                    current_time="2026-06-20T12:06:00+09:00",
                ),
                ("active", "2026-06-20T12:06:00+09:00", None),
            )

    def test_self_initiated_step_drops_visual_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["origin_kind"] = "background_thinking"
            run["source_current_input"] = {
                "sender_kind": "system",
                "sender_ref": None,
                "source_kind": "background_thinking",
                "response_target_refs": [],
                "text": "自己評価。しばらく関わっていない気にかけていることがある。",
            }
            service._list_current_world_states = Mock(
                return_value=[
                    {"state_type": "visual_context", "summary_text": "リズムゲームのS評価"},
                    {"state_type": "external_service", "summary_text": "ELYTHの通知は空"},
                ]
            )
            service._summarize_foreground_world_states = Mock(
                side_effect=lambda states, current_time: states
            )
            service._build_capability_decision_view = Mock(return_value=[])
            service._build_agent_skill_context = Mock(return_value=None)
            service._build_time_context = Mock(return_value={})
            service._load_recent_turns = Mock(return_value=[])
            service._autonomous_run_activity_context = Mock(return_value=None)
            service._summarize_ongoing_action = Mock(return_value=None)
            service._current_ongoing_action = Mock(return_value=None)
            service._build_people_context = Mock(return_value=[])
            service._autonomous_run_prompt_summary = Mock(return_value={"objective_summary": "向きへ関わる。"})

            context = service._build_autonomous_step_context(
                state=state,
                run=run,
                current_time="2026-08-16T19:33:00+09:00",
                source_current_input=run["source_current_input"],
                last_result_context=None,
            )

            self.assertEqual(
                context.foreground_world_state,
                [{"state_type": "external_service", "summary_text": "ELYTHの通知は空"}],
            )

    def test_people_context_includes_observed_mcp_persons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()

            people = service._build_people_context(
                state=state,
                current_input=CurrentInput(
                    sender_kind="system",
                    sender_ref=None,
                    source_kind="autonomous_run",
                    response_target_refs=(),
                    interaction_context=None,
                    text="通知を確認した。",
                ),
                structured_sources=[
                    {
                        "observed_persons": [
                            {
                                "person_ref": "person:mcp:elyth:rin_ichinose",
                                "display_name": "一ノ瀬 凜",
                            }
                        ],
                        "observed_person_refs": ["person:mcp:elyth:rin_ichinose"],
                    }
                ],
            )

            self.assertEqual(
                people,
                [
                    {
                        "person_ref": "person:mcp:elyth:rin_ichinose",
                        "display_name": "一ノ瀬 凜",
                    }
                ],
            )

    def test_terminal_consolidation_records_observed_person_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["source_cycle_id"] = "cycle:source"
            run["source_current_input"] = {
                "sender_kind": "person",
                "sender_ref": "person:test",
                "source_kind": "user_message",
                "response_target_refs": ["person:test"],
                "interaction_context": {
                    "interaction_ref": "interaction:test",
                    "speaker_ref": "person:test",
                    "participants": [{"person_ref": "person:test", "display_name": "テスト人物"}],
                },
                "text": "対応しておいて",
            }
            run["participant_refs"] = ["person:test"]
            run["origin_interaction_ref"] = "interaction:test"
            run["observed_persons"] = [
                {
                    "person_ref": "person:mcp:elyth:rin_ichinose",
                    "display_name": "一ノ瀬 凜",
                }
            ]
            run["observed_result_summaries"] = [
                {
                    "capability_id": "mcp.call_tool",
                    "tool_name": "create_reply",
                    "summary_text": "一ノ瀬 凜への返信を投稿した。",
                    "created_at": "2026-08-13T20:52:59+09:00",
                }
            ]
            run["result_events"] = []
            service.store.upsert_autonomous_run(autonomous_run=run)

            updated = service._finalize_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status="completed",
                current_time="2026-08-13T20:53:14+09:00",
                evidence_events=[
                    {
                        "event_id": "event:speech",
                        "cycle_id": "cycle:source",
                        "memory_set_id": state["selected_memory_set_id"],
                        "kind": "speech",
                        "role": "assistant",
                        "text": "一ノ瀬 凜さんへ返信しました。",
                        "created_at": "2026-08-13T20:53:14+09:00",
                    }
                ],
            )

            consolidation = updated["terminal_consolidation"]
            self.assertEqual(consolidation["result_status"], "succeeded")
            self.assertTrue(str(consolidation["episode_id"] or "").startswith("episode:"))

    def _run_record(
        self,
        *,
        status: str,
        waiting_request_id: str | None,
        run_id: str = "autonomous_run:test",
        pause_reason: str | None = None,
    ) -> dict:
        return {
            "run_id": run_id,
            "memory_set_id": "memory_set:default",
            "status": status,
            "objective_summary": "視覚確認を続ける。",
            "origin_kind": "user_message",
            "current_step_summary": "vision.capture の結果を待つ。",
            "history_summary": "action=capability_request transition=continue",
            "next_run_at": None,
            "waiting_request_id": waiting_request_id,
            "pause_reason": pause_reason,
            "resume_status": "waiting_result" if status == "paused" else None,
            "consecutive_step_count": 0,
            "cooldown_until": None,
            "created_at": "2026-06-20T11:00:00+09:00",
            "updated_at": "2026-06-20T11:00:00+09:00",
            "completed_at": "2026-06-20T11:30:00+09:00" if status == "cancelled" else None,
            "last_step": {
                "action": {
                    "kind": "capability_request",
                    "capability_request": {
                        "capability_id": "vision.capture",
                        "input": {
                            "vision_source_id": "vision_source:main",
                            "mode": "still",
                        },
                    },
                    "speech": None,
                },
                "transition": {
                    "kind": "continue",
                    "next_run_at": None,
                },
                "run_update": {
                    "current_step_summary": "vision.capture の結果を待つ。",
                    "history_summary": "action=capability_request:vision.capture transition=continue",
                },
            },
        }

    def _request_record(self, run: dict) -> dict:
        return {
            "request_id": run["waiting_request_id"],
            "target_client_id": "client:vision",
            "memory_set_id": run["memory_set_id"],
            "capability_id": "vision.capture",
            "input": {
                "vision_source_id": "vision_source:main",
                "mode": "still",
            },
            "timeout_ms": 1000,
            "created_at": "2026-06-20T11:00:00+09:00",
            "expires_at": "2026-06-20T11:00:01+09:00",
            "autonomous_run_id": run["run_id"],
            "vision_source_id": "vision_source:main",
            "source_kind": "camera",
            "source_owner": "self",
            "source_label": "main",
        }

    def _commitment_run_record(
        self,
        *,
        memory_set_id: str,
        status: str = "active",
        source_commitment_memory_unit_ids: list[str] | None = None,
    ) -> dict:
        return {
            "run_id": "autonomous_run:commitment",
            "memory_set_id": memory_set_id,
            "status": status,
            "objective_summary": "3分後にユーザーへ声をかける",
            "origin_kind": "user_message",
            "current_step_summary": "予定時刻にユーザーへ声をかける。",
            "history_summary": "3分後のリマインド依頼を受諾した。",
            "next_run_at": None,
            "waiting_request_id": None,
            "pause_reason": None,
            "resume_status": None,
            "consecutive_step_count": 0,
            "cooldown_until": None,
            "created_at": "2026-06-20T11:00:00+09:00",
            "updated_at": "2026-06-20T11:00:00+09:00",
            "completed_at": "2026-06-20T11:03:00+09:00" if status in {"completed", "cancelled"} else None,
            "source_cycle_id": "cycle:source",
            "source_current_input": {
                "sender_kind": "person",
                "sender_ref": "person:test",
                "source_kind": "user_message",
                "response_target_refs": ["person:test"],
                "interaction_context": {
                    "interaction_ref": "interaction:test",
                    "speaker_ref": "person:test",
                    "participants": [{"person_ref": "person:test", "display_name": "テスト人物"}],
                },
                "text": "3分後に声をかけて",
            },
            "source_commitment_memory_unit_ids": source_commitment_memory_unit_ids or [],
        }

    def _persist_commitment(
        self,
        *,
        service: OtomeKairoService,
        memory_set_id: str,
        memory_unit_id: str,
    ) -> dict:
        unit = {
            "memory_unit_id": memory_unit_id,
            "memory_set_id": memory_set_id,
            "memory_type": "commitment",
            "scope_type": "self",
            "scope_key": "self",
            "subject_ref": "self",
            "predicate": "notify",
            "object_ref_or_value": "person:test",
            "summary_text": "3分後にユーザーへ声をかけること。",
            "status": "inferred",
            "commitment_state": "open",
            "confidence": 0.58,
            "salience": 0.36,
            "formed_at": "2026-06-20T11:00:00+09:00",
            "last_confirmed_at": None,
            "valid_from": None,
            "valid_to": "2026-06-21T03:00:00+09:00",
            "evidence_event_ids": [],
            "evidence_cycle_ids": ["cycle:source"],
            "qualifiers": {
                "source": "assistant_response",
                "commitment_actor": "self",
                "scope_duration": "session",
            },
        }
        action = service.memory.action_resolver.build_memory_action(
            operation="new",
            memory_set_id=memory_set_id,
            finished_at="2026-06-20T11:00:00+09:00",
            memory_unit=unit,
            related_memory_unit_ids=[],
            before_snapshot=None,
            after_snapshot=unit,
            reason="test commitment",
            event_ids=[],
        )
        service.store.persist_turn_consolidation(
            episode=None,
            memory_actions=[action],
            episode_affects=[],
        )
        return unit


if __name__ == "__main__":
    unittest.main()
