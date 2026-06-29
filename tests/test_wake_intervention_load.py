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
    def _list_current_drive_states(self, *, state: dict, current_time: str) -> list[dict]:
        _ = state, current_time
        return []

    def _summarize_drive_states(self, drive_states: list[dict]) -> list[dict]:
        return drive_states

    def _current_ongoing_action(self, *, state: dict, current_time: str) -> dict | None:
        _ = state, current_time
        return None

    def _summarize_ongoing_action(self, ongoing_action: dict | None) -> dict | None:
        return ongoing_action


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

    def test_visual_repetition_sets_high_suppression_without_foreground_drive(self) -> None:
        service = DummyInputService()
        summary = service._initiative_suppression_summary(
            drive_summaries=[],
            foreground_signal_summary={
                "visual_observations": [
                    {"change_state": "stable"},
                    {"change_state": "same_as_recent_speech", "same_as_recent_speech": True},
                ]
            },
            speech_timing_state={"background_trigger": True},
            speech_timing_summary=None,
        )

        self.assertEqual(summary["suppression_level"], "high")
        self.assertTrue(summary["visual_repetition_present"])
        self.assertTrue(summary["same_as_recent_speech_present"])
        self.assertFalse(summary["all_visual_observations_repeated"])
        self.assertEqual(summary["visual_observation_count"], 2)
        self.assertEqual(summary["repeated_visual_observation_count"], 1)

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
            speech_timing_state={"background_trigger": True},
            speech_timing_summary=None,
        )

        self.assertEqual(summary["suppression_level"], "low")
        self.assertFalse(summary["visual_repetition_present"])
        self.assertFalse(summary["all_visual_observations_repeated"])

    def test_changed_visual_observation_makes_autonomous_family_available(self) -> None:
        service = DummyInputService()

        family = service._initiative_autonomous_family(
            trigger_kind="background_thinking",
            drive_summaries=[],
            world_state_summary=[],
            status_refresh_world_state_summary=[],
            recent_turn_summary=[],
            foreground_signal_summary={
                "visual_observations": [
                    {
                        "observation_id": "observation:desktop",
                        "change_state": "changed",
                        "reason_summary": "画面内容が変化している。",
                    }
                ]
            },
            initiative_entry_summary=None,
            suppression_summary={"suppression_level": "low"},
            initiative_baseline={},
            speech_timing_state={"background_trigger": True},
            capability_summary={},
        )

        self.assertTrue(family.available)
        self.assertIn("現在観測候補 1 件", family.reason_summary)
        self.assertIn("visual change_state=changed", family.reason_summary)

    def test_stable_visual_observation_makes_autonomous_family_available(self) -> None:
        service = DummyInputService()

        family = service._initiative_autonomous_family(
            trigger_kind="background_thinking",
            drive_summaries=[],
            world_state_summary=[],
            status_refresh_world_state_summary=[],
            recent_turn_summary=[],
            foreground_signal_summary={
                "visual_observations": [
                    {
                        "observation_id": "observation:desktop",
                        "change_state": "stable",
                        "reason_summary": "現在状態が続いている。",
                    }
                ]
            },
            initiative_entry_summary=None,
            suppression_summary={"suppression_level": "low"},
            initiative_baseline={},
            speech_timing_state={"background_trigger": True},
            capability_summary={},
        )

        self.assertTrue(family.available)
        self.assertIn("現在観測候補 1 件", family.reason_summary)
        self.assertIn("visual change_state=stable", family.reason_summary)

    def test_changed_visual_observation_enters_autonomous_context_without_entry_check(self) -> None:
        service = DummyInputService()
        client_context = {
            "visual_observation_signals": [
                {
                    "observation_id": "observation:desktop",
                    "change_state": "changed",
                    "change_basis": "semantic_change",
                    "reason_summary": "画面内容が変化している。",
                    "summary_text": "作業画面が別の内容に切り替わっている。",
                }
            ]
        }

        checked_context = service._run_autonomous_initiative_entry_check(
            state={},
            current_time="2026-06-22T22:30:00+09:00",
            trigger_kind="background_thinking",
            client_context=client_context,
            recent_turns=[],
            cycle_id=None,
        )

        self.assertIsNot(checked_context, client_context)
        self.assertNotIn("initiative_entry_check", checked_context)
        self.assertTrue(checked_context["autonomous_visual_observation_direct_entry"])
        self.assertTrue(
            service._has_autonomous_initiative_context(
                state={},
                current_time="2026-06-22T22:30:00+09:00",
                client_context=checked_context,
            )
        )

    def test_visual_observation_direct_entry_skips_recall_interpretation(self) -> None:
        service = DummyInputService()

        recall_inputs = service._build_pipeline_recall_inputs(
            state={},
            started_at="2026-06-22T22:30:00+09:00",
            input_text="定期思考。",
            current_input=CurrentInput(
                sender="system",
                source_kind="background_thinking",
                response_target="none",
                text="定期思考。",
            ),
            recent_turns=[],
            augmented_query_text="定期思考。",
            visual_observation_context=None,
            activity_context=None,
            recall_role={},
            persona_context=None,
            client_context={"autonomous_visual_observation_direct_entry": True},
            cycle_label="[test]",
        )

        self.assertEqual(recall_inputs["recall_hint"], service._empty_recall_hint())
        self.assertEqual(recall_inputs["answer_contract"]["contract"], "summary")
        self.assertEqual(recall_inputs["recall_pack"]["candidate_count"], 0)
        self.assertEqual(recall_inputs["evidence_pack"]["status"], "summary")

    def test_workspace_context_includes_visual_repetition_suppression_candidate(self) -> None:
        service = DummyInputService()
        initiative_context = InitiativeContext(
            trigger_kind="background_thinking",
            opportunity_summary="定期思考。",
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
            speech_timing_state={"background_trigger": True},
            suppression_summary={
                "suppression_level": "high",
                "visual_repetition_present": True,
                "same_as_recent_speech_present": True,
                "all_visual_observations_repeated": True,
                "visual_observation_count": 2,
                "repeated_visual_observation_count": 2,
            },
            speech_timing_summary="視覚観測が反復している。",
        )

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender="system",
                source_kind="background_thinking",
                response_target="none",
                text="定期思考。",
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

    def test_workspace_context_includes_changed_visual_observation_candidate(self) -> None:
        service = DummyInputService()
        initiative_context = InitiativeContext(
            trigger_kind="background_thinking",
            opportunity_summary="定期思考。",
            initiative_entry_summary=None,
            time_context_summary={},
            foreground_signal_summary={
                "visual_observations": [
                    {
                        "observation_id": "observation:desktop",
                        "change_state": "changed",
                        "change_basis": "semantic_change",
                        "reason_summary": "画面内容が変化している。",
                        "summary_text": "作業画面が別の内容に切り替わっている。",
                        "source_kind": "desktop",
                        "source_owner": "user_environment",
                    }
                ]
            },
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
            candidate_families=[],
            selected_candidate_family=None,
            speech_timing_state={"background_trigger": True},
            suppression_summary={
                "suppression_level": "low",
                "visual_repetition_present": False,
            },
            speech_timing_summary=None,
        )

        payload = service._build_workspace_context(
            current_input=CurrentInput(
                sender="system",
                source_kind="background_thinking",
                response_target="none",
                text="定期思考。",
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

        visual_candidates = [
            candidate
            for candidate in payload["workspace_candidates"]
            if candidate["factor_ref"] == "visual_observation_signal:observation:desktop"
        ]

        self.assertEqual(len(visual_candidates), 1)
        self.assertEqual(visual_candidates[0]["kind"], "visual_observation")
        self.assertEqual(visual_candidates[0]["metadata"]["change_state"], "changed")
        self.assertEqual(payload["workspace_candidates"][0]["factor_ref"], "visual_observation_signal:observation:desktop")

    def test_initiative_recent_turn_summary_keeps_recent_turns(self) -> None:
        service = DummyInputService()

        summary = service._initiative_recent_turn_summary(
            [
                {"role": "assistant", "text": "さっき触れた内容。"},
                {"role": "user", "text": "了解。"},
            ]
        )

        self.assertEqual(
            summary,
            [
                {"role": "assistant", "text": "さっき触れた内容。"},
                {"role": "user", "text": "了解。"},
            ],
        )

    def test_initiative_opportunity_summary_is_evaluation_framed(self) -> None:
        service = DummyInputService()

        summary = service._initiative_opportunity_summary(
            trigger_kind="background_thinking",
            client_context={},
            selected_candidate=None,
            initiative_entry_summary={
                "entry_kind": "enter",
                "entry_basis": "activity_mode_transition",
                "reason_summary": "活動が切り替わった。",
            },
        )

        self.assertIn("評価対象", summary)
        self.assertIn("関わる、保留する、見送る", summary)
        self.assertNotIn("外向き", summary)

    def test_autonomous_family_reason_uses_evaluation_terms(self) -> None:
        service = DummyInputService()
        drive_summary = {
            "drive_kind": "care",
            "summary_text": "様子を気にかけている。",
            "freshness_hint": "fresh",
            "stability_hint": "stable",
        }

        reason = service._initiative_autonomous_family_reason(
            drive_summaries=[drive_summary],
            foreground_drive_summaries=[drive_summary],
            strongest_drive=drive_summary,
            world_state_summary=[],
            recent_turn_summary=[],
            initiative_entry_summary=None,
            visual_signals=[],
            suppression_summary={},
            capability_summary={},
        )

        self.assertIn("強く前景化した drive_state", reason)
        self.assertNotIn("speech-ready", reason)
        self.assertNotIn("speech の入口", reason)


if __name__ == "__main__":
    unittest.main()
