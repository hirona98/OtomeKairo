import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from otomekairo.defaults import build_default_state
from otomekairo.llm.contracts import LLMError, validate_mcp_inbound_observation_contract
from otomekairo.llm.mock import MockLLMClient
from otomekairo.event_stream import EventStreamRegistry
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.config.resources import ServiceConfigResourcesMixin
from otomekairo.service.config.stream import ServiceConfigStreamMixin
from otomekairo.service.inbound_observation import (
    inbound_observation_delay_seconds,
    inbound_observation_is_due,
    inbound_present_mcp_server_ids,
    inbound_present_mcp_server_ids_from_workspace,
    list_due_inbound_observation_servers,
)
from otomekairo.service.spontaneous.capability_payload import ServiceSpontaneousCapabilityPayloadMixin


class DummyStore:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.autonomous_runs: list[dict] = []

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)

    def list_autonomous_runs(self, *, memory_set_id: str, limit: int) -> list[dict]:
        return deepcopy(self.autonomous_runs[:limit])


class DummyService(
    ServiceConfigStreamMixin,
    ServiceConfigResourcesMixin,
    ServiceSpontaneousCapabilityPayloadMixin,
):
    def __init__(self) -> None:
        self.store = DummyStore()
        self._event_stream_registry = EventStreamRegistry()

    def _now_iso(self) -> str:
        return "2026-08-13T12:00:00+09:00"

    def _clamp(self, value: str, *, limit: int) -> str:
        return value

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class InboundObservationLogicTests(unittest.TestCase):
    def test_due_when_never_attempted(self) -> None:
        self.assertTrue(
            inbound_observation_is_due(
                enabled=True,
                interval_seconds=900,
                last_attempt_at=None,
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_not_due_before_interval(self) -> None:
        self.assertFalse(
            inbound_observation_is_due(
                enabled=True,
                interval_seconds=900,
                last_attempt_at="2026-08-13T11:50:00+09:00",
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_due_after_interval(self) -> None:
        self.assertTrue(
            inbound_observation_is_due(
                enabled=True,
                interval_seconds=900,
                last_attempt_at="2026-08-13T11:40:00+09:00",
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_disabled_is_not_due(self) -> None:
        self.assertFalse(
            inbound_observation_is_due(
                enabled=False,
                interval_seconds=900,
                last_attempt_at=None,
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_list_due_requires_enabled_server_and_background_session(self) -> None:
        servers = {
            "elyth": {
                "mcp_server_id": "elyth",
                "enabled": True,
                "autonomous_session": {
                    "enabled": True,
                    "background_enabled": True,
                    "min_interval_seconds": 86400,
                    "max_tool_calls": 10,
                },
                "inbound_observation": {
                    "enabled": True,
                    "interval_seconds": 900,
                    "tool_name": "get_notifications",
                    "arguments": {},
                },
            },
            "other": {
                "mcp_server_id": "other",
                "enabled": True,
                "autonomous_session": {
                    "enabled": True,
                    "background_enabled": False,
                    "min_interval_seconds": 900,
                    "max_tool_calls": 10,
                },
                "inbound_observation": {
                    "enabled": True,
                    "interval_seconds": 900,
                    "tool_name": "get_notifications",
                    "arguments": {},
                },
            },
        }
        due = list_due_inbound_observation_servers(
            mcp_servers=servers,
            last_attempt_at_by_id={},
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual([item["mcp_server_id"] for item in due], ["elyth"])

    def test_delay_is_zero_when_never_attempted(self) -> None:
        delay = inbound_observation_delay_seconds(
            mcp_servers={
                "elyth": {
                    "mcp_server_id": "elyth",
                    "enabled": True,
                    "autonomous_session": {
                        "enabled": True,
                        "background_enabled": True,
                        "min_interval_seconds": 86400,
                        "max_tool_calls": 10,
                    },
                    "inbound_observation": {
                        "enabled": True,
                        "interval_seconds": 900,
                        "tool_name": "get_notifications",
                        "arguments": {},
                    },
                }
            },
            last_attempt_at_by_id={},
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(delay, 0.0)

    def test_present_ids_ignore_failed_or_empty(self) -> None:
        present = inbound_present_mcp_server_ids(
            [
                {
                    "mcp_server_id": "elyth",
                    "inbound_present": True,
                },
                {
                    "mcp_server_id": "other",
                    "inbound_present": False,
                },
                {
                    "mcp_server_id": "failed",
                    "status": "failed",
                },
            ]
        )
        self.assertEqual(present, ["elyth"])

    def test_workspace_present_ids(self) -> None:
        present = inbound_present_mcp_server_ids_from_workspace(
            {
                "workspace_candidates": [
                    {
                        "kind": "inbound_observation",
                        "metadata": {"mcp_server_id": "elyth"},
                    },
                    {
                        "kind": "standing_concern",
                        "metadata": {"concern_id": "elyth"},
                    },
                ]
            }
        )
        self.assertEqual(present, ["elyth"])


class InboundObservationContractTests(unittest.TestCase):
    def test_contract_accepts_exact_shape(self) -> None:
        payload = {
            "inbound_present": False,
            "observation_summary": "届いている働きかけは見当たらない。",
            "reason_summary": "未読はない。",
        }
        validate_mcp_inbound_observation_contract(payload)

    def test_contract_rejects_extra_key(self) -> None:
        with self.assertRaises(LLMError):
            validate_mcp_inbound_observation_contract(
                {
                    "inbound_present": True,
                    "observation_summary": "返信がある。",
                    "reason_summary": "未読の返信がある。",
                    "extra": True,
                }
            )

    def test_mock_treats_empty_result_as_absent(self) -> None:
        payload = MockLLMClient().generate_mcp_inbound_observation(
            {"model": "mock-test"},
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
                "is_error": False,
                "content": [],
                "structured_content": None,
            },
        )
        self.assertFalse(payload["inbound_present"])

    def test_mock_treats_nonempty_result_as_present(self) -> None:
        payload = MockLLMClient().generate_mcp_inbound_observation(
            {"model": "mock-test"},
            {
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
                "is_error": False,
                "content": [{"type": "text", "text": "reply from someone"}],
                "structured_content": None,
            },
        )
        self.assertTrue(payload["inbound_present"])


class InboundObservationSessionTests(unittest.TestCase):
    def _configure_elyth(self, service: DummyService) -> None:
        service.store.state["mcp_servers"] = {
            "elyth": {
                "mcp_server_id": "elyth",
                "connector_kind": "mcp_client",
                "client_id": "mcp-client-connector-main",
                "enabled": True,
                "pre_send_check_enabled": True,
                "transport": "streamable_http",
                "url": "https://elythworld.com/api/mcp/remote",
                "headers": {"Authorization": "Bearer test-token"},
                "autonomous_session": {
                    "enabled": True,
                    "background_enabled": True,
                    "min_interval_seconds": 86400,
                    "max_tool_calls": 10,
                },
                "inbound_observation": {
                    "enabled": True,
                    "interval_seconds": 900,
                    "tool_name": "get_notifications",
                    "arguments": {},
                },
            }
        }

    def _register_catalog(self, service: DummyService) -> None:
        class DummyWebSocket:
            def close(self) -> None:
                return None

        session_id = service.register_event_stream_connection(DummyWebSocket())
        service.handle_event_stream_message(
            session_id,
            {
                "type": "hello",
                "client_id": "mcp-client-connector-main",
                "client_kind": "capability_connector",
                "caps": [{"id": "mcp.call_tool", "version": "1"}],
                "mcp_servers": [
                    {
                        "mcp_server_id": "elyth",
                        "transport": "streamable_http",
                        "tools": [
                            {
                                "name": "get_notifications",
                                "description": "通知を取得する",
                                "inputSchema": {"type": "object"},
                            }
                        ],
                    }
                ],
            },
        )

    def test_cooldown_blocks_without_inbound(self) -> None:
        service = DummyService()
        self._configure_elyth(service)
        self._register_catalog(service)
        service.store.autonomous_runs = [
            {
                "run_id": "autonomous_run:recent",
                "status": "completed",
                "created_at": "2026-08-13T11:00:00+09:00",
                "mcp_session": {"mcp_server_id": "elyth"},
            }
        ]
        servers = service._inspection_mcp_servers(
            service._event_stream_registry.list_capability_bindings()["mcp_servers"],
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(
            service._finite_mcp_session_targets(
                mcp_servers=servers,
                trigger_kind="background_thinking",
            ),
            [],
        )

    def test_inbound_present_bypasses_cooldown_for_targets(self) -> None:
        service = DummyService()
        self._configure_elyth(service)
        self._register_catalog(service)
        service.store.autonomous_runs = [
            {
                "run_id": "autonomous_run:recent",
                "status": "completed",
                "created_at": "2026-08-13T11:00:00+09:00",
                "mcp_session": {"mcp_server_id": "elyth"},
            }
        ]
        servers = service._inspection_mcp_servers(
            service._event_stream_registry.list_capability_bindings()["mcp_servers"],
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(
            service._finite_mcp_session_targets(
                mcp_servers=servers,
                trigger_kind="background_thinking",
                inbound_present_mcp_server_ids=["elyth"],
            ),
            [{"mcp_server_id": "elyth", "active_run_ids": []}],
        )

    def test_background_eligible_with_inbound_ignores_interval(self) -> None:
        service = DummyService()
        self._configure_elyth(service)
        service.store.autonomous_runs = [
            {
                "run_id": "autonomous_run:recent",
                "status": "completed",
                "created_at": "2026-08-13T11:00:00+09:00",
                "mcp_session": {"mcp_server_id": "elyth"},
            }
        ]
        self.assertFalse(
            service._mcp_background_session_eligible(
                mcp_server_id="elyth",
                policy={
                    "enabled": True,
                    "background_enabled": True,
                    "min_interval_seconds": 86400,
                },
                current_time="2026-08-13T12:00:00+09:00",
            )
        )
        self.assertTrue(
            service._mcp_background_session_eligible(
                mcp_server_id="elyth",
                policy={
                    "enabled": True,
                    "background_enabled": True,
                    "min_interval_seconds": 86400,
                },
                current_time="2026-08-13T12:00:00+09:00",
                inbound_present=True,
            )
        )


class InboundObservationRuntimeTests(unittest.TestCase):
    def test_start_session_from_inbound_despite_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["mcp_servers"]["elyth"]["enabled"] = True
            state["mcp_servers"]["elyth"]["autonomous_session"]["min_interval_seconds"] = 86400
            service.store.write_state(state)
            service.store.upsert_autonomous_run(
                autonomous_run={
                    "run_id": "autonomous_run:recent",
                    "memory_set_id": state["selected_memory_set_id"],
                    "status": "completed",
                    "objective_summary": "前回の巡回。",
                    "origin_kind": "background_thinking",
                    "current_step_summary": "完了。",
                    "history_summary": "",
                    "next_run_at": None,
                    "waiting_request_id": None,
                    "pause_reason": None,
                    "created_at": "2026-08-13T11:00:00+09:00",
                    "updated_at": "2026-08-13T11:05:00+09:00",
                    "completed_at": "2026-08-13T11:05:00+09:00",
                    "mcp_session": {
                        "mcp_server_id": "elyth",
                        "policy": state["mcp_servers"]["elyth"]["autonomous_session"],
                        "tool_call_count": 3,
                    },
                }
            )
            service._execute_autonomous_run_step = lambda **_: {"status": "ok"}
            started = service._start_autonomous_run_from_decision(
                state=state,
                current_time="2026-08-13T12:00:00+09:00",
                decision={
                    "autonomous_run": {
                        "objective_summary": "届いた返信を返す。",
                        "initial_step_summary": "通知を読む。",
                        "mcp_server_id": "elyth",
                        "coordination": {
                            "mode": "create_new",
                            "target_run_ids": [],
                            "reason_summary": "inbound",
                        },
                    }
                },
                source_current_input={
                    "sender_kind": "system",
                    "source_kind": "background_thinking",
                    "text": "向こうから届いている働きかけがある。",
                    "inbound_present_mcp_server_ids": ["elyth"],
                },
                source_cycle_id="cycle:test",
                assistant_message_target_client_id=None,
            )
            self.assertEqual(started["autonomous_run"]["mcp_session"]["mcp_server_id"], "elyth")
            self.assertEqual(started["autonomous_run"]["status"], "active")

    def test_start_session_without_inbound_still_respects_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["mcp_servers"]["elyth"]["enabled"] = True
            state["mcp_servers"]["elyth"]["autonomous_session"]["min_interval_seconds"] = 86400
            service.store.write_state(state)
            service.store.upsert_autonomous_run(
                autonomous_run={
                    "run_id": "autonomous_run:recent",
                    "memory_set_id": state["selected_memory_set_id"],
                    "status": "completed",
                    "objective_summary": "前回の巡回。",
                    "origin_kind": "background_thinking",
                    "current_step_summary": "完了。",
                    "history_summary": "",
                    "next_run_at": None,
                    "waiting_request_id": None,
                    "pause_reason": None,
                    "created_at": "2026-08-13T11:00:00+09:00",
                    "updated_at": "2026-08-13T11:05:00+09:00",
                    "completed_at": "2026-08-13T11:05:00+09:00",
                    "mcp_session": {
                        "mcp_server_id": "elyth",
                        "policy": state["mcp_servers"]["elyth"]["autonomous_session"],
                        "tool_call_count": 3,
                    },
                }
            )
            with self.assertRaisesRegex(ValueError, "Background finite MCP session is not eligible"):
                service._start_autonomous_run_from_decision(
                    state=state,
                    current_time="2026-08-13T12:00:00+09:00",
                    decision={
                        "autonomous_run": {
                            "objective_summary": "ELYTH を見る。",
                            "initial_step_summary": "見る。",
                            "mcp_server_id": "elyth",
                            "coordination": {
                                "mode": "create_new",
                                "target_run_ids": [],
                                "reason_summary": "browse",
                            },
                        }
                    },
                    source_current_input={
                        "sender_kind": "system",
                        "source_kind": "background_thinking",
                        "text": "定期思考。",
                    },
                    source_cycle_id="cycle:test",
                    assistant_message_target_client_id=None,
                )

    def test_scheduled_thinking_skips_when_inbound_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["mcp_servers"]["elyth"]["enabled"] = True
            service.store.write_state(state)
            with service._runtime_state_lock:
                service._wake_runtime_state["last_wake_at"] = "2026-08-13T11:59:00+09:00"
            called = []

            def fake_run(**kwargs):
                called.append(kwargs)

            service._due_inbound_observation_servers = lambda **_: [state["mcp_servers"]["elyth"]]
            service._run_due_inbound_observations = lambda **_: [
                {
                    "mcp_server_id": "elyth",
                    "tool_name": "get_notifications",
                    "status": "observed",
                    "inbound_present": False,
                    "observation_summary": "届いている働きかけは見当たらない。",
                    "reason_summary": "未読はない。",
                }
            ]
            service._execute_wake_cycle = fake_run
            service._execute_scheduled_background_thinking(state=state)
            self.assertEqual(called, [])

    def test_scheduled_thinking_opens_inbound_only_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["mcp_servers"]["elyth"]["enabled"] = True
            service.store.write_state(state)
            with service._runtime_state_lock:
                service._wake_runtime_state["last_wake_at"] = "2026-08-13T11:59:00+09:00"
            called = []

            def fake_run(**kwargs):
                called.append(kwargs)

            service._due_inbound_observation_servers = lambda **_: [state["mcp_servers"]["elyth"]]
            service._run_due_inbound_observations = lambda **_: [
                {
                    "mcp_server_id": "elyth",
                    "tool_name": "get_notifications",
                    "status": "observed",
                    "inbound_present": True,
                    "observation_summary": "返信が届いている。",
                    "reason_summary": "未読の返信がある。",
                }
            ]
            service._execute_wake_cycle = fake_run
            service._execute_scheduled_background_thinking(state=state)
            self.assertEqual(len(called), 1)
            client_context = called[0]["client_context"]
            self.assertEqual(client_context["inbound_present_mcp_server_ids"], ["elyth"])
            self.assertTrue(client_context["inbound_only"])


if __name__ == "__main__":
    unittest.main()
