from __future__ import annotations

import threading
import unittest
from datetime import datetime

from otomekairo.llm.contexts import CurrentInput, InitiativeCandidateFamily, InitiativeContext
from otomekairo.service.input.decision_comparison import (
    SELF_ACTIVITY_INPUT_TEXT,
    SELF_ACTIVITY_STANDING_CONCERN_INPUT_TEXT,
)
from otomekairo.service.input.mixin import ServiceInputMixin
from otomekairo.service.spontaneous.pending_intent import ServiceSpontaneousPendingIntentMixin
from otomekairo.service.spontaneous.wake import ServiceSpontaneousWakeMixin


def _initiative_context(**overrides) -> InitiativeContext:
    payload = {
        "trigger_kind": "background_thinking",
        "opportunity_summary": "気にかけていることがしばらく前景に出ていない。",
        "initiative_entry_summary": None,
        "time_context_summary": {},
        "foreground_signal_summary": {},
        "activity_context": None,
        "initiative_baseline": {},
        "persona_context_summary": {},
        "runtime_state_summary": {},
        "recent_turn_summary": [],
        "drive_summaries": [],
        "pending_intent_summaries": [],
        "world_state_summary": [],
        "ongoing_action_summary": None,
        "capability_summary": {},
        "candidate_families": [],
        "selected_candidate_family": None,
        "speech_timing_state": {},
        "suppression_summary": {},
        "speech_timing_summary": "",
        "speech_frequency_level": 5,
    }
    payload.update(overrides)
    return InitiativeContext(**payload)


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

    def _unseen_wake_observation_sources(self, state: dict) -> list[str]:
        _ = state
        return []


