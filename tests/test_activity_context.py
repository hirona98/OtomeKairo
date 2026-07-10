from __future__ import annotations

import unittest
from datetime import datetime

from otomekairo.service.input.activity import ServiceInputActivityMixin


class DummyActivityService(ServiceInputActivityMixin):
    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class ActivityContextTests(unittest.TestCase):
    def test_activity_context_includes_transition_and_duration_labels(self) -> None:
        service = DummyActivityService()

        context = service._summarize_activity_context(
            {
                "label": "離席中",
                "actor": "user",
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
            started_at="2026-07-07T20:08:16+09:00",
            source_pack={
                "current_input": {"sender": "system", "source_kind": "background_thinking"},
                "pre_observation_activity_context": {
                    "current_activity": {
                        "label": "離席中",
                        "actor": "user",
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
                "actor": "user",
                "started_at": "2026-07-07T20:08:16+09:00",
                "updated_at": "2026-07-07T20:08:16+09:00",
            },
            candidate={
                "label": "アプリケーション起動検討",
                "actor": "user",
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
