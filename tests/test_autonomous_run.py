import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from otomekairo.service.app import OtomeKairoService
from otomekairo.service.autonomous_run import AUTONOMOUS_PRE_SEND_CHECK_RETRY_FEEDBACK
from otomekairo.service.capability import PreSendCheckWithheldError


class AutonomousRunRecoveryTests(unittest.TestCase):
    def test_operational_session_consumes_budget_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._operational_run_record(
                memory_set_id=state["selected_memory_set_id"],
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service._consume_autonomous_operational_session_budget(
                run=run,
                capability_id="mcp.call_tool",
                input_payload={
                    "mcp_server_id": "elyth",
                    "tool_name": "create_post",
                    "arguments": {"content": "candidate"},
                },
                action={
                    "operational_skill": {
                        "bundle_id": "elyth-remote-mcp-skills@0.1.0",
                        "skill_id": "elyth-post",
                    }
                },
                current_time="2026-08-11T12:00:01+09:00",
            )

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertEqual(updated["operational_skill"]["tool_call_count"], 1)
            self.assertEqual(updated["operational_skill"]["mutating_call_count"], 1)
            self.assertEqual(updated["updated_at"], "2026-08-11T12:00:01+09:00")

    def test_operational_session_rejects_mutation_after_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._operational_run_record(
                memory_set_id=state["selected_memory_set_id"],
                tool_call_count=3,
                mutating_call_count=3,
            )

            with self.assertRaises(ValueError):
                service._consume_autonomous_operational_session_budget(
                    run=run,
                    capability_id="mcp.call_tool",
                    input_payload={
                        "mcp_server_id": "elyth",
                        "tool_name": "create_post",
                        "arguments": {"content": "candidate"},
                    },
                    action={
                        "operational_skill": {
                            "bundle_id": "elyth-remote-mcp-skills@0.1.0",
                            "skill_id": "elyth-post",
                        }
                    },
                    current_time="2026-08-11T12:00:01+09:00",
                )

    def test_operational_session_cooldown_blocks_recent_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            service._now_iso = lambda: "2026-08-11T12:00:00+09:00"
            run = self._operational_run_record(
                memory_set_id=state["selected_memory_set_id"],
                status="completed",
                created_at="2026-08-11T11:30:00+09:00",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            eligible = service._operational_session_eligible(
                bundle_id="elyth-remote-mcp-skills@0.1.0",
                policy={"enabled": True, "min_interval_seconds": 3600},
            )

            self.assertFalse(eligible)

    def test_operational_session_completes_without_llm_after_tool_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._operational_run_record(
                memory_set_id=state["selected_memory_set_id"],
                tool_call_count=10,
                mutating_call_count=3,
            )
            service.store.upsert_autonomous_run(autonomous_run=run)
            service.llm = Mock()

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-11T12:00:01+09:00",
                emit_speech_event=False,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["autonomous_run"]["status"], "completed")
            self.assertIn("tool call 上限", result["autonomous_run"]["history_summary"])
            service.llm.generate_autonomous_step.assert_not_called()

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
            state = service.store.read_state()
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
            state = service.store.read_state()
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
            state = service.store.read_state()
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

    def _run_record(
        self,
        *,
        status: str,
        waiting_request_id: str,
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

    def _operational_run_record(
        self,
        *,
        memory_set_id: str,
        status: str = "active",
        created_at: str = "2026-08-11T12:00:00+09:00",
        tool_call_count: int = 0,
        mutating_call_count: int = 0,
    ) -> dict:
        run = self._commitment_run_record(memory_set_id=memory_set_id, status=status)
        run["run_id"] = "autonomous_run:elyth-session"
        run["created_at"] = created_at
        run["updated_at"] = created_at
        run["operational_skill"] = {
            "mcp_server_id": "elyth",
            "bundle_id": "elyth-remote-mcp-skills@0.1.0",
            "skill_id": "elyth-run-session",
            "reason_summary": "ELYTH 内の活動を確認する。",
            "host_policy": {
                "enabled": True,
                "entry_skill": "elyth-run-session",
                "min_interval_seconds": 3600,
                "max_tool_calls": 10,
                "max_mutating_calls": 3,
            },
            "tool_call_count": tool_call_count,
            "mutating_call_count": mutating_call_count,
        }
        return run

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
