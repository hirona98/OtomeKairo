import tempfile
import threading
import unittest
from pathlib import Path

from otomekairo.defaults import (
    DEFAULT_ELYTH_PERIODIC_THOUGHT_TOPIC_SUMMARY,
    build_default_periodic_thought_topics,
)
from otomekairo.llm.contexts import CurrentInput
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.periodic_thought_topics import (
    build_periodic_thought_topic_orientation_context,
    list_due_periodic_thought_topics,
    selected_periodic_thought_topic_ids,
    periodic_thought_topic_ids_from_runs,
    periodic_thought_topic_is_due,
)
from otomekairo.store.file_store import FileStore


class PeriodicThoughtTopicLogicTests(unittest.TestCase):
    def test_orientation_context_projects_only_factor_and_summary(self) -> None:
        context = build_periodic_thought_topic_orientation_context(
            [
                {
                    "topic_id": "elyth",
                    "enabled": True,
                    "min_periodic_thinking_interval_seconds": 3600,
                    "topic_summary": DEFAULT_ELYTH_PERIODIC_THOUGHT_TOPIC_SUMMARY,
                }
            ]
        )

        self.assertEqual(
            context,
            {
                "periodic_thought_topics": [
                    {
                        "factor_ref": "periodic_thought_topic:elyth",
                        "summary_text": DEFAULT_ELYTH_PERIODIC_THOUGHT_TOPIC_SUMMARY,
                    }
                ]
            },
        )

    def test_due_when_never_attended(self) -> None:
        self.assertTrue(
            periodic_thought_topic_is_due(
                enabled=True,
                min_periodic_thinking_interval_seconds=3600,
                last_attended_at=None,
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_not_due_before_interval(self) -> None:
        self.assertFalse(
            periodic_thought_topic_is_due(
                enabled=True,
                min_periodic_thinking_interval_seconds=3600,
                last_attended_at="2026-08-13T11:30:00+09:00",
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_due_after_interval(self) -> None:
        self.assertTrue(
            periodic_thought_topic_is_due(
                enabled=True,
                min_periodic_thinking_interval_seconds=3600,
                last_attended_at="2026-08-13T10:00:00+09:00",
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_disabled_is_not_due(self) -> None:
        self.assertFalse(
            periodic_thought_topic_is_due(
                enabled=False,
                min_periodic_thinking_interval_seconds=3600,
                last_attended_at=None,
                current_time="2026-08-13T12:00:00+09:00",
            )
        )

    def test_list_due_filters_disabled_and_recent(self) -> None:
        due = list_due_periodic_thought_topics(
            topics=[
                {
                    "topic_id": "elyth",
                    "enabled": True,
                    "min_periodic_thinking_interval_seconds": 3600,
                    "topic_summary": "ELYTH",
                },
                {
                    "topic_id": "other",
                    "enabled": True,
                    "min_periodic_thinking_interval_seconds": 3600,
                    "topic_summary": "other",
                },
            ],
            last_attended_at_by_id={"other": "2026-08-13T11:30:00+09:00"},
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual([item["topic_id"] for item in due], ["elyth"])

    def test_active_run_hides_elapsed_topic(self) -> None:
        due = list_due_periodic_thought_topics(
            topics=[
                {
                    "topic_id": "elyth",
                    "enabled": True,
                    "min_periodic_thinking_interval_seconds": 60,
                    "topic_summary": "ELYTH",
                }
            ],
            last_attended_at_by_id={"elyth": "2026-08-13T10:00:00+09:00"},
            current_time="2026-08-13T12:00:00+09:00",
            active_topic_ids={"elyth"},
        )
        self.assertEqual(due, [])

    def test_ids_from_runs_keep_non_empty_strings(self) -> None:
        self.assertEqual(
            periodic_thought_topic_ids_from_runs(
                [
                    {"periodic_thought_topic_ids": [" elyth ", ""]},
                    {"periodic_thought_topic_ids": "elyth"},
                    {},
                ]
            ),
            {"elyth"},
        )

    def test_attendance_requires_action_and_factor_selection(self) -> None:
        workspace = {
            "workspace_candidates": [
                {
                    "factor_ref": "periodic_thought_topic:elyth",
                    "kind": "periodic_thought_topic",
                    "metadata": {"topic_id": "elyth"},
                }
            ]
        }
        self.assertEqual(
            selected_periodic_thought_topic_ids(
                decision={
                    "kind": "autonomous_run",
                    "foreground_selection": {"primary_factor_ref": "periodic_thought_topic:elyth"},
                },
                workspace_context=workspace,
            ),
            ["elyth"],
        )
        self.assertEqual(
            selected_periodic_thought_topic_ids(
                decision={
                    "kind": "noop",
                    "foreground_selection": {"primary_factor_ref": "periodic_thought_topic:elyth"},
                },
                workspace_context=workspace,
            ),
            [],
        )
        self.assertEqual(
            selected_periodic_thought_topic_ids(
                decision={
                    "kind": "capability_request",
                    "foreground_selection": {"primary_factor_ref": "drive_state:x"},
                },
                workspace_context=workspace,
            ),
            [],
        )
        self.assertEqual(
            selected_periodic_thought_topic_ids(
                decision={
                    "kind": "speech",
                    "foreground_selection": {
                        "primary_factor_ref": "visual_observation_signal:desktop",
                    },
                    "separated_comparisons": {
                        "self_activity": {
                            "kind": "capability_request",
                            "foreground_selection": {
                                "primary_factor_ref": "periodic_thought_topic:elyth",
                                "supporting_factor_refs": [],
                            },
                        }
                    },
                },
                workspace_context=workspace,
            ),
            ["elyth"],
        )


class PeriodicThoughtTopicWorkspaceTests(unittest.TestCase):
    def test_skill_orientation_uses_due_topics_only_for_self_initiated_cycles(self) -> None:
        service = ServiceInputPipelineMixin()
        due = [
            {
                "topic_id": "elyth",
                "enabled": True,
                "min_periodic_thinking_interval_seconds": 3600,
                "topic_summary": DEFAULT_ELYTH_PERIODIC_THOUGHT_TOPIC_SUMMARY,
            }
        ]

        self.assertEqual(
            service._build_agent_skill_orientation_context(
                trigger_kind="background_thinking",
                due_periodic_thought_topics=due,
            )["periodic_thought_topics"][0]["summary_text"],
            DEFAULT_ELYTH_PERIODIC_THOUGHT_TOPIC_SUMMARY,
        )
        self.assertEqual(
            service._build_agent_skill_orientation_context(
                trigger_kind="user_message",
                due_periodic_thought_topics=due,
            ),
            {"periodic_thought_topics": []},
        )

    def test_workspace_includes_due_periodic_thought_topic(self) -> None:
        for source_kind in ("wake", "background_thinking"):
            with self.subTest(source_kind=source_kind):
                payload = self._workspace_with_due_topic("system", source_kind)
                topics = [
                    candidate for candidate in payload["workspace_candidates"]
                    if candidate["kind"] == "periodic_thought_topic"
                ]
                self.assertEqual(len(topics), 1)
                self.assertEqual(topics[0]["factor_ref"], "periodic_thought_topic:elyth")
                self.assertEqual(topics[0]["summary_text"], DEFAULT_ELYTH_PERIODIC_THOUGHT_TOPIC_SUMMARY)

    def test_conversation_and_results_do_not_foreground_due_topics(self) -> None:
        for sender_kind, source_kind in (
            ("person", "user_message"),
            ("capability", "capability_result"),
            ("capability", "background_thinking"),
        ):
            with self.subTest(sender_kind=sender_kind, source_kind=source_kind):
                payload = self._workspace_with_due_topic(sender_kind, source_kind)
                self.assertFalse(any(
                    candidate["kind"] == "periodic_thought_topic"
                    for candidate in payload["workspace_candidates"]
                ))
                self.assertTrue(any(
                    candidate["kind"] == "current_input"
                    for candidate in payload["workspace_candidates"]
                ))

    def _workspace_with_due_topic(self, sender_kind, source_kind):
        service = ServiceInputPipelineMixin()
        return service._build_workspace_context(
            current_input=CurrentInput(
                sender_kind=sender_kind,
                sender_ref=None,
                source_kind=source_kind,
                response_target_refs=(),
                interaction_context=None,
                text="定期思考。",
            ),
            due_periodic_thought_topics=[
                {
                    "topic_id": "elyth",
                    "enabled": True,
                    "min_periodic_thinking_interval_seconds": 3600,
                    "topic_summary": DEFAULT_ELYTH_PERIODIC_THOUGHT_TOPIC_SUMMARY,
                }
            ],
            recall_pack={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            initiative_context=None,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
        )


class PeriodicThoughtTopicStoreTests(unittest.TestCase):
    def test_active_run_removes_topic_from_due_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["periodic_thought_topics"] = [
                {
                    "topic_id": "elyth",
                    "enabled": True,
                    "min_periodic_thinking_interval_seconds": 60,
                    "topic_summary": "ELYTH",
                }
            ]
            current_time = "2026-08-13T12:00:00+09:00"
            self.assertEqual(
                [item["topic_id"] for item in service._due_periodic_thought_topics(state=state, current_time=current_time)],
                ["elyth"],
            )
            service.store.upsert_autonomous_run(
                autonomous_run={
                    "run_id": "autonomous_run:elyth-open",
                    "memory_set_id": state["selected_memory_set_id"],
                    "status": "active",
                    "periodic_thought_topic_ids": ["elyth"],
                    "created_at": current_time,
                    "updated_at": current_time,
                }
            )
            self.assertEqual(service._due_periodic_thought_topics(state=state, current_time=current_time), [])
            service.store.upsert_autonomous_run(
                autonomous_run={
                    "run_id": "autonomous_run:elyth-open",
                    "memory_set_id": state["selected_memory_set_id"],
                    "status": "completed",
                    "periodic_thought_topic_ids": ["elyth"],
                    "created_at": current_time,
                    "updated_at": current_time,
                    "completed_at": current_time,
                }
            )
            self.assertEqual(
                [item["topic_id"] for item in service._due_periodic_thought_topics(state=state, current_time=current_time)],
                ["elyth"],
            )

    def test_new_store_has_default_periodic_thought_topics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            state = store.read_state()
            self.assertEqual(state["periodic_thought_topics"], build_default_periodic_thought_topics())


class PeriodicThoughtTopicAttendanceMixinTests(unittest.TestCase):
    def test_mark_attended_updates_runtime_state(self) -> None:
        from otomekairo.service.input.periodic_thought_topic import ServiceInputPeriodicThoughtTopicMixin

        class Subject(ServiceInputPeriodicThoughtTopicMixin):
            def __init__(self) -> None:
                self._runtime_state_lock = threading.RLock()
                self._wake_runtime_state = {"periodic_thought_topic_last_attended_at": {}}

        subject = Subject()
        marked = subject._mark_periodic_thought_topics_attended(
            decision={
                "kind": "capability_request",
                "foreground_selection": {
                    "primary_factor_ref": "periodic_thought_topic:elyth",
                    "supporting_factor_refs": [],
                },
            },
            workspace_context={
                "workspace_candidates": [
                    {
                        "factor_ref": "periodic_thought_topic:elyth",
                        "kind": "periodic_thought_topic",
                        "metadata": {"topic_id": "elyth"},
                    }
                ]
            },
            current_time="2026-08-13T12:00:00+09:00",
        )
        self.assertEqual(marked, ["elyth"])
        self.assertEqual(
            subject._periodic_thought_topic_last_attended_map()["elyth"],
            "2026-08-13T12:00:00+09:00",
        )


if __name__ == "__main__":
    unittest.main()