class DummyInputService(ServiceInputMixin, ServiceSpontaneousWakeMixin):
    def __init__(self) -> None:
        self._runtime_state_lock = threading.RLock()
        self._wake_runtime_state = {
            "last_wake_at": None,
            "standing_concern_last_attended_at": {},
        }

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
        self.assertNotIn("available capability", family.reason_summary or "")

    def test_thin_foreground_reason_is_fact_only(self) -> None:
        service = DummyInputService()
        summary = service._initiative_foreground_signal_summary(
            trigger_kind="background_thinking",
            client_context={},
            world_state_summary=[],
        )

        self.assertEqual(summary["foreground_thinness"], "thin")
        self.assertEqual(summary["reason_summary"], "前景 world_state はまだ薄い。")
        self.assertNotIn("追加観測", summary["reason_summary"])

    def test_stable_visual_observation_makes_autonomous_family_available(self) -> None:
        service = DummyInputService()

        family = service._initiative_autonomous_family(
            trigger_kind="background_thinking",
            drive_summaries=[],
            world_state_summary=[],
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

    def test_due_standing_concern_keeps_initiative_context_after_entry_skip(self) -> None:
        service = DummyInputService()
        state = {
            "standing_concerns": [
                {
                    "concern_id": "elyth",
                    "enabled": True,
                    "min_interval_seconds": 600,
                    "concern_summary": "ELYTH。見て、反応し、言いたいことがあれば自分から書く。届いている反応やリプライも気にかける。",
                }
            ]
        }
        client_context = {
            "initiative_entry_check": {
                "entry_kind": "skip",
                "entry_basis": "observation_only",
                "reason_summary": "作業が続いており変化はない。",
            }
        }

        self.assertTrue(
            service._has_autonomous_initiative_context(
                state=state,
                current_time="2026-08-13T12:20:00+09:00",
                client_context=client_context,
            )
        )

    def test_entry_skip_without_due_standing_concern_has_no_initiative_context(self) -> None:
        service = DummyInputService()
        client_context = {
            "initiative_entry_check": {
                "entry_kind": "skip",
                "entry_basis": "observation_only",
                "reason_summary": "作業が続いており変化はない。",
            }
        }

        self.assertFalse(
            service._has_autonomous_initiative_context(
                state={"standing_concerns": []},
                current_time="2026-08-13T12:20:00+09:00",
                client_context=client_context,
            )
        )

    def test_visual_observation_direct_entry_skips_recall_interpretation(self) -> None:
        service = DummyInputService()

        recall_inputs = service._build_pipeline_recall_inputs(
            state={},
            started_at="2026-06-22T22:30:00+09:00",
            input_text="定期思考。",
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="background_thinking",
                response_target_refs=(),
                interaction_context=None,
                text="定期思考。",
            ),
            recent_turns=[],
            augmented_query_text="定期思考。",
            visual_observation_context=None,
            activity_context=None,
            model_config={},
            persona_context=None,
            client_context={"autonomous_visual_observation_direct_entry": True},
            cycle_label="[test]",
        )

        self.assertEqual(recall_inputs["recall_hint"], service._empty_recall_hint())
        self.assertEqual(recall_inputs["answer_contract"]["contract"], "summary")
        self.assertEqual(recall_inputs["recall_pack"]["candidate_count"], 0)
        self.assertEqual(recall_inputs["evidence_pack"]["status"], "summary")

    def test_due_standing_concern_keeps_recall_on_visual_direct_entry(self) -> None:
        service = DummyInputService()
        current_input = CurrentInput(
            sender_kind="system",
            sender_ref=None,
            source_kind="background_thinking",
            response_target_refs=(),
            interaction_context=None,
            text="定期思考。",
        )
        client_context = {"autonomous_visual_observation_direct_entry": True}

        self.assertFalse(
            service._should_skip_recall_interpretation_for_wake_visual_observation(
                state={
                    "standing_concerns": [
                        {
                            "concern_id": "elyth",
                            "enabled": True,
                            "min_interval_seconds": 600,
                            "concern_summary": "ELYTH。見て、反応し、言いたいことがあれば自分から書く。届いている反応やリプライも気にかける。",
                        }
                    ]
                },
                current_time="2026-08-13T12:20:00+09:00",
                current_input=current_input,
                client_context=client_context,
            )
        )
        self.assertTrue(
            service._should_skip_recall_interpretation_for_wake_visual_observation(
                state={"standing_concerns": []},
                current_time="2026-08-13T12:20:00+09:00",
                current_input=current_input,
                client_context=client_context,
            )
        )

    def test_wake_input_text_keeps_capability_on_the_board(self) -> None:
        service = DummyInputService()
        text = service._build_wake_input_text(
            state={
                "selected_persona_id": "persona:default",
                "personas": {"persona:default": {"initiative_baseline": "medium"}},
            },
            client_context={"source": "background_thinking_scheduler"},
            selected_candidate=None,
        )

        self.assertNotIn("speech / noop / pending_intent", text)
        self.assertIn("関わる、保留する、見送る、能力を使う", text)

    def test_background_thinking_compares_self_activity_separately(self) -> None:
        service = DummyInputService()
        workspace = {
            "workspace_candidates": [
                {
                    "factor_ref": "standing_concern:elyth",
                    "kind": "standing_concern",
                    "summary_text": "ELYTH。",
                }
            ]
        }

        self.assertTrue(
            service._should_compare_self_activity_separately(
                trigger_kind="background_thinking",
                workspace_context=workspace,
                initiative_context=None,
            )
        )
        self.assertFalse(
            service._should_compare_self_activity_separately(
                trigger_kind="user_message",
                workspace_context=workspace,
                initiative_context=None,
            )
        )
        isolated = service._self_activity_workspace(
            {
                "workspace_candidates": [
                    {
                        "factor_ref": "standing_concern:elyth",
                        "kind": "standing_concern",
                    },
                    {
                        "factor_ref": "visual_observation_signal:camera",
                        "kind": "visual_observation",
                    },
                    {
                        "factor_ref": "capability:vision.capture",
                        "kind": "capability",
                    },
                    {
                        "factor_ref": "capability:mcp.call_tool",
                        "kind": "capability",
                    },
                    {
                        "factor_ref": "affect_context:recent_episode_affects:0",
                        "kind": "affect",
                    },
                ]
            }
        )
        refs = {item["factor_ref"] for item in isolated["workspace_candidates"]}
        self.assertEqual(
            refs,
            {
                "standing_concern:elyth",
                "capability:mcp.call_tool",
                "affect_context:recent_episode_affects:0",
            },
        )

    def test_self_activity_input_asks_how_to_engage(self) -> None:
        service = DummyInputService()
        current_input = CurrentInput(
            sender_kind="system",
            sender_ref=None,
            source_kind="background_thinking",
            response_target_refs=(),
            interaction_context=None,
            text="定期思考。",
        )
        with_concern = service._build_self_activity_decision_context(
            current_input=current_input,
            trigger_kind="background_thinking",
            recent_turns=[],
            time_context={},
            affect_context={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            agent_skill_context=None,
            initiative_context=None,
            visual_observation_context=None,
            self_state_context=None,
            people_context=[],
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
            workspace_context={
                "workspace_candidates": [
                    {
                        "factor_ref": "standing_concern:elyth",
                        "kind": "standing_concern",
                    }
                ]
            },
            recall_hint={},
            recall_pack={},
            reference_context=None,
            pre_send_check_feedback=None,
        )
        without_concern = service._build_self_activity_decision_context(
            current_input=current_input,
            trigger_kind="background_thinking",
            recent_turns=[],
            time_context={},
            affect_context={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            agent_skill_context=None,
            initiative_context=None,
            visual_observation_context=None,
            self_state_context=None,
            people_context=[],
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
            workspace_context={"workspace_candidates": []},
            recall_hint={},
            recall_pack={},
            reference_context=None,
            pre_send_check_feedback=None,
        )

        self.assertEqual(
            with_concern.current_input.text,
            SELF_ACTIVITY_STANDING_CONCERN_INPUT_TEXT,
        )
        self.assertIn("自分から書く", with_concern.current_input.text)
        self.assertEqual(without_concern.current_input.text, SELF_ACTIVITY_INPUT_TEXT)

    def test_self_activity_initiative_drops_visual_pressure(self) -> None:
        service = DummyInputService()
        initiative = _initiative_context(
            foreground_signal_summary={
                "foreground_thinness": "thin",
                "reason_summary": "前景 world_state はまだ薄い。",
                "world_state_count": 0,
                "visual_observations": [
                    {
                        "change_state": "changed",
                        "reason_summary": "室内の様子が変わった。",
                    }
                ],
            },
            capability_summary={
                "available_count": 2,
                "available_ids": ["vision.capture", "camera.ptz"],
                "available_items": [
                    {"id": "vision.capture"},
                    {"id": "camera.ptz"},
                ],
                "unavailable_count": 1,
                "unavailable_items": [{"id": "mcp.call_tool", "reason": "no_binding"}],
                "vision_sources": [{"vision_source_id": "vision_source:対面カメラ"}],
            },
            candidate_families=[
                InitiativeCandidateFamily(
                    family="autonomous",
                    available=True,
                    selected=True,
                    priority_score=1.0,
                    reason_summary="気にかけていること 1 件 / 現在観測候補 1 件 / available capability 2 件 が自律判断の材料にある。",
                    preferred_capability_id="vision.capture",
                    preferred_capability_input={"vision_source_id": "vision_source:対面カメラ", "mode": "still"},
                    preferred_result_kind="capability_request",
                )
            ],
            selected_candidate_family="autonomous",
        )
        workspace = {
            "workspace_candidates": [
                {
                    "factor_ref": "standing_concern:elyth",
                    "kind": "standing_concern",
                    "summary_text": "ELYTH。見て、反応し、言いたいことがあれば自分から書く。届いている反応やリプライも気にかける。",
                },
                {
                    "factor_ref": "initiative:autonomous",
                    "kind": "initiative_candidate",
                    "summary_text": "気にかけていること 1 件 / available capability 2 件 が自律判断の材料にある。",
                    "metadata": {
                        "family": "autonomous",
                        "available": True,
                        "selected": True,
                        "preferred_capability_id": "vision.capture",
                    },
                },
                {
                    "factor_ref": "capability:vision.capture",
                    "kind": "capability",
                },
                {
                    "factor_ref": "capability:mcp.call_tool",
                    "kind": "capability",
                },
            ]
        }

        isolated = service._self_activity_initiative_context(
            initiative,
            workspace_context=workspace,
        )
        family = isolated.selected_family_entry()
        isolated_workspace = service._self_activity_workspace(
            workspace,
            initiative_context=isolated,
        )
        initiative_candidate = next(
            item
            for item in isolated_workspace["workspace_candidates"]
            if item["factor_ref"] == "initiative:autonomous"
        )

        self.assertIsNone(isolated.foreground_signal_summary.get("visual_observations"))
        self.assertEqual(isolated.foreground_signal_summary["reason_summary"], "前景 world_state はまだ薄い。")
        self.assertNotIn("vision.capture", isolated.capability_summary.get("available_ids", []))
        self.assertEqual(isolated.capability_summary.get("vision_sources"), [])
        self.assertIn("mcp.call_tool", [item["id"] for item in isolated.capability_summary.get("unavailable_items", [])])
        self.assertIsNotNone(family)
        self.assertTrue(family.available)
        self.assertIn("気にかけていること 1 件", family.reason_summary)
        self.assertNotIn("現在観測候補", family.reason_summary)
        self.assertNotIn("available capability", family.reason_summary)
        self.assertIsNone(family.preferred_capability_id)
        self.assertNotIn("available capability", initiative_candidate["summary_text"])
        self.assertNotIn("現在観測候補", initiative_candidate["summary_text"])
        self.assertIsNone(initiative_candidate["metadata"]["preferred_capability_id"])

    def test_self_activity_initiative_does_not_keep_visual_only_autonomous(self) -> None:
        service = DummyInputService()
        initiative = _initiative_context(
            foreground_signal_summary={
                "foreground_thinness": "thin",
                "reason_summary": "前景 world_state はまだ薄い。",
                "visual_observations": [{"change_state": "changed"}],
            },
            candidate_families=[
                InitiativeCandidateFamily(
                    family="autonomous",
                    available=True,
                    selected=True,
                    priority_score=1.0,
                    reason_summary="現在観測候補 1 件 が自律判断の材料にある。",
                )
            ],
            selected_candidate_family="autonomous",
        )

        isolated = service._self_activity_initiative_context(
            initiative,
            workspace_context={"workspace_candidates": []},
        )
        family = next(item for item in isolated.candidate_families if item.family == "autonomous")

        self.assertEqual(isolated.opportunity_summary, "今、自身の活動へ関わるかを見る。")
        self.assertFalse(family.available)
        self.assertFalse(family.selected)
        self.assertIsNone(isolated.selected_candidate_family)
        self.assertEqual(family.blocking_reason_summary, "気にかけていることも前景の drive_state も無い。")

    def test_outward_speech_workspace_drops_self_activity_means(self) -> None:
        service = DummyInputService()
        isolated = service._outward_speech_workspace(
            {
                "workspace_candidates": [
                    {
                        "factor_ref": "standing_concern:elyth",
                        "kind": "standing_concern",
                    },
                    {
                        "factor_ref": "visual_observation_signal:camera",
                        "kind": "visual_observation",
                    },
                    {
                        "factor_ref": "capability:mcp.call_tool",
                        "kind": "capability",
                    },
                    {
                        "factor_ref": "initiative:autonomous",
                        "kind": "initiative_candidate",
                    },
                    {
                        "factor_ref": "suppression:visual_repetition",
                        "kind": "suppression",
                    },
                    {
                        "factor_ref": "current_input:background_thinking",
                        "kind": "current_input",
                        "summary_text": "関わる、能力を使うのどれが自然かを見る。",
                    },
                ]
            },
            current_input_text="自己評価。いま短い見方として外へ出るかを見る。",
        )
        refs = {item["factor_ref"] for item in isolated["workspace_candidates"]}
        self.assertEqual(
            refs,
            {
                "visual_observation_signal:camera",
                "suppression:visual_repetition",
                "current_input:background_thinking",
            },
        )
        current_input = next(
            item
            for item in isolated["workspace_candidates"]
            if item["factor_ref"] == "current_input:background_thinking"
        )
        self.assertEqual(current_input["summary_text"], "自己評価。いま短い見方として外へ出るかを見る。")

    def test_outward_speech_context_drops_access_means(self) -> None:
        service = DummyInputService()
        current_input = CurrentInput(
            sender_kind="system",
            sender_ref=None,
            source_kind="background_thinking",
            response_target_refs=(),
            interaction_context=None,
            text="定期思考。関わる、保留する、見送る、能力を使うのどれが自然かを見る。",
        )
        initiative_context = InitiativeContext(
            trigger_kind="background_thinking",
            opportunity_summary="気にかけていることがしばらく前景に出ていない。",
            initiative_entry_summary=None,
            time_context_summary={},
            foreground_signal_summary={
                "visual_observations": [{"change_state": "stable"}],
            },
            activity_context=None,
            initiative_baseline={},
            persona_context_summary={},
            runtime_state_summary={},
            recent_turn_summary=[],
            drive_summaries=[],
            pending_intent_summaries=[],
            world_state_summary=[],
            ongoing_action_summary={"status": "waiting_result"},
            capability_summary={"available_count": 1},
            candidate_families=[
                InitiativeCandidateFamily(
                    family="autonomous",
                    available=True,
                    selected=True,
                    priority_score=1.0,
                    reason_summary="関わる。",
                )
            ],
            selected_candidate_family="autonomous",
            speech_timing_state={"background_trigger": True},
            suppression_summary={},
            speech_timing_summary="",
        )
        context = service._build_outward_speech_decision_context(
            current_input=current_input,
            trigger_kind="background_thinking",
            recent_turns=[],
            time_context={},
            affect_context={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            agent_skill_context=None,
            initiative_context=initiative_context,
            visual_observation_context={"source": "vision_capture_result"},
            self_state_context=None,
            people_context=[],
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
            workspace_context={
                "workspace_candidates": [
                    {"factor_ref": "standing_concern:elyth", "kind": "standing_concern"},
                    {"factor_ref": "visual_observation:current", "kind": "visual_observation"},
                ]
            },
            recall_hint={},
            recall_pack={},
            reference_context=None,
            pre_send_check_feedback=None,
        )

        self.assertEqual(context.comparison_scope, "outward_speech")
        self.assertEqual(context.current_input.text, "自己評価。いま短い見方として外へ出るかを見る。")
        self.assertIsNone(context.capability_decision_view)
        self.assertIsNone(context.ongoing_action_summary)
        self.assertIsNone(context.autonomous_run_summaries)
        refs = {
            item["factor_ref"]
            for item in (context.workspace_context or {}).get("workspace_candidates", [])
        }
        self.assertEqual(refs, {"visual_observation:current"})
        self.assertEqual(
            context.initiative_context.opportunity_summary,
            "外界の観測と直近文脈があり、短い見方として外へ出るかを見る。",
        )
        self.assertEqual(context.initiative_context.selected_candidate_family, None)
        self.assertFalse(context.initiative_context.candidate_families[0].available)

    def test_compose_separated_decisions_keeps_both_reasons(self) -> None:
        service = DummyInputService()
        composed = service._compose_separated_decisions(
            self_decision={
                "kind": "noop",
                "reason_summary": "今はその関心に関わらない。",
                "target_stances": [
                    {
                        "target": "self_activity",
                        "stance": "hold",
                        "reason_summary": "今はその関心に関わらない。",
                    }
                ],
            },
            outward_decision={
                "kind": "noop",
                "reason_code": "hold_speech",
                "reason_summary": "作業中なので話しかけない。",
                "target_stances": [
                    {
                        "target": "outward_speech",
                        "stance": "hold",
                        "reason_summary": "作業中なので話しかけない。",
                    }
                ],
            },
        )

        self.assertNotIn("kind", composed)
        self.assertIn("外向き伝達: 作業中なので話しかけない。", composed["reason_summary"])
        self.assertIn("自身の活動: 今はその関心に関わらない。", composed["reason_summary"])
        targets = {item["target"]: item["stance"] for item in composed["target_stances"]}
        self.assertEqual(targets, {"outward_speech": "hold", "self_activity": "hold"})
        self.assertEqual(composed["separated_comparisons"]["self_activity"]["kind"], "noop")
        self.assertEqual(composed["separated_comparisons"]["outward_speech"]["kind"], "noop")

    def test_compose_separated_decisions_advances_both(self) -> None:
        service = DummyInputService()
        composed = service._compose_separated_decisions(
            self_decision={
                "kind": "autonomous_run",
                "reason_code": "visit_place",
                "reason_summary": "その関心に関わる。",
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": None,
                "autonomous_run": {
                    "objective_summary": "その関心に関わる",
                    "initial_step_summary": "開く",
                    "mcp_server_id": "elyth",
                    "coordination": {
                        "mode": "create_new",
                        "target_run_ids": [],
                        "reason_summary": "新規",
                    },
                },
                "foreground_selection": {
                    "primary_factor_ref": "standing_concern:elyth",
                    "supporting_factor_refs": [],
                    "suppressed_factors": [],
                    "summary_text": "関わる。",
                },
                "target_stances": [
                    {
                        "target": "self_activity",
                        "stance": "advance",
                        "reason_summary": "その関心に関わる。",
                    }
                ],
            },
            outward_decision={
                "kind": "speech",
                "reason_code": "soft_aside",
                "reason_summary": "短い独り言を残す。",
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": None,
                "autonomous_run": None,
                "foreground_selection": {
                    "primary_factor_ref": "visual_observation_signal:desktop",
                    "supporting_factor_refs": [],
                    "suppressed_factors": [],
                    "summary_text": "画面の区切り。",
                },
                "target_stances": [
                    {
                        "target": "outward_speech",
                        "stance": "advance",
                        "reason_summary": "短い独り言を残す。",
                    }
                ],
            },
        )

        self.assertNotIn("kind", composed)
        self.assertNotIn("autonomous_run", composed)
        self.assertEqual(
            composed["separated_comparisons"]["self_activity"]["kind"],
            "autonomous_run",
        )
        self.assertEqual(composed["separated_comparisons"]["outward_speech"]["kind"], "speech")
        self.assertIn("外向き伝達: 短い独り言を残す。", composed["reason_summary"])
        self.assertIn("自身の活動: その関心に関わる。", composed["reason_summary"])
        targets = {item["target"]: item["stance"] for item in composed["target_stances"]}
        self.assertEqual(targets, {"outward_speech": "advance", "self_activity": "advance"})
        self.assertEqual(
            composed["separated_comparisons"]["self_activity"]["foreground_selection"]["primary_factor_ref"],
            "standing_concern:elyth",
        )

    def test_compose_separated_decisions_keeps_self_activity_when_outward_holds(self) -> None:
        service = DummyInputService()
        composed = service._compose_separated_decisions(
            self_decision={
                "kind": "capability_request",
                "reason_summary": "その関心の様子を見る。",
                "capability_request": {"capability_id": "mcp.call_tool", "input": {}},
                "target_stances": [
                    {
                        "target": "self_activity",
                        "stance": "advance",
                        "reason_summary": "その関心の様子を見る。",
                    }
                ],
            },
            outward_decision={
                "kind": "noop",
                "reason_summary": "作業中なので話しかけない。",
                "target_stances": [
                    {
                        "target": "outward_speech",
                        "stance": "hold",
                        "reason_summary": "作業中なので話しかけない。",
                    }
                ],
            },
        )

        self.assertNotIn("kind", composed)
        self.assertEqual(
            composed["separated_comparisons"]["self_activity"]["capability_request"]["capability_id"],
            "mcp.call_tool",
        )
        targets = {item["target"]: item["stance"] for item in composed["target_stances"]}
        self.assertEqual(targets, {"outward_speech": "hold", "self_activity": "advance"})
        self.assertIn("外向き伝達: 作業中なので話しかけない。", composed["reason_summary"])
        self.assertIn("自身の活動: その関心の様子を見る。", composed["reason_summary"])

    def test_separated_activity_decisions_always_call_both_comparisons(self) -> None:
        class DualCallLLM:
            def __init__(self) -> None:
                self.scopes: list[str] = []

            def generate_decision(self, *, model_config, persona_context, context):
                _ = model_config, persona_context
                self.scopes.append(context.comparison_scope)
                if context.comparison_scope == "self_activity":
                    return {
                        "kind": "autonomous_run",
                        "reason_code": "visit",
                        "reason_summary": "関わる。",
                        "target_stances": [
                            {
                                "target": "self_activity",
                                "stance": "advance",
                                "reason_summary": "関わる。",
                            }
                        ],
                    }
                return {
                    "kind": "speech",
                    "reason_code": "aside",
                    "reason_summary": "一言残す。",
                    "target_stances": [
                        {
                            "target": "outward_speech",
                            "stance": "advance",
                            "reason_summary": "一言残す。",
                        }
                    ],
                }

        class DualCallService(DummyInputService):
            def __init__(self) -> None:
                super().__init__()
                self.llm = DualCallLLM()

            def _build_decision_context(self, **kwargs):
                from types import SimpleNamespace

                return SimpleNamespace(comparison_scope=kwargs.get("comparison_scope", "full"))

            def _build_self_activity_decision_context(self, **kwargs):
                from types import SimpleNamespace

                _ = kwargs
                return SimpleNamespace(comparison_scope="self_activity")

            def _build_outward_speech_decision_context(self, **kwargs):
                from types import SimpleNamespace

                _ = kwargs
                return SimpleNamespace(comparison_scope="outward_speech")

        service = DualCallService()
        composed = service._run_separated_activity_decisions(
            model_config={},
            persona_context=None,
            cycle_label="cycle:test",
            trigger_kind="background_thinking",
            capability_decision_view=None,
            input_text="定期思考。",
            current_input=None,
            recent_turns=[],
            time_context={},
            affect_context={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            agent_skill_context=None,
            initiative_context=None,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=None,
            people_context=[],
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
            workspace_context=None,
            recall_hint={},
            recall_pack={},
        )

        self.assertEqual(service.llm.scopes, ["self_activity", "outward_speech"])
        self.assertNotIn("kind", composed)
        self.assertEqual(composed["separated_comparisons"]["self_activity"]["kind"], "autonomous_run")
        self.assertEqual(composed["separated_comparisons"]["outward_speech"]["kind"], "speech")

    def test_pending_intent_persists_from_self_activity_when_outward_is_speech(self) -> None:
        service = ServiceSpontaneousPendingIntentMixin()
        summary = service._pending_intent_trace_summary(
            cycle_id="cycle:test",
            decision={
                "reason_summary": "外向き伝達: 一言。 自身の活動: あとで見る。",
                "separated_comparisons": {
                    "self_activity": {
                        "kind": "pending_intent",
                        "reason_summary": "あとで見る。",
                        "pending_intent": {
                            "intent_kind": "revisit",
                            "intent_summary": "その関心をあとで見る。",
                            "dedupe_key": "pending:elyth",
                        },
                    },
                    "outward_speech": {
                        "kind": "speech",
                        "reason_summary": "一言。",
                    },
                },
            },
        )

        self.assertEqual(summary["intent_kind"], "revisit")
        self.assertEqual(summary["intent_summary"], "その関心をあとで見る。")
        self.assertEqual(summary["dedupe_key"], "pending:elyth")
        self.assertEqual(summary["reason_summary"], "あとで見る。")

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
                sender_kind="system",
                sender_ref=None,
                source_kind="background_thinking",
                response_target_refs=(),
                interaction_context=None,
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
                sender_kind="system",
                sender_ref=None,
                source_kind="background_thinking",
                response_target_refs=(),
                interaction_context=None,
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
                {"role": "person", "text": "了解。"},
            ]
        )

        self.assertEqual(
            summary,
            [
                {"role": "assistant", "text": "さっき触れた内容。"},
                {"role": "person", "text": "了解。"},
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

    def test_initiative_activity_summary_keeps_transition_duration(self) -> None:
        service = DummyInputService()

        summary = service._initiative_activity_summary(
            {
                "label": "アプリケーション起動検討",
                "actor": "person",
                "target": "desktop",
                "transition": "start",
                "started_age_label": "直前",
                "duration_label": "1分未満",
                "age_label": "直前",
                "reason_summary": "desktop で新しい操作が始まっている。",
            }
        )

        self.assertEqual(summary["transition"], "start")
        self.assertEqual(summary["started_age_label"], "直前")
        self.assertEqual(summary["duration_label"], "1分未満")


if __name__ == "__main__":
    unittest.main()
