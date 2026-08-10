from __future__ import annotations

import unittest

from otomekairo.service.input.capability_context import ServiceInputCapabilityContextMixin


class _CapabilityContextService(ServiceInputCapabilityContextMixin):
    def _client_context_text(self, value: object, *, limit: int) -> str | None:
        # 実サービスと同様に、LLM入力となる正本文字列を長さで切らない。
        _ = limit
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


class CapabilityContextTests(unittest.TestCase):
    def test_fresh_state_reuse_is_limited_to_matching_visual_source(self) -> None:
        service = _CapabilityContextService()
        mcp_capability = {
            "id": "mcp.call_tool",
            "available": True,
            "mcp_servers": [{"mcp_server_id": "elyth"}],
        }
        vision_capability = {
            "id": "vision.capture",
            "available": True,
            "vision_sources": [
                {
                    "vision_source_id": "vision_source:desktop",
                    "label": "デスクトップ",
                },
                {
                    "vision_source_id": "vision_source:camera",
                    "label": "カメラ",
                },
            ],
        }

        result = service._annotate_capability_decision_view_with_fresh_visual_context(
            capability_decision_view=[mcp_capability, vision_capability],
            foreground_world_state=[
                {
                    "state_type": "external_service",
                    "scope": "world",
                    "summary_text": "タイムラインに未読の投稿が2件ある。",
                    "age_label": "たった今",
                    "integration_key": "external_service:mcp%3Aelyth/get_information",
                },
                {
                    "state_type": "visual_context",
                    "scope": "topic:current_work",
                    "summary_text": "エディタを開いている。",
                    "age_label": "1分前",
                    "confidence": 0.9,
                    "salience": 0.8,
                    "integration_key": "visual_context:vision_source:desktop",
                },
            ],
            world_state_trace=None,
            trigger_kind="capability_result",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[0], mcp_capability)
        self.assertNotIn("fresh_world_state_available", result[0])
        self.assertEqual(
            result[1]["fresh_world_state_by_vision_source"],
            [
                {
                    "vision_source_id": "vision_source:desktop",
                    "summary_text": "エディタを開いている。",
                    "age_label": "1分前",
                    "confidence": 0.9,
                    "salience": 0.8,
                    "source_label": "デスクトップ",
                }
            ],
        )
        self.assertEqual(
            result[1]["fresh_world_state_policy"],
            "同じ vision_source_id の新鮮な現在状態を再取得しない。",
        )


if __name__ == "__main__":
    unittest.main()
