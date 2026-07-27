from __future__ import annotations

import unittest
from datetime import datetime

from otomekairo.llm.contracts import LLMError, validate_world_state_contract
from otomekairo.llm.mock import MockLLMClient
from otomekairo.llm.prompts import _build_world_state_system_prompt, build_world_state_repair_prompt
from otomekairo.service.input.world_state import ServiceInputWorldStateMixin
from otomekairo.world_state.models import (
    WorldStateClientContext,
    WorldStateExternalServiceContext,
    WorldStateNamedSummaryContext,
    WorldStateScheduleContext,
    WorldStateSourcePack,
    WorldStateVisualContext,
)


class _WorldStateService(ServiceInputWorldStateMixin):
    # 実サービスの時刻変換境界だけをテスト用に与える。
    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _client_context_text(self, value: object, *, limit: int) -> str | None:
        # 実サービスと同様に、LLM入力となる正本文字列を長さで切らない。
        _ = limit
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


def _source_pack() -> WorldStateSourcePack:
    pack = WorldStateSourcePack(
        trigger_kind="capability_result",
        current_input_summary="現在状態を更新する。",
        source_kind="capability_result",
        source_ref="request:test",
        time_context="2026年7月20日 月曜日 12時00分（日本時間）",
        client_context=WorldStateClientContext(source="test"),
        current_person_ref="person:external-123",
        visual_context=WorldStateVisualContext(
            summary_text="作業画面が前景にある。",
            visual_summary_text="エディタで文書を開いている。",
            image_interpreted=True,
            vision_source_id="vision_source:test",
            source_kind="desktop",
            source_owner="user_environment",
        ),
        external_service_context=WorldStateExternalServiceContext(
            summary_text="外部サービスに未確認項目がある。",
            status_text="外部サービスに未確認項目がある。",
            service="example",
        ),
        body_context=WorldStateNamedSummaryContext(
            summary_text="肩に疲れがある。",
            summary_field_name="body_state_summary",
        ),
        device_context=WorldStateNamedSummaryContext(
            summary_text="接続機器は正常である。",
            summary_field_name="device_state_summary",
        ),
        schedule_context=WorldStateScheduleContext(
            summary_text="近い予定がある。",
            schedule_summary="近い予定がある。",
        ),
        social_context_context=WorldStateNamedSummaryContext(
            summary_text="対話の続きが前景にある。",
            summary_field_name="social_context_summary",
        ),
        environment_context=WorldStateNamedSummaryContext(
            summary_text="作業環境は静かである。",
            summary_field_name="environment_summary",
        ),
        location_context=WorldStateNamedSummaryContext(
            summary_text="自宅デスクにいる。",
            summary_field_name="location_summary",
        ),
    )
    service = _WorldStateService()
    pack.state_sources = tuple(service._build_world_state_source_candidates(source_pack=pack))
    return pack


