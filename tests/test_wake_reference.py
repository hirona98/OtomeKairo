from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from otomekairo.service.common import ServiceError
from otomekairo.service.input.wake_reference import (
    ServiceInputWakeReferenceMixin,
    WAKE_REFERENCE_TEXT_FIELD_MAX_CHARS,
    WAKE_REFERENCE_TEXT_MAX_BYTES,
)
from otomekairo.service.input.wake_pipeline import ServiceInputWakePipelineMixin


class DummyWakeReferenceService(ServiceInputWakeReferenceMixin):
    pass


class DummyImmediateWakePipeline(ServiceInputWakePipelineMixin):
    def __init__(self) -> None:
        self.due_called = False
        self.input_pipeline_called = False
        self.last_wake_at = None

    def _debug_cycle_label(self, cycle_id: str | None) -> str:
        return cycle_id or "cycle:test"

    def _build_wake_input_text(
        self,
        *,
        state: dict,
        client_context: dict,
        selected_candidate: dict | None,
    ) -> str:
        _ = state, client_context, selected_candidate
        return "wake input"

    def _wake_is_due(self, *, state: dict, current_time: str) -> dict:
        _ = state, current_time
        self.due_called = True
        return {"should_skip": True, "reason_summary": "not due"}

    def _clamp(self, value: object, limit: int = 200) -> str:
        _ = limit
        return str(value)

    def _noop_pipeline(self, *, state: dict, started_at: str, reason_summary: str) -> dict:
        _ = state, started_at
        return {"decision": {"kind": "noop"}, "reason_summary": reason_summary}

    def _run_wake_policy_observations(
        self,
        *,
        state: dict,
        started_at: str,
        client_context: dict,
        cycle_id: str | None,
    ) -> dict:
        _ = state, started_at, cycle_id
        return client_context

    def _user_response_cycle_active(self) -> bool:
        return False

    def _recent_turns_added_since(self, *, state: dict, started_at: str) -> bool:
        _ = state, started_at
        return False

    def _run_autonomous_initiative_entry_check(
        self,
        *,
        state: dict,
        current_time: str,
        trigger_kind: str,
        client_context: dict,
        recent_turns: list[dict],
        cycle_id: str | None,
    ) -> dict:
        _ = state, current_time, trigger_kind, recent_turns, cycle_id
        return client_context

    def _has_autonomous_initiative_context(
        self,
        *,
        state: dict,
        current_time: str,
        client_context: dict | None = None,
    ) -> bool:
        _ = state, current_time, client_context
        return True

    def _set_last_wake_at(self, current_time: str) -> None:
        self.last_wake_at = current_time

    def _set_wake_retry_after(self, current_time: str) -> None:
        _ = current_time

    def _client_context_has_retryable_wake_observation_failure(self, client_context: dict | None) -> bool:
        _ = client_context
        return False

    def _initiative_entry_check_skip_reason(self, client_context: dict | None) -> str | None:
        _ = client_context
        return None

    def _was_recently_replied(self, *, dedupe_key: str, current_time: str) -> bool:
        _ = dedupe_key, current_time
        return False

    def _run_input_pipeline(self, **kwargs: object) -> dict:
        self.input_pipeline_called = True
        return {
            "decision": {"kind": "noop"},
            "trigger_kind": kwargs.get("trigger_kind"),
            "reference_context": kwargs.get("reference_context"),
        }


