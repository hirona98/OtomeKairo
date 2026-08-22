from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from otomekairo.capabilities import capability_readiness_input_digest
from otomekairo.service.app import OtomeKairoService


class CycleResultAndMemoryTests(unittest.TestCase):
    def test_capability_result_pending_intent_is_external_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            source_request = {
                "request_id": "mcp_call_tool_request:source",
                "capability_id": "mcp.call_tool",
            }
            self.assertEqual(
                service._external_result_kind(
                    speech_payload=None,
                    capability_request_summary=source_request,
                ),
                "capability_request",
            )
            self.assertEqual(
                service._external_result_kind(
                    speech_payload=None,
                    capability_request_summary=None,
                ),
                "noop",
            )

    def test_merges_self_activity_event_evidence_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            outward = {
                "candidate_count": 0,
                "selected_memory_ids": [],
                "selected_episode_ids": [],
                "selected_event_ids": [],
                "event_evidence_generation": service._empty_event_evidence_generation_trace(),
            }
            self_activity = {
                "event_evidence_generation": {
                    "requested_event_count": 8,
                    "loaded_event_count": 8,
                    "succeeded_event_count": 0,
                    "failed_items": [
                        {
                            "event_id": "event:1",
                            "kind": "decision",
                            "failure_stage": "llm_generation",
                            "failure_reason": "parse_failed",
                        }
                    ],
                    "precise_evidence_used": False,
                    "precise_reason_codes": ["not_needed"],
                    "precise_reason_summary": "圧縮済みだけを使う。",
                    "precise_selected_event_ids": [],
                    "precise_requested_event_count": 0,
                    "precise_loaded_event_count": 0,
                }
            }
            merged = service._recall_pack_with_merged_event_evidence(outward, [self_activity])
            generation = merged["event_evidence_generation"]
            self.assertEqual(generation["requested_event_count"], 8)
            self.assertEqual(generation["failed_items"][0]["event_id"], "event:1")

    def test_empty_arguments_count_as_present_readiness(self) -> None:
        digest = capability_readiness_input_digest(
            "mcp.call_tool",
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_information",
                "arguments": {},
            },
        )
        self.assertIsNotNone(digest)
        assert digest is not None
        self.assertTrue(digest["input_keys_satisfied"])
        self.assertEqual(digest["missing_input_keys"], [])

    def test_visual_cycle_with_capability_request_still_consolidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            pipeline = {
                "decision": {
                    "separated_comparisons": {
                        "self_activity": {"kind": "capability_request"},
                        "outward_speech": {"kind": "noop"},
                    }
                }
            }
            self.assertTrue(
                service._should_consolidate_spontaneous_cycle(
                    trigger_kind="background_thinking",
                    pipeline=pipeline,
                    observation_summary={
                        "source": "capability_result",
                        "capability_id": "vision.capture",
                    },
                    client_context={
                        "wake_observations": [{"capability_id": "vision.capture"}],
                    },
                )
            )
            self.assertFalse(
                service._should_consolidate_spontaneous_cycle(
                    trigger_kind="background_thinking",
                    pipeline={"decision": {"kind": "noop"}},
                    observation_summary={
                        "source": "capability_result",
                        "capability_id": "vision.capture",
                    },
                    client_context={
                        "wake_observations": [{"capability_id": "vision.capture"}],
                    },
                )
            )