class WorldStateContractTests(unittest.TestCase):
    def test_source_candidates_have_code_owned_scopes(self) -> None:
        pack = _source_pack()

        actual = {
            candidate.state_type: (candidate.candidate_ref, candidate.scope_type, candidate.scope_key)
            for candidate in pack.state_sources
        }

        self.assertEqual(
            actual,
            {
                "visual_context": ("state_source:visual_context", "topic", "topic:current_work"),
                "external_service": ("state_source:external_service", "world", "world"),
                "body": ("state_source:body", "self", "self"),
                "device": ("state_source:device", "world", "world"),
                "schedule": ("state_source:schedule", "self", "self"),
                "social_context": (
                    "state_source:social_context",
                    "relationship",
                    "self|person:external-123",
                ),
                "environment": ("state_source:environment", "world", "world"),
                "location": ("state_source:location", "world", "world"),
            },
        )
        prompt_payload = pack.to_prompt_payload()
        self.assertNotIn("allowed_state_types", prompt_payload)
        self.assertEqual(len(prompt_payload["state_sources"]), 8)

    def test_contract_accepts_only_unique_refs_from_source_pack(self) -> None:
        pack = _source_pack()
        candidate = {
            "candidate_ref": "state_source:visual_context",
            "summary_text": "作業画面が前景にある。",
            "confidence_hint": "medium",
            "salience_hint": "high",
            "ttl_hint": "short",
        }

        validate_world_state_contract({"state_candidates": [candidate]}, source_pack=pack)

        invalid_payloads = (
            {"state_candidates": [{**candidate, "candidate_ref": "state_source:unknown"}]},
            {"state_candidates": [candidate, dict(candidate)]},
            {
                "state_candidates": [
                    {
                        "state_type": "visual_context",
                        "scope": "topic:current_work",
                        "summary_text": "作業画面が前景にある。",
                        "confidence_hint": "medium",
                        "salience_hint": "high",
                        "ttl_hint": "short",
                    }
                ]
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(LLMError):
                    validate_world_state_contract(payload, source_pack=pack)

    def test_normalization_resolves_identity_from_candidate_ref(self) -> None:
        pack = _source_pack()
        service = _WorldStateService()

        records = service._normalize_world_state_candidates(
            memory_set_id="memory_set:test",
            observed_at="2026-07-20T12:00:00+09:00",
            source_kind="capability_result",
            source_ref="request:test",
            payload={
                "state_candidates": [
                    {
                        "candidate_ref": "state_source:visual_context",
                        "summary_text": "作業画面が前景にある。",
                        "confidence_hint": "medium",
                        "salience_hint": "high",
                        "ttl_hint": "short",
                    }
                ]
            },
            source_pack=pack,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state_type"], "visual_context")
        self.assertEqual(records[0]["scope_type"], "topic")
        self.assertEqual(records[0]["scope_key"], "topic:current_work")
        self.assertEqual(records[0]["integration_key"], "visual_context:vision_source:test")
        self.assertEqual(records[0]["source_owner"], "user_environment")

    def test_mcp_result_builds_external_service_context_with_exact_integration_key(self) -> None:
        service = _WorldStateService()

        context = service._build_world_state_external_service_context(
            client_context={},
            observation_summary={
                "capability_id": "mcp.call_tool",
                "status": "completed",
                "is_error": False,
                "error": None,
                "mcp_result_summary": "タイムラインに未読の投稿が2件ある。",
                "mcp_server_id": "mcp:elyth/main",
                "tool_name": "timeline/read",
            },
            source_kind="capability_result",
        )

        self.assertIsInstance(context, WorldStateExternalServiceContext)
        self.assertEqual(context.summary_text, "タイムラインに未読の投稿が2件ある。")
        self.assertEqual(context.mcp_server_id, "mcp:elyth/main")
        self.assertEqual(context.tool_name, "timeline/read")
        self.assertEqual(
            context.summary_source_hint,
            "capability_result.client_context.mcp_result_summary",
        )
        self.assertEqual(
            service._world_state_integration_policy(
                state_type="external_service",
                scope_type="world",
                scope_key="world",
                context=context,
            ),
            {
                "mode": "external_service_service",
                "key": "external_service:mcp%3Aelyth%2Fmain/timeline%2Fread",
            },
        )
        self.assertEqual(
            service._build_world_state_state_type_hook(
                state_type="external_service",
                context=context,
            ),
            {
                "summary_text": "タイムラインに未読の投稿が2件ある。",
                "summary_source": "capability_result.client_context.mcp_result_summary",
                "signal_fields": [
                    "service",
                    "mcp_server_id",
                    "tool_name",
                ],
                "capability_id": "mcp.call_tool",
                "service": "mcp:elyth/main/timeline/read",
                "mcp_server_id": "mcp:elyth/main",
                "tool_name": "timeline/read",
            },
        )

    def test_failed_or_incomplete_mcp_result_does_not_build_external_service_context(self) -> None:
        service = _WorldStateService()
        base_summary = {
            "capability_id": "mcp.call_tool",
            "status": "completed",
            "is_error": False,
            "error": None,
            "mcp_result_summary": "現在状態の候補",
            "mcp_server_id": "mcp:elyth",
            "tool_name": "get_information",
        }
        invalid_summaries = (
            {**base_summary, "status": "failed", "error": "接続に失敗した。"},
            {**base_summary, "is_error": True},
            {**base_summary, "mcp_result_summary": ""},
            {**base_summary, "tool_name": ""},
        )

        for observation_summary in invalid_summaries:
            with self.subTest(observation_summary=observation_summary):
                context = service._build_world_state_external_service_context(
                    client_context={},
                    observation_summary=observation_summary,
                    source_kind="capability_result",
                )
                self.assertIsNone(context)

    def test_mock_returns_new_contract(self) -> None:
        pack = _source_pack()

        payload = MockLLMClient().generate_world_state({"model": "mock"}, pack)

        validate_world_state_contract(payload, source_pack=pack)
        self.assertLessEqual(len(payload["state_candidates"]), 4)
        for candidate in payload["state_candidates"]:
            self.assertEqual(
                set(candidate),
                {"candidate_ref", "summary_text", "confidence_hint", "salience_hint", "ttl_hint"},
            )

    def test_prompts_describe_candidate_ref_contract(self) -> None:
        prompts = (
            _build_world_state_system_prompt(),
            build_world_state_repair_prompt("invalid"),
        )

        for prompt in prompts:
            self.assertIn("candidate_ref", prompt)
            self.assertIn("source_pack.state_sources", prompt)
            self.assertNotIn("allowed_state_types", prompt)


if __name__ == "__main__":
    unittest.main()
