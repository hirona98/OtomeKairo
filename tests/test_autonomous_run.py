import tempfile
import unittest
from pathlib import Path

from otomekairo.service.app import OtomeKairoService


class AutonomousRunRecoveryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
