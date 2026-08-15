import tempfile
import unittest
from pathlib import Path

from otomekairo.service.app import OtomeKairoService


class PendingIntentCooldownTests(unittest.TestCase):
    def _decision(self) -> dict:
        return {
            "kind": "pending_intent",
            "reason_summary": "今は外へ出さず次の判断で見直す。",
            "pending_intent": {
                "intent_kind": "followup",
                "intent_summary": "状況をもう一度確認する。",
                "dedupe_key": "pending_intent:test:followup",
            },
        }

    def test_new_candidate_is_immediately_eligible_for_the_next_wake(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            summary = service._apply_pending_intent_candidate(
                cycle_id="cycle:create",
                memory_set_id="memory_set:default",
                decision=self._decision(),
                occurred_at="2026-08-15T12:00:00+09:00",
            )

            self.assertIsNotNone(summary)
            assert summary is not None
            self.assertEqual(summary["not_before"], "2026-08-15T12:00:00+09:00")
            self.assertEqual(summary["consecutive_evaluation_count"], 0)
            self.assertIsNone(summary["cooldown_until"])

            inspection = service._list_pending_intent_candidates_for_inspection(
                state=service.store.read_state(),
                current_time="2026-08-15T12:00:00+09:00",
                limit=8,
            )[0]
            self.assertEqual(inspection["consecutive_evaluation_count"], 0)
            self.assertIsNone(inspection["cooldown_until"])

    def test_selected_noop_is_counted_by_wake_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            summary = service._apply_pending_intent_candidate(
                cycle_id="cycle:create",
                memory_set_id="memory_set:default",
                decision=self._decision(),
                occurred_at="2026-08-15T12:00:00+09:00",
            )
            assert summary is not None
            selected = service._pending_intent_candidate_pool(
                memory_set_id="memory_set:default",
                current_time="2026-08-15T12:00:00+09:00",
            )[0]

            service._record_wake_outcome(
                current_time="2026-08-15T12:01:00+09:00",
                decision={"kind": "noop"},
                selected_candidate=selected,
            )

            candidate = service._pending_intent_candidate_pool(
                memory_set_id="memory_set:default",
                current_time="2026-08-15T12:01:00+09:00",
            )[0]
            self.assertEqual(candidate["consecutive_evaluation_count"], 1)

    def test_twentieth_selected_evaluation_starts_five_minute_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            summary = service._apply_pending_intent_candidate(
                cycle_id="cycle:create",
                memory_set_id="memory_set:default",
                decision=self._decision(),
                occurred_at="2026-08-15T12:00:00+09:00",
            )
            assert summary is not None
            candidate_id = summary["candidate_id"]

            for _ in range(19):
                state = service._record_pending_intent_candidate_evaluation(
                    candidate_id=candidate_id,
                    current_time="2026-08-15T12:01:00+09:00",
                )
            assert state is not None
            self.assertEqual(state["consecutive_evaluation_count"], 19)
            self.assertIsNone(state["cooldown_until"])

            state = service._record_pending_intent_candidate_evaluation(
                candidate_id=candidate_id,
                current_time="2026-08-15T12:02:00+09:00",
            )
            assert state is not None
            self.assertEqual(state["consecutive_evaluation_count"], 0)
            self.assertEqual(state["cooldown_until"], "2026-08-15T12:07:00+09:00")

            candidate = service._pending_intent_candidate_pool(
                memory_set_id="memory_set:default",
                current_time="2026-08-15T12:03:00+09:00",
            )[0]
            self.assertEqual(candidate["not_before"], "2026-08-15T12:07:00+09:00")

    def test_same_candidate_update_preserves_active_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            summary = service._apply_pending_intent_candidate(
                cycle_id="cycle:create",
                memory_set_id="memory_set:default",
                decision=self._decision(),
                occurred_at="2026-08-15T12:00:00+09:00",
            )
            assert summary is not None
            for _ in range(20):
                service._record_pending_intent_candidate_evaluation(
                    candidate_id=summary["candidate_id"],
                    current_time="2026-08-15T12:01:00+09:00",
                )

            updated = service._apply_pending_intent_candidate(
                cycle_id="cycle:update",
                memory_set_id="memory_set:default",
                decision=self._decision(),
                occurred_at="2026-08-15T12:02:00+09:00",
            )
            assert updated is not None
            self.assertEqual(updated["queue_action"], "updated")
            self.assertEqual(updated["not_before"], "2026-08-15T12:06:00+09:00")
            self.assertEqual(updated["cooldown_until"], "2026-08-15T12:06:00+09:00")


if __name__ == "__main__":
    unittest.main()
