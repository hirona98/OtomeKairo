from __future__ import annotations

import threading
import unittest
from datetime import datetime

from otomekairo.llm.contexts import CurrentInput, InitiativeCandidateFamily, InitiativeContext
from otomekairo.service.input.mixin import ServiceInputMixin
from otomekairo.service.spontaneous.wake import ServiceSpontaneousWakeMixin


class DummyWakeService(ServiceSpontaneousWakeMixin):
    def __init__(self) -> None:
        self._runtime_state_lock = threading.RLock()
        self._wake_runtime_state = {
            "last_wake_at": None,
            "last_spontaneous_at": None,
            "initial_delay_until": None,
            "retry_after": None,
            "speech_history_by_dedupe": {},
        }

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class DummyInputService(ServiceInputMixin, ServiceSpontaneousWakeMixin):
    pass


class WakeInterventionLoadTests(unittest.TestCase):
    def test_recent_spontaneous_speech_does_not_skip_wake_by_time(self) -> None:
        service = DummyWakeService()
        service._wake_runtime_state["last_spontaneous_at"] = "2026-06-21T15:52:00+09:00"
        state = {"wake_policy": {"mode": "interval", "interval_seconds": 60}}

        due = service._wake_is_due(
            state=state,
            current_time="2026-06-21T15:53:00+09:00",
        )

        self.assertFalse(due["should_skip"])

    def test_visual_repetition_sets_high_suppression_without_speech_ready_drive(self) -> None:
        service = DummyInputService()
        summary = service._initiative_suppression_summary(
            drive_summaries=[],
            foreground_signal_summary={
                "visual_observations": [
                    {"change_state": "stable"},
                    {"change_state": "same_as_recent_speech", "same_as_recent_speech": True},
                ]
            },
            intervention_state={"background_trigger": True},
            intervention_risk_summary=None,
        )

        self.assertEqual(summary["suppression_level"], "high")
        self.assertTrue(summary["visual_repetition_present"])
        self.assertTrue(summary["same_as_recent_speech_present"])
        self.assertTrue(summary["all_visual_observations_repeated"])
        self.assertEqual(summary["visual_observation_count"], 2)
        self.assertEqual(summary["repeated_visual_observation_count"], 2)

    def test_changed_visual_observation_does_not_set_high_repetition_suppression(self) -> None:
        service = DummyInputService()
        summary = service._initiative_suppression_summary(
            drive_summaries=[],
            foreground_signal_summary={
                "visual_observations": [
                    {"change_state": "stable"},
                    {"change_state": "changed"},
                ]
            },
            intervention_state={"background_trigger": True},
            intervention_risk_summary=None,
        )

        self.assertEqual(summary["suppression_level"], "low")
        self.assertTrue(summary["visual_repetition_present"])
        self.assertFalse(summary["all_visual_observations_repeated"])

    def test_workspace_context_includes_visual_repetition_suppression_candidate(self) -> None:
        service = DummyInputService()
        initiative_context = InitiativeContext(
            trigger_kind="background_wake",
            opportunity_summary="定期起床。",
            initiative_entry_summary=None,
            time_context_summary={},
            foreground_signal_summary={},
            activity_context=None,
            initiative_baseline={},
            persona_context_summary={},
            runtime_state_summary={},
            recent_turn_summary=[],
            drive_summaries=[],
            pending_intent_summaries=[],
            world_state_summary=[],
            ongoing_action_summary=None,
            capability_summary={},
            candidate_families=[
                InitiativeCandidateFamily(
                    family="autonomous",
                    available=True,
                    selected=True,
                    priority_score=1.0,
                    reason_summary="自律判断候補がある。",
                )
            ],
            selected_candidate_family="autonomous",
            intervention_state={"background_trigger": True},
            suppression_summary={
                "suppression_level": "high",
                "visual_repetition_present": True,
                "same_as_recent_speech_present": True,
                "all_visual_observations_repeated": True,
                "visual_observation_count": 2,
                "repeated_visual_observation_count": 2,
            },
            intervention_risk_summary="視覚観測が反復している。",
        )

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender="system",
                source_kind="background_wake",
                response_target="none",
                text="定期起床。",
            ),
            recall_pack={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            initiative_context=initiative_context,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
        )

        suppression_candidates = [
            candidate
            for candidate in payload["workspace_candidates"]
            if candidate["factor_ref"] == "suppression:visual_repetition"
        ]

        self.assertEqual(len(suppression_candidates), 1)
        self.assertEqual(suppression_candidates[0]["kind"], "suppression")
        self.assertTrue(suppression_candidates[0]["metadata"]["all_visual_observations_repeated"])


if __name__ == "__main__":
    unittest.main()