class WakeReferenceTests(unittest.TestCase):
    def test_resolves_local_text_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.txt"
            path.write_text("軽量CVが変化を検出した。\n画面の右側に通知が出ている。", encoding="utf-8")
            service = DummyWakeReferenceService()

            resolution = service._resolve_wake_reference(
                reference_payload={
                    "uri": str(path),
                    "label": "差分スナップショット",
                    "reason_summary": "前回との差分が大きい。",
                    "content_hint": "auto",
                },
                resolved_at="2026-07-05T12:00:00+09:00",
            )

            self.assertEqual(resolution.content_kind, "text")
            self.assertEqual(resolution.text, "軽量CVが変化を検出した。\n画面の右側に通知が出ている。")
            self.assertIsNone(resolution.image_data_uri)
            self.assertEqual(resolution.summary["uri"], str(path))
            self.assertEqual(resolution.summary["label"], "差分スナップショット")
            self.assertNotIn("通知が出ている", str(resolution.summary))

    def test_resolves_png_reference_by_magic_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.bin"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"image-bytes")
            service = DummyWakeReferenceService()

            resolution = service._resolve_wake_reference(
                reference_payload={"uri": str(path), "content_hint": "auto"},
                resolved_at="2026-07-05T12:00:00+09:00",
            )

            self.assertEqual(resolution.content_kind, "image")
            self.assertIsNone(resolution.text)
            self.assertIsNotNone(resolution.image_data_uri)
            assert resolution.image_data_uri is not None
            self.assertTrue(resolution.image_data_uri.startswith("data:image/png;base64,"))
            self.assertEqual(resolution.summary["media_type"], "image/png")

    def test_svg_reference_auto_is_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.svg"
            path.write_text("<svg><text>変化あり</text></svg>", encoding="utf-8")
            service = DummyWakeReferenceService()

            resolution = service._resolve_wake_reference(
                reference_payload={"uri": str(path), "content_hint": "auto"},
                resolved_at="2026-07-05T12:00:00+09:00",
            )

            self.assertEqual(resolution.content_kind, "text")
            self.assertEqual(resolution.text, "<svg><text>変化あり</text></svg>")
            self.assertIsNone(resolution.image_data_uri)

    def test_missing_file_returns_unavailable_error(self) -> None:
        service = DummyWakeReferenceService()

        with self.assertRaises(ServiceError) as raised:
            service._resolve_wake_reference(
                reference_payload={"uri": "/tmp/otomekairo-missing-reference.txt"},
                resolved_at="2026-07-05T12:00:00+09:00",
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.error_code, "wake_reference_unavailable")

    def test_large_text_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.txt"
            path.write_bytes(b"a" * (WAKE_REFERENCE_TEXT_MAX_BYTES + 1))
            service = DummyWakeReferenceService()

            with self.assertRaises(ServiceError) as raised:
                service._resolve_wake_reference(
                    reference_payload={"uri": str(path), "content_hint": "text"},
                    resolved_at="2026-07-05T12:00:00+09:00",
                )

            self.assertEqual(raised.exception.status_code, 413)
            self.assertEqual(raised.exception.error_code, "wake_reference_too_large")

    def test_invalid_content_hint_is_rejected(self) -> None:
        service = DummyWakeReferenceService()

        with self.assertRaises(ServiceError) as raised:
            service._resolve_wake_reference(
                reference_payload={"uri": "/tmp/reference.txt", "content_hint": "binary"},
                resolved_at="2026-07-05T12:00:00+09:00",
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.error_code, "invalid_wake_reference")

    def test_long_reference_label_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.txt"
            path.write_text("変化あり", encoding="utf-8")
            service = DummyWakeReferenceService()

            with self.assertRaises(ServiceError) as raised:
                service._resolve_wake_reference(
                    reference_payload={
                        "uri": str(path),
                        "label": "a" * (WAKE_REFERENCE_TEXT_FIELD_MAX_CHARS + 1),
                    },
                    resolved_at="2026-07-05T12:00:00+09:00",
                )

            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(raised.exception.error_code, "invalid_wake_reference")

    def test_api_wake_ignores_wake_policy_due_gate(self) -> None:
        service = DummyImmediateWakePipeline()

        pipeline, input_text, _ = service._run_wake_pipeline(
            state={"wake_policy": {"mode": "disabled"}},
            started_at="2026-07-05T12:00:00+09:00",
            trigger_kind="wake",
            client_context={},
            recent_turns=[],
            selected_candidate={"candidate_id": "candidate:test", "dedupe_key": "dedupe:test"},
            reference_context={"summary": {"uri": "/tmp/reference.txt"}},
        )

        self.assertFalse(service.due_called)
        self.assertTrue(service.input_pipeline_called)
        self.assertEqual(service.last_wake_at, "2026-07-05T12:00:00+09:00")
        self.assertEqual(input_text, "wake input")
        self.assertEqual(pipeline["trigger_kind"], "wake")
        self.assertEqual(pipeline["reference_context"], {"summary": {"uri": "/tmp/reference.txt"}})
