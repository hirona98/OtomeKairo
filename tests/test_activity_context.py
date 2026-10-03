from __future__ import annotations

import unittest
from unittest.mock import Mock
from datetime import datetime

from otomekairo.service.input.activity import ServiceInputActivityMixin


class DummyActivityService(ServiceInputActivityMixin):
    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class ActivityContextTests(unittest.TestCase):
    def test_unidentified_camera_person_is_not_activity_evidence(self) -> None:
        service = DummyActivityService()
        service._build_time_context = Mock(return_value={})
        service._activity_client_context = Mock(return_value={"source": "test", "wake_observation_summary": "別人の動作", "visual_observations": [{"summary_text": "別人の動作"}]})
        service._activity_observation_summary = Mock(return_value={"visual_summary_text": "別人の動作"})
        service._activity_source_owner = Mock(return_value="self")
        service._observed_persons_from_structured_source = Mock(return_value=[])
        pack = service._build_activity_source_pack(
            started_at="2026-10-02T17:00:00+09:00", input_text="私は台所にいる。",
            current_input={"sender_ref": "person:test", "sender_kind": "person"},
            recent_turns=[], trigger_kind="user_message", client_context={},
            observation_summary={}, visual_observation_context={"summary_text": "別人の動作"},
            foreground_world_state=[{"state_type": "visual_context", "summary_text": "別人の動作"}, {"state_type": "location", "summary_text": "台所"}],
            previous_activity_state=None, persona_context=Mock(),
        )
        self.assertNotIn("visual_observation_context", pack)
        self.assertEqual(pack["client_context"], {"source": "test"})
        self.assertEqual(pack["observation_summary"], {})
        self.assertEqual(pack["foreground_world_state"], [{"state_type": "location", "summary_text": "台所"}])
        self.assertEqual(pack["current_input_summary"], "私は台所にいる。")

    def test_continue_preserves_previous_activity_without_ending_current(self) -> None:
        service = DummyActivityService()
        previous = {"label": "休憩", "actor": "person", "ended_age_label": "5分前"}
        current = {
            "activity_id": "activity:current", "label": "文書を整理", "actor": "person",
            "started_at": "2026-07-07T20:00:00+09:00",
            "updated_at": "2026-07-07T20:05:00+09:00", "previous_activity": previous,
        }
        candidate = {
            "label": "文書を整理", "actor": "person", "target": "文書",
            "confidence_hint": "high", "salience_hint": "medium", "ttl_hint": "short",
            "transition": "continue", "reason_summary": "同じ作業が続いている。",
        }
        state, ended = service._normalize_activity_candidate(
            memory_set_id="memory:test", actor_ref="person:test",
            started_at="2026-07-07T20:08:00+09:00", source_pack={},
            previous_state=current, candidate=candidate, cycle_id="cycle:test",
        )
        self.assertIsNone(ended)
        assert state is not None
        self.assertEqual(state["activity_id"], current["activity_id"])
        self.assertEqual(state["started_at"], current["started_at"])
        self.assertEqual(state["previous_activity"], previous)
        current["previous_activity"] = None
        state, _ = service._normalize_activity_candidate(
            memory_set_id="memory:test", actor_ref="person:test",
            started_at="2026-07-07T20:09:00+09:00", source_pack={},
            previous_state=current, candidate=candidate, cycle_id="cycle:test",
        )
        self.assertIsNone(state["previous_activity"])

    def test_person_activity_rejects_self_actor(self) -> None:
        service = DummyActivityService()
        with self.assertRaises(ValueError):
            service._normalize_activity_candidate(
                memory_set_id="memory:test", actor_ref="person:test",
                started_at="2026-07-07T20:08:00+09:00", source_pack={},
                previous_state=None, candidate={"actor": "self", "transition": "start"},
                cycle_id="cycle:test",
            )

    def test_activity_context_includes_transition_and_duration_labels(self) -> None:
        service = DummyActivityService()

        context = service._summarize_activity_context(
            {
                "label": "離席中",
                "actor": "person",
                "target": "workspace",
                "transition": "continue",
                "confidence": 0.86,
                "salience": 0.62,
                "reason_summary": "作業場に戻っていない状態が続いている。",
                "started_at": "2026-07-07T01:08:16+09:00",
                "updated_at": "2026-07-07T19:38:16+09:00",
            },
            current_time="2026-07-07T20:08:16+09:00",
        )

        assert context is not None
        current_activity = context["current_activity"]
        self.assertEqual(current_activity["transition"], "continue")
        self.assertEqual(current_activity["started_age_label"], "19時間前")
        self.assertEqual(current_activity["duration_label"], "約19時間")
        self.assertEqual(current_activity["age_label"], "30分前")

    def test_start_transition_uses_pre_observation_activity_as_previous(self) -> None:
        service = DummyActivityService()

        activity_state, ended_activity_id = service._normalize_activity_candidate(
            memory_set_id="memory:set",
            actor_ref="person:test",
            started_at="2026-07-07T20:08:16+09:00",
            source_pack={
                "current_input": {"sender_kind": "system", "source_kind": "background_thinking"},
                "pre_observation_activity_context": {
                    "current_activity": {
                        "label": "離席中",
                        "actor": "person",
                        "target": "workspace",
                        "transition": "continue",
                        "started_age_label": "19時間前",
                        "duration_label": "約19時間",
                        "reason_summary": "長く作業場に不在だった。",
                    }
                },
            },
            previous_state={
                "activity_id": "activity:refreshed-by-observation",
                "label": "観測直後の短い状態",
                "actor": "person",
                "started_at": "2026-07-07T20:08:16+09:00",
                "updated_at": "2026-07-07T20:08:16+09:00",
            },
            candidate={
                "label": "アプリケーション起動検討",
                "actor": "person",
                "target": "desktop",
                "confidence_hint": "high",
                "salience_hint": "medium",
                "ttl_hint": "short",
                "transition": "start",
                "reason_summary": "desktop で新しい操作が始まっている。",
            },
            cycle_id="cycle:test",
        )

        self.assertIsNone(ended_activity_id)
        assert activity_state is not None
        previous_activity = activity_state["previous_activity"]
        self.assertEqual(previous_activity["label"], "離席中")
        self.assertEqual(previous_activity["duration_label"], "約19時間")
        self.assertEqual(previous_activity["ended_age_label"], "直前")
        self.assertIn("pre_observation_activity_context", activity_state["source_kinds"])


if __name__ == "__main__":
    unittest.main()
