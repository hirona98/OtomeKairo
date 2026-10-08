from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from otomekairo.service.app import OtomeKairoService


NOW = "2026-09-27T18:00:00+09:00"


class EvaluationRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = OtomeKairoService(Path(self.temp_dir.name))
        self.state = self.service.store.read_state()
        self.memory_set_id = self.state["selected_memory_set_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_memory_snapshot_shows_active_understandings_only(self) -> None:
        with self.service.store._memory_db() as conn:
            for index, status in enumerate(("confirmed", "inferred", "dormant", "revoked")):
                self.service.store._upsert_memory_unit(conn, {
                    "memory_unit_id": f"memory_unit:{status}",
                    "memory_set_id": self.memory_set_id,
                    "memory_type": "fact",
                    "scope_type": "entity",
                    "scope_key": "person:test",
                    "subject_ref": "person:test",
                    "predicate": "prefers_drink",
                    "object_ref_or_value": status,
                    "summary_text": f"飲み物についての{status}理解。",
                    "status": status,
                    "confidence": 0.7,
                    "salience": 0.8 - index * 0.1,
                    "formed_at": NOW,
                })

        token = self.service.acquire_console_access_token()["console_access_token"]
        snapshot = self.service.get_memory_snapshot_inspection(token)

        self.assertEqual(
            [unit["status"] for unit in snapshot["memory_units"]],
            ["confirmed", "inferred"],
        )

    def test_wake_camera_interpretation_becomes_durable_visual_record(self) -> None:
        visual_result = {
            "summary_text": "机の上に小さな鉢植えが見える。",
            "confidence_hint": "high",
            "change_state": "first_seen",
            "change_basis": "no_previous_observation",
            "change_reason_summary": "前回の観測がない。",
        }
        with patch.object(type(self.service.llm), "generate_visual_observation_summary", return_value=visual_result):
            _, interpreted = self.service._interpret_visual_observation(
                state=self.state,
                started_at=NOW,
                trigger_kind="capability_result",
                client_context={"source_kind": "camera"},
                observation_summary={
                    "source": "capability_result",
                    "capability_id": "vision.capture",
                    "source_kind": "camera",
                    "source_label": "対面カメラ",
                    "vision_source_id": "vision_source:test-camera",
                    "image_count": 1,
                },
                input_text="カメラの結果を受信。",
                images=["data:image/png;base64,YQ=="],
            )
        wake_summary = self.service._wake_policy_observation_success_summary(
            observation={"observation_id": "wake_observation:camera", "capability_id": "vision.capture"},
            capability_response={"request_id": "request:test-camera"},
            observation_summary=interpreted,
            capability_request_summary=None,
        )
        records = self.service._build_visual_observation_records(
            cycle_id="cycle:test-camera",
            memory_set_id=self.memory_set_id,
            observed_at=NOW,
            client_context={"wake_observation_trace": {"wake_observations": [wake_summary]}},
            observation_summary=None,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["image_input_kind"], "vision_capture_result")
        self.assertEqual(records[0]["detailed_summary_text"], "机の上に小さな鉢植えが見える。")
        with self.service.store._memory_db() as conn:
            self.service.store._insert_visual_observation_record(conn, records[0])
        stored = self.service.store.list_recent_visual_observation_records(memory_set_id=self.memory_set_id)
        self.assertEqual(stored[0]["visual_observation_id"], interpreted["visual_observation_id"])

    def test_interpreted_visual_observation_without_identity_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "observation ID"):
            self.service._build_visual_observation_records(
                cycle_id="cycle:missing-id",
                memory_set_id=self.memory_set_id,
                observed_at=NOW,
                client_context={},
                observation_summary={
                    "image_interpreted": True,
                    "image_input_kind": "vision_capture_result",
                    "source_kind": "camera",
                    "visual_summary_text": "机が見える。",
                },
            )

    def test_autonomous_speech_is_in_origin_conversation_history(self) -> None:
        run = {
            "run_id": "autonomous_run:test-reminder",
            "memory_set_id": self.memory_set_id,
            "source_cycle_id": "cycle:test-reminder",
            "origin_interaction_ref": "interaction:test-master",
            "participant_refs": ["person:master"],
            "source_current_input": {
                "sender_kind": "person",
                "sender_ref": "person:master",
                "source_kind": "user_message",
                "response_target_refs": ["person:master"],
                "interaction_context": {
                    "interaction_ref": "interaction:test-master",
                    "speaker_ref": "person:master",
                    "participants": [{"person_ref": "person:master", "display_name": "マスター"}],
                },
                "text": "あとで作業へ戻る時間を知らせて。",
            },
        }
        self.service._persist_autonomous_run_speech_event(
            state=self.state,
            run=run,
            speech_payload={"speech_text": "そろそろ作業に戻る時間です。"},
            created_at=NOW,
            step={"action": {"kind": "speech"}},
            transition={"kind": "complete"},
        )

        history = self.service.store.load_conversation_history(
            memory_set_id=self.memory_set_id,
            interaction_ref="interaction:test-master",
            limit=30,
        )
        other_history = self.service.store.load_conversation_history(
            memory_set_id=self.memory_set_id,
            interaction_ref="interaction:other",
            limit=30,
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["message"], "そろそろ作業に戻る時間です。")
        self.assertEqual(history[0]["display_name"], self.state["personas"][self.state["selected_persona_id"]]["display_name"])
        self.assertEqual(other_history, [])


if __name__ == "__main__":
    unittest.main()
