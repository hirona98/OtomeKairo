import tempfile
import unittest
from contextlib import closing
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from otomekairo.llm.contexts import CurrentInput
from otomekairo.llm.contracts import LLMError
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.autonomous_run import AUTONOMOUS_PRE_SEND_CHECK_RETRY_FEEDBACK
from otomekairo.service.autonomous_run import AUTONOMOUS_COMPLETION_REVIEW_RETRY_FEEDBACK
from otomekairo.service.capability import PreSendCheckWithheldError


class AutonomousRunRecoveryTests(unittest.TestCase):
    def test_scheduled_step_and_delivery_share_saved_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            service._build_agent_skill_context = Mock(return_value=None)
            context = service._build_autonomous_step_context(
                state=state, run=run, current_time="2026-06-20T11:03:00+09:00",
                source_current_input=None, last_result_context=None, step_trigger="timer",
            )
            self.assertEqual(context.current_input.sender_kind, "system")
            self.assertEqual(context.current_input.source_kind, "autonomous_run")
            self.assertEqual(context.current_input.response_target_refs, ("person:test",))
            self.assertEqual(context.current_input.interaction_context.interaction_ref, "interaction:test")
            self.assertEqual(context.current_input.interaction_context.participants[0].display_name, "テスト人物")
            service._emit_assistant_message_with_audio = Mock(return_value=(True, None))
            service._emit_autonomous_run_assistant_message_event(
                state=state, run=run, speech_payload={"speech_text": "時間の確認です。"},
            )
            wire = service._emit_assistant_message_with_audio.call_args.kwargs["event_data"]
            self.assertEqual(tuple(wire["recipient_person_refs"]), context.current_input.response_target_refs)
            self.assertEqual(wire["interaction_ref"], context.current_input.interaction_context.interaction_ref)

    def test_run_speech_rejects_inconsistent_saved_delivery_route(self) -> None:
        for change in (
            {"origin_interaction_ref": "interaction:other"},
            {"participant_refs": ["person:other"]},
            {"participant_refs": []},
            {"source_current_input": {}},
            {"source_current_input": None},
            {"origin_interaction_ref": None},
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp_dir:
                service = OtomeKairoService(Path(temp_dir))
                state = service.store.read_state()
                run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
                run.update(change)
                service._emit_assistant_message_with_audio = Mock()
                with self.assertRaises(ValueError):
                    service._emit_autonomous_run_assistant_message_event(
                        state=state, run=run, speech_payload={"speech_text": "時間の確認です。"},
                    )
                service._emit_assistant_message_with_audio.assert_not_called()

    def test_self_run_has_no_person_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            service._emit_assistant_message_with_audio = Mock()
            run = {"source_current_input": {"sender_kind": "system", "source_kind": "wake"},
                   "origin_interaction_ref": None, "participant_refs": []}
            self.assertIsNone(service._autonomous_run_speech_interaction(run))
            service._emit_autonomous_run_assistant_message_event(
                state={}, run=run, speech_payload={"speech_text": "少し落ち着きました。"},
            )
            service._emit_assistant_message_with_audio.assert_not_called()

    def test_current_run_context_keeps_active_work_and_recent_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            for index in range(7):
                run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"], status="cancelled")
                run.update(run_id=f"autonomous_run:cancelled-{index}",
                           created_at=f"2026-10-04T09:00:0{index}+09:00", updated_at=f"2026-10-04T09:00:0{index}+09:00")
                service.store.upsert_autonomous_run(autonomous_run=run)
            active = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"], status="waiting_timer")
            active["run_id"] = "autonomous_run:long-wait"
            service.store.upsert_autonomous_run(autonomous_run=active)
            summaries = service._list_autonomous_run_prompt_summaries(state=state, current_time="2026-10-04T10:00:00+09:00")
            self.assertEqual({r["run_id"] for r in summaries}, {active["run_id"], *[f"autonomous_run:cancelled-{i}" for i in range(2, 7)]})
            self.assertEqual(len([r for r in summaries if r["status"] == "cancelled"]), 5)

    def test_completion_context_keeps_the_current_requests_origin_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._commitment_run_record(memory_set_id="memory_set:default")
            run.update({
                "run_id": "autonomous_run:new-request",
                "source_cycle_id": "cycle:new-request",
                "created_at": "2026-10-03T09:05:28+09:00",
                "next_run_at": "2026-10-03T09:05:28+09:00",
                "history_summary": "",
            })
            context = service._autonomous_run_prompt_summary(run)
            context["recent_turns"] = [{"run_id": "autonomous_run:previous", "status": "completed"}]
            review = service._autonomous_completion_review_run_context(context)
            self.assertEqual(review["run_id"], run["run_id"])
            self.assertEqual(review["source_cycle_id"], run["source_cycle_id"])
            self.assertEqual(review["created_at"], run["created_at"])
            self.assertEqual(review["source_started_at"], run["source_started_at"])
            self.assertEqual(review["next_run_at"], run["next_run_at"])
            self.assertEqual(review["source_current_input"], run["source_current_input"])
            self.assertEqual(review["history_summary"], "")
            self.assertNotIn("recent_turns", review)
            review["source_current_input"]["text"] = "changed"
            self.assertEqual(run["source_current_input"]["text"], "3分後に声をかけて")

    def test_autonomous_speech_receives_run_objective(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            service._build_speech_context = Mock(return_value=object())
            service._build_selected_persona_context = Mock(return_value=object())
            service.llm = Mock()
            service.llm.generate_speech.return_value = {"speech_text": "そろそろ作業に戻りましょう。"}
            objective = "読書を終えて作業に戻るよう声をかける。"
            step_context = SimpleNamespace(
                capability_decision_view=[], affect_context={"mood_state": {"current_vad": {"v": 0.4, "a": 0.1, "d": 0.2}}},
                self_state_context={"agency_confidence": []}, drive_state_summary=[],
                run={"objective_summary": objective, "observed_result_summaries": [
                    {"tool_name": "create_reply", "summary_text": "返信成功。" * 100}]},
                current_input=CurrentInput(
                    sender_kind="system", sender_ref="self", source_kind="autonomous_run",
                    response_target_refs=("person:master",), interaction_context=None,
                    text="タイマーが満了した。",
                ),
                recent_turns=[], time_context={}, foreground_world_state=None,
                activity_context=None, ongoing_action_summary=None, people_context=[],
                agent_skill_context=None,
            )

            service._generate_autonomous_run_speech(
                state={}, selected_preset={"model": "mock-test"},
                step_context=step_context,
                step={"action": {"speech": {"reason_summary": "1分が経過した。"}},
                      "transition": {"kind": "complete", "next_run_at": None},
                      "run_update": {"current_step_summary": "声をかけて終了する。", "history_summary": "1分経過した。"}},
            )

            decision = service._build_speech_context.call_args.kwargs["decision"]
            self.assertEqual(decision["run_objective_summary"], objective)
            self.assertEqual(decision["autonomous_step"], {
                "transition": {"kind": "complete", "next_run_at": None},
                "run_update": {"current_step_summary": "声をかけて終了する。", "history_summary": "1分経過した。"}})
            context_args = service._build_speech_context.call_args.kwargs
            self.assertEqual(context_args["affect_context"], step_context.affect_context)
            self.assertEqual(context_args["self_state_context"], step_context.self_state_context)
            self.assertEqual(context_args["autonomous_run_summaries"], [step_context.run])

    def _use_mock_model(self, service: OtomeKairoService, state: dict) -> dict:
        preset_id = state["selected_model_preset_id"]
        state["model_presets"][preset_id]["model"] = "mock-test"
        service.store.write_state(state)
        return service.store.read_state()

    def test_step_execution_distinguishes_retained_result_from_new_arrival(self) -> None:
        for status, incoming, expected in (
            ("waiting_timer", None, "timer"),
            ("active", None, "scheduled"),
            ("active", {"source_capability_id": "mcp.call_tool"}, "capability_result"),
        ):
            with self.subTest(trigger=expected), tempfile.TemporaryDirectory() as temp_dir:
                service = OtomeKairoService(Path(temp_dir))
                state = service.store.read_state()
                retained = {"source_capability_id": "mcp.call_tool"}
                run = {**self._commitment_run_record(memory_set_id=state["selected_memory_set_id"]),
                    "status": status, "last_result_context": retained}
                service.store.upsert_autonomous_run(autonomous_run=run)
                service._autonomous_run_step_guard = Mock(return_value=None)
                service._now_iso = Mock(return_value="2026-09-21T10:00:00+09:00")
                generate = Mock(return_value=(None, {
                    "action": {"kind": "none", "capability_request": None, "speech": None},
                    "transition": {"kind": "wait_until", "next_run_at": "2026-09-21T11:00:00+09:00"},
                    "run_update": {"current_step_summary": "以前の結果に基づき待つ。", "history_summary": "追加取得なし。"},
                }, None))
                service._generate_reviewed_autonomous_step_candidate = generate
                result = service._execute_autonomous_run_step_locked(
                    state=state, run_id=run["run_id"], started_at="2026-09-21T10:00:00+09:00",
                    last_result_context=incoming, emit_speech_event=False, allow_during_user_response=True,
                )
                self.assertEqual(result["status"], "waiting_timer")
                self.assertEqual(generate.call_args.kwargs["step_trigger"], expected)
                self.assertEqual(generate.call_args.kwargs["last_result_context"], retained)
                stored = service.store.get_autonomous_run(run_id=run["run_id"])
                self.assertEqual(stored.get("result_events"), run.get("result_events"))
                self.assertEqual(stored.get("observed_result_summaries"), run.get("observed_result_summaries"))

    def test_timer_keeps_observation_age_and_id_without_claiming_new_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = {"result_events": [{
                "event_id": "event:observed", "created_at": "2026-09-21T09:00:00+09:00",
                "capability_id": "mcp.call_tool", "tool_name": "get_notifications",
            }]}
            received = service._autonomous_step_observation_context(
                run=run, current_time="2026-09-21T09:00:01+09:00", step_trigger="capability_result",
            )
            timer = service._autonomous_step_observation_context(
                run=run, current_time="2026-09-21T10:00:00+09:00", step_trigger="timer",
            )
            resumed = service._autonomous_step_observation_context(
                run=run, current_time="2026-09-21T10:01:00+09:00", step_trigger="scheduled",
            )
            self.assertTrue(received["result_received_for_this_step"])
            self.assertFalse(timer["result_received_for_this_step"])
            self.assertFalse(resumed["result_received_for_this_step"])
            self.assertEqual(timer["latest_result"]["event_id"], received["latest_result"]["event_id"])
            self.assertEqual(timer["latest_result"]["age_seconds"], 3600)
            self.assertEqual(timer["result_count"], 1)
            empty = service._autonomous_step_observation_context(
                run={}, current_time="2026-09-21T10:00:00+09:00", step_trigger="scheduled",
            )
            self.assertIsNone(empty["latest_result"])
            self.assertEqual(empty["result_count"], 0)
            with self.assertRaises(ValueError):
                service._autonomous_step_observation_context(
                    run=run, current_time="2026-09-21T10:00:00+09:00", step_trigger="invalid",
                )

    def test_start_review_blocks_creation_and_replacement_before_side_effects(self) -> None:
        for mode in ("create_new", "replace_existing"):
            for response in (
                {"outcome": "reject_start", "reason_summary": "既存実行の待機を続ける目的です。"},
                {"outcome": "invalid"}, LLMError("failed"),
            ):
                with self.subTest(mode=mode, response=response), tempfile.TemporaryDirectory() as temp_dir:
                    service = OtomeKairoService(Path(temp_dir))
                    state = service.store.read_state()
                    run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
                    service.store.upsert_autonomous_run(autonomous_run=run)
                    service.store.append_events = Mock(wraps=service.store.append_events)
                    review = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
                    service.llm = SimpleNamespace(generate_autonomous_start_review=review)
                    service._execute_autonomous_run_step = Mock()
                    with self.assertRaises(LLMError):
                        service._start_autonomous_run_from_decision(
                            state=state, current_time=run["created_at"],
                            source_started_at=run["source_started_at"],
                            decision={"kind": "autonomous_run", "reason_summary": "既存の待機を維持する。",
                                "autonomous_run": {"objective_summary": "交流機会を確認する。",
                                    "initial_step_summary": "待つ。", "coordination": {
                                        "mode": mode,
                                        "target_run_ids": [run["run_id"]] if mode == "replace_existing" else [],
                                        "reason_summary": "既存の待機を維持する。"}}},
                            source_current_input=run["source_current_input"],
                            source_cycle_id="cycle:test-review", assistant_message_target_client_id=None,
                        )
                    self.assertEqual(service.store.get_autonomous_run(run_id=run["run_id"]), run)
                    self.assertEqual(len(service.store.list_autonomous_runs(memory_set_id=run["memory_set_id"])), 1)
                    service._execute_autonomous_run_step.assert_not_called()
                    self.assertEqual(review.call_args.kwargs["review_context"]["existing_runs"][0]["run_id"], run["run_id"])
                    context = review.call_args.kwargs["review_context"]
                    self.assertEqual(context["time_context"], service._build_time_context(current_time=run["created_at"]))
                    event = service.store.append_events.call_args.kwargs["events"][0]
                    self.assertEqual(event["kind"], "autonomous_start_review")
                    audit = event["start_review"]
                    self.assertEqual(audit["candidate_summary"], context["decision"])
                    self.assertEqual(audit["time_context"], context["time_context"])
                    rejected = isinstance(response, dict) and response["outcome"] == "reject_start"
                    self.assertEqual(audit["reason_summary"], response["reason_summary"] if rejected else None)
                    self.assertEqual(audit["result_status"], "completed" if rejected else "failed")

    def test_start_review_preserves_decision_clock_across_generation_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            decision_clock = service._build_time_context(current_time="2027-10-04T09:00:47+09:00")
            review_time = "2027-10-04T09:02:17+09:00"
            review = Mock(return_value={"outcome": "allow_start", "reason_summary": "依頼時点から5分後の一回通知です。"})
            service.llm = SimpleNamespace(generate_autonomous_start_review=review)
            service.store.append_events = Mock(wraps=service.store.append_events)
            service._review_autonomous_start_candidate(
                state=state, current_time=review_time, time_context=decision_clock,
                source_cycle_id="cycle:delayed-review",
                source_current_input={"sender_kind": "person", "source_kind": "user_message",
                    "text": "今から5分後に一度知らせて。", "response_target_refs": ["person:master"]},
                decision={"kind": "autonomous_run", "reason_summary": "時刻まで待って知らせる依頼です。",
                    "autonomous_run": {"objective_summary": "2027年10月4日9時5分47秒に一度知らせて終了する。",
                        "initial_step_summary": "指定時刻まで待機する。",
                        "coordination": {"mode": "create_new", "target_run_ids": [], "reason_summary": "独立した依頼です。"}}},
            )
            context = review.call_args.kwargs["review_context"]
            self.assertEqual(context["time_context"], decision_clock)
            self.assertNotEqual(context["time_context"], service._build_time_context(current_time=review_time))
            event = service.store.append_events.call_args.kwargs["events"][0]
            self.assertEqual(event["created_at"], review_time)
            self.assertEqual(event["start_review"]["time_context"], decision_clock)
            self.assertEqual(event["start_review"]["reason_summary"], review.return_value["reason_summary"])
            context["time_context"]["current_time_text"] = "changed"
            self.assertEqual(event["start_review"]["time_context"], decision_clock)

    def test_allowed_start_review_creates_run_and_includes_all_existing_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            for index in range(21):
                service.store.upsert_autonomous_run(autonomous_run={**run, "run_id": f"autonomous_run:{index}"})
            review = Mock(return_value={"outcome": "allow_start", "reason_summary": "独立した追加目的。"})
            service.llm = SimpleNamespace(generate_autonomous_start_review=review)
            service._execute_autonomous_run_step = Mock(return_value={"status": "active"})
            result = service._start_autonomous_run_from_decision(
                state=state, current_time=run["created_at"],
                source_started_at="2026-06-20T10:59:00+09:00",
                decision={"kind": "autonomous_run", "autonomous_run": {
                    "objective_summary": "指定時刻に声をかけて完了する。", "initial_step_summary": "時刻を確認する。",
                    "coordination": {"mode": "create_new", "target_run_ids": [], "reason_summary": "独立した依頼。"}}},
                source_current_input=run["source_current_input"], source_cycle_id="cycle:test-review",
                assistant_message_target_client_id=None,
            )
            self.assertEqual(len(review.call_args.kwargs["review_context"]["existing_runs"]), 21)
            self.assertEqual(
                review.call_args.kwargs["review_context"]["time_context"],
                service._build_time_context(current_time="2026-06-20T10:59:00+09:00"),
            )
            self.assertEqual(result["autonomous_run"]["status"], "active")
            saved = service.store.get_autonomous_run(run_id=result["autonomous_run"]["run_id"])
            self.assertEqual(saved["source_started_at"], "2026-06-20T10:59:00+09:00")
            self.assertEqual(saved["created_at"], run["created_at"])
            summary = service._autonomous_run_prompt_summary(saved)
            self.assertEqual(summary["source_started_at"], saved["source_started_at"])
            self.assertEqual(service._autonomous_completion_review_run_context(summary)["source_started_at"], saved["source_started_at"])
            service._execute_autonomous_run_step.assert_called_once()

    def test_step_requires_the_saved_time_origin_before_model_calls(self) -> None:
        for value in (None, "", "invalid", "2026-10-04T09:00:47"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                service = OtomeKairoService(Path(temp_dir))
                service.llm = Mock()
                run = self._commitment_run_record(memory_set_id="memory_set:default")
                run["source_started_at"] = value
                with self.assertRaises(ValueError):
                    service._build_autonomous_step_context(
                        state={}, run=run, current_time="2026-10-04T09:02:17+09:00",
                        source_current_input=run["source_current_input"], last_result_context=None,
                    )
                service.llm.generate_autonomous_step.assert_not_called()

    def test_saved_source_factors_are_not_forwarded_to_run_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["source_factors"] = [{
                "factor_ref": "drive_state:other", "kind": "drive_state",
                "summary_text": "別の主体への配慮。",
            }]
            summary = service._autonomous_run_prompt_summary(run)
            self.assertNotIn("source_factors", summary)

    def test_run_coordination_preserves_topic_suppression_only_for_replaced_runs(self) -> None:
        for mode in ("replace_existing", "create_new"):
            for select_new_topic in (False, True):
                with self.subTest(mode=mode, select_new=select_new_topic), tempfile.TemporaryDirectory() as temp_dir:
                    service = OtomeKairoService(Path(temp_dir))
                    state = service.store.read_state()
                    state["periodic_thought_topics"] = [
                        {"topic_id": topic_id, "enabled": True, "min_periodic_thinking_interval_seconds": 60,
                         "topic_summary": "公開の会話を読み、必要なら応じる。"}
                        for topic_id in ("first", "second", "new")
                    ]
                    run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
                    target_ids = []
                    for index, topic_ids in enumerate((["first"], ["first", "second"])):
                        target_id = f"autonomous_run:topic-{index}"
                        target_ids.append(target_id)
                        service.store.upsert_autonomous_run(autonomous_run={
                            **run, "run_id": target_id, "periodic_thought_topic_ids": topic_ids,
                        })
                    service.llm = SimpleNamespace(generate_autonomous_start_review=Mock(return_value={
                        "outcome": "allow_start", "reason_summary": "今回の目的と操作が一致している。",
                    }))
                    service._execute_autonomous_run_step = Mock(return_value={"status": "active"})
                    # 置換時の記憶統合と外部 LLM 呼び出しは、この保存・候補化境界の対象外。
                    service._finalize_autonomous_run_commitments = Mock(side_effect=lambda **kwargs: kwargs["run"])
                    now = "2026-09-22T12:00:00+09:00"
                    workspace = {"workspace_candidates": ([{
                        "kind": "periodic_thought_topic", "factor_ref": "periodic_thought_topic:new",
                        "summary_text": "公開の会話を読み、必要なら応じる。",
                        "metadata": {"topic_id": "new"},
                    }] if select_new_topic else [])}
                    result = service._start_autonomous_run_from_decision(
                        state=state, current_time=now,
                        source_started_at=now,
                        decision={"kind": "autonomous_run", "foreground_selection": {
                            "primary_factor_ref": "periodic_thought_topic:new" if select_new_topic else None,
                            "supporting_factor_refs": [],
                        }, "autonomous_run": {
                            "objective_summary": "公開の会話を確認し、必要な訂正を投稿して終える。",
                            "initial_step_summary": "会話を確認する。",
                            "coordination": {"mode": mode,
                                "target_run_ids": target_ids if mode == "replace_existing" else [],
                                "reason_summary": "目的を変更する。" if mode == "replace_existing" else "別の活動を始める。"},
                        }},
                        source_current_input=run["source_current_input"], source_cycle_id=None,
                        assistant_message_target_client_id=None, workspace_context=workspace,
                    )
                    replacement = result["autonomous_run"]
                    expected = {"first", "second"} if mode == "replace_existing" else set()
                    if select_new_topic:
                        expected.add("new")
                    self.assertEqual(replacement.get("periodic_thought_topic_ids", []), sorted(expected))
                    service.llm.generate_autonomous_start_review.assert_called_once()
                    review_context = service.llm.generate_autonomous_start_review.call_args.kwargs["review_context"]
                    self.assertNotIn("periodic_thought_topic_ids", review_context)
                    self.assertNotIn("source_factors", replacement)
                    self.assertNotIn("periodic_thought_topic_ids", service._autonomous_run_prompt_summary(replacement))
                    self.assertEqual(
                        [item["topic_id"] for item in service._due_periodic_thought_topics(state=state, current_time=now)],
                        [] if select_new_topic else ["new"],
                    )
                    if mode == "replace_existing":
                        for target_id in target_ids:
                            self.assertEqual(service.store.get_autonomous_run(run_id=target_id)["status"], "cancelled")
                        service.store.upsert_autonomous_run(autonomous_run={**replacement, "status": "completed"})
                        self.assertEqual(len(service._due_periodic_thought_topics(state=state, current_time=now)), 3)

    def test_pre_send_check_withhold_regenerates_autonomous_step_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            service.store.upsert_autonomous_run(autonomous_run=run)
            step_context = SimpleNamespace(
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=step_context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            initial_step = {
                "action": {
                    "kind": "capability_request",
                    "capability_request": {
                        "capability_id": "mcp.call_tool",
                        "input": {
                            "mcp_server_id": "e-stat",
                            "tool_name": "create_post",
                            "arguments": {"content": "candidate"},
                        },
                    },
                    "speech": None,
                },
                "transition": {"kind": "continue", "next_run_at": None},
                "run_update": {"current_step_summary": "投稿する", "history_summary": "投稿する"},
            }
            retry_step = {
                "action": {"kind": "none", "capability_request": None, "speech": None},
                "transition": {"kind": "cancel", "next_run_at": None},
                "run_update": {"current_step_summary": "送らない", "history_summary": "送らない"},
            }
            generate_autonomous_step = Mock(side_effect=[initial_step, retry_step])
            service.llm = SimpleNamespace(generate_autonomous_step=generate_autonomous_step)
            first_audit = {
                "mcp_server_id": "e-stat",
                "tool_name": "create_post",
                "result_status": "withheld",
                "outcome": "withhold",
                "reason_code": "reviewer_withheld",
                "review_attempt": 1,
            }
            service._dispatch_autonomous_run_capability_request = Mock(
                side_effect=PreSendCheckWithheldError(audit_summary=first_audit)
            )
            service._record_autonomous_pre_send_check_terminal = Mock()
            cancelled = {**run, "status": "cancelled", "completed_at": "2026-08-11T12:00:01+09:00"}
            service._apply_autonomous_step_transition = Mock(return_value=cancelled)
            service._finalize_autonomous_run_commitments = Mock(return_value=cancelled)

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-11T12:00:00+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=False,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(generate_autonomous_step.call_count, 2)
            self.assertEqual(service._build_autonomous_step_context.call_args.kwargs["step_trigger"], "scheduled")
            self.assertEqual(
                service._build_autonomous_step_context.call_args.kwargs[
                    "pre_send_check_feedback"
                ],
                AUTONOMOUS_PRE_SEND_CHECK_RETRY_FEEDBACK,
            )
            service._record_autonomous_pre_send_check_terminal.assert_called_once()

    def test_unperformed_future_speech_is_replaced_by_capability_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run.update(
                {
                    "objective_summary": "ELYTHに投稿を1件作成する。",
                    "current_step_summary": "投稿内容を決める。",
                    "history_summary": "タイムラインと自分の投稿を確認した。",
                    "observed_result_summaries": [
                        {
                            "capability_id": "mcp.call_tool",
                            "tool_name": "get_my_posts",
                            "summary_text": "自分の投稿履歴を確認した。",
                        }
                    ],
                }
            )
            run["source_current_input"]["text"] = "ELYTHに投稿して。"
            service.store.upsert_autonomous_run(autonomous_run=run)
            context = SimpleNamespace(
                run=service._autonomous_run_prompt_summary(run),
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            future_speech_step = {
                "action": {
                    "kind": "speech",
                    "capability_request": None,
                    "speech": {
                        "reason_code": "announce_post",
                        "reason_summary": "これから投稿することを伝える。",
                    },
                },
                "transition": {"kind": "complete", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "投稿の準備を整えた。",
                    "history_summary": "投稿へ移る準備を整えた。",
                },
            }
            create_post_step = {
                "action": {
                    "kind": "capability_request",
                    "capability_request": {
                        "capability_id": "mcp.call_tool",
                        "input": {
                            "mcp_server_id": "elyth",
                            "tool_name": "create_post",
                            "arguments": {"content": "朝の空気を言葉にする。"},
                        },
                    },
                    "speech": None,
                },
                "transition": {"kind": "continue", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "create_post の結果を待つ。",
                    "history_summary": "投稿作成を要求した。",
                },
            }
            generate_step = Mock(side_effect=[future_speech_step, create_post_step])
            review = Mock(
                return_value={
                    "outcome": "continue_run",
                    "reason_summary": "外界への投稿作用がまだ実行されていない。",
                }
            )
            service.llm = SimpleNamespace(
                generate_autonomous_step=generate_step,
                generate_autonomous_completion_review=review,
            )
            service._generate_autonomous_run_speech = Mock(
                return_value={"speech_text": "さて、そろそろ私も何か投稿してみるとします。"}
            )
            service._dispatch_autonomous_run_capability_request = Mock(
                return_value={
                    "request_id": "mcp_call_tool_request:create-post",
                    "capability_id": "mcp.call_tool",
                }
            )
            service._emit_autonomous_run_assistant_message_event = Mock()
            service._persist_autonomous_run_speech_event = Mock()

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-16T09:44:30+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=True,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "waiting_result")
            self.assertIsNone(result["speech_payload"])
            self.assertEqual(result["step"], create_post_step)
            self.assertEqual(generate_step.call_count, 2)
            self.assertEqual(review.call_count, 1)
            self.assertEqual(
                service._build_autonomous_step_context.call_args.kwargs[
                    "completion_review_feedback"
                ],
                AUTONOMOUS_COMPLETION_REVIEW_RETRY_FEEDBACK + "\n審査理由: 外界への投稿作用がまだ実行されていない。",
            )
            service._emit_autonomous_run_assistant_message_event.assert_not_called()
            service._persist_autonomous_run_speech_event.assert_not_called()

    def test_completed_effect_report_is_reviewed_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run.update(
                {
                    "objective_summary": "ELYTHに投稿を1件作成する。",
                    "observed_result_summaries": [
                        {
                            "capability_id": "mcp.call_tool",
                            "tool_name": "create_post",
                            "result_status": "completed",
                            "is_error": False,
                            "summary_text": "投稿を作成し、投稿IDを受け取った。",
                        }
                    ],
                }
            )
            service.store.upsert_autonomous_run(autonomous_run=run)
            context = SimpleNamespace(
                run=service._autonomous_run_prompt_summary(run),
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            complete_step = {
                "action": {
                    "kind": "speech",
                    "capability_request": None,
                    "speech": {
                        "reason_code": "post_completed",
                        "reason_summary": "投稿が完了したことを報告する。",
                    },
                },
                "transition": {"kind": "complete", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "投稿作成を完了した。",
                    "history_summary": "create_post の成功結果を確認した。",
                },
            }
            call_order: list[str] = []
            review = Mock(
                side_effect=lambda **_: (
                    call_order.append("review")
                    or {
                        "outcome": "allow_complete",
                        "reason_summary": "投稿作成の成功結果と完了報告が一致する。",
                    }
                )
            )
            service.llm = SimpleNamespace(
                generate_autonomous_step=Mock(return_value=complete_step),
                generate_autonomous_completion_review=review,
            )
            service._generate_autonomous_run_speech = Mock(
                return_value={"speech_text": "投稿が完了しました。"}
            )
            service._emit_autonomous_run_assistant_message_event = Mock(
                side_effect=lambda **_: call_order.append("delivery")
            )
            service._now_iso = Mock(return_value="2026-08-16T09:45:00+09:00")
            service._persist_autonomous_run_speech_event = Mock(return_value=None)
            service._finalize_autonomous_run_commitments = Mock(
                side_effect=lambda **kwargs: kwargs["run"]
            )

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-16T09:45:00+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=True,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["speech_payload"]["speech_text"], "投稿が完了しました。")
            self.assertEqual(call_order, ["review", "delivery"])
            review_context = review.call_args.kwargs["review_context"]
            self.assertEqual(
                review_context["time_context"],
                service._build_time_context(current_time=service._now_iso()),
            )
            self.assertEqual(
                review_context["run"]["observed_result_summaries"][0]["tool_name"],
                "create_post",
            )
            self.assertFalse(
                review_context["run"]["observed_result_summaries"][0]["is_error"]
            )
            self.assertEqual(
                review_context["candidate"]["speech_text"],
                "投稿が完了しました。",
            )
            with closing(sqlite3.connect(service.store.memory_db_path)) as conn:
                audits = [json.loads(row[0])["completion_review"] for row in conn.execute(
                    "SELECT payload_json FROM events WHERE kind = 'autonomous_completion_review'")]
            self.assertEqual(audits[-1]["reason_summary"], "投稿作成の成功結果と完了報告が一致する。")
            self.assertEqual(audits[-1]["candidate"], review_context["candidate"])

    def test_second_completion_rejection_cancels_without_speech_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["objective_summary"] = "ELYTHに投稿を1件作成する。"
            service.store.upsert_autonomous_run(autonomous_run=run)
            context = SimpleNamespace(
                run=service._autonomous_run_prompt_summary(run),
                current_input=SimpleNamespace(to_prompt_payload=lambda: {}),
            )
            service._build_autonomous_step_context = Mock(return_value=context)
            service._autonomous_run_step_guard = Mock(return_value=None)
            incomplete_step = {
                "action": {
                    "kind": "speech",
                    "capability_request": None,
                    "speech": {
                        "reason_code": "announce_post",
                        "reason_summary": "これから投稿することを伝える。",
                    },
                },
                "transition": {"kind": "complete", "next_run_at": None},
                "run_update": {
                    "current_step_summary": "投稿準備を終えた。",
                    "history_summary": "投稿はまだ実行していない。",
                },
            }
            service.llm = SimpleNamespace(
                generate_autonomous_step=Mock(return_value=incomplete_step),
                generate_autonomous_completion_review=Mock(
                    return_value={
                        "outcome": "continue_run",
                        "reason_summary": "投稿作用がまだ実行されていない。",
                    }
                ),
            )
            service._generate_autonomous_run_speech = Mock(
                side_effect=[
                    {"speech_text": "投稿してみるとします。"},
                    {"speech_text": "これから投稿へ移ります。"},
                ]
            )
            service._emit_autonomous_run_assistant_message_event = Mock()
            service._persist_autonomous_run_speech_event = Mock()
            service._finalize_autonomous_run_commitments = Mock(
                side_effect=lambda **kwargs: kwargs["run"]
            )

            result = service._execute_autonomous_run_step_locked(
                state=state,
                run_id=run["run_id"],
                started_at="2026-08-16T09:45:30+09:00",
                source_current_input=run["source_current_input"],
                emit_speech_event=True,
                allow_during_user_response=True,
            )

            self.assertEqual(result["status"], "cancelled")
            self.assertIn("2回続けて", result["error"])
            service._emit_autonomous_run_assistant_message_event.assert_not_called()
            service._persist_autonomous_run_speech_event.assert_not_called()

    def test_links_autonomous_run_to_commitment_created_by_source_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            memory_set_id = state["selected_memory_set_id"]
            run = self._commitment_run_record(memory_set_id=memory_set_id)
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:linked",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service._link_autonomous_run_source_commitments(
                state=state,
                pipeline={
                    "decision": {"kind": "autonomous_run"},
                    "autonomous_run_summary": {"run_id": run["run_id"]},
                },
                memory_trace={
                    "turn_consolidation_status": "succeeded",
                    "updated_memory_unit_ids": [commitment["memory_unit_id"]],
                },
                current_time="2026-06-20T11:00:10+09:00",
            )

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertEqual(updated["source_commitment_memory_unit_ids"], [commitment["memory_unit_id"]])

    def test_terminal_run_resolves_commitment_after_late_source_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            memory_set_id = state["selected_memory_set_id"]
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:late_link",
            )
            run = self._commitment_run_record(memory_set_id=memory_set_id, status="completed")
            run["commitment_resolution"] = {
                "result_status": "skipped",
                "reason": "no_linked_commitments",
                "terminal_status": "completed",
            }
            service.store.upsert_autonomous_run(autonomous_run=run)
            service.memory.vector_indexer.sync = lambda **_: None
            summary_patch = patch.object(type(service.memory.llm), "generate_commitment_lifecycle_summaries", return_value={"summaries": [{
                "memory_unit_id": commitment["memory_unit_id"], "summary_text": "依頼された声かけを完了した。",
            }]})
            summary_patch.start()
            self.addCleanup(summary_patch.stop)

            service._link_autonomous_run_source_commitments(
                state=state,
                pipeline={
                    "decision": {"kind": "autonomous_run"},
                    "autonomous_run_summary": {"run_id": run["run_id"]},
                },
                memory_trace={
                    "turn_consolidation_status": "succeeded",
                    "updated_memory_unit_ids": [commitment["memory_unit_id"]],
                },
                current_time="2026-06-20T11:03:00+09:00",
            )

            updated_commitment = service.store.list_memory_units_by_id(
                memory_set_id=memory_set_id,
                memory_unit_ids=[commitment["memory_unit_id"]],
            )[0]
            updated_run = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertEqual(updated_commitment["commitment_state"], "done")
            self.assertEqual(updated_commitment["summary_text"], "依頼された声かけを完了した。")
            self.assertEqual(updated_run["commitment_resolution"]["result_status"], "updated")

    def test_completed_autonomous_run_marks_linked_commitment_done(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            memory_set_id = state["selected_memory_set_id"]
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:done",
            )
            run = self._commitment_run_record(
                memory_set_id=memory_set_id,
                status="completed",
                source_commitment_memory_unit_ids=[commitment["memory_unit_id"]],
            )
            service.store.upsert_autonomous_run(autonomous_run=run)
            service.memory.vector_indexer.sync = lambda **_: None

            updated_run = service._finalize_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status="completed",
                current_time="2026-06-20T11:03:00+09:00",
                evidence_events=[],
            )

            updated_commitment = service.store.list_memory_units_by_id(
                memory_set_id=memory_set_id,
                memory_unit_ids=[commitment["memory_unit_id"]],
            )[0]
            active_commitments = service.store.list_memory_units_for_recall(
                memory_set_id=memory_set_id,
                current_time="2026-06-20T11:04:00+09:00",
                include_memory_types=["commitment"],
                statuses=["inferred", "confirmed"],
                commitment_states=["open", "waiting_confirmation", "on_hold"],
                limit=20,
            )

            self.assertEqual(updated_commitment["commitment_state"], "done")
            self.assertEqual(updated_run["commitment_resolution"]["result_status"], "updated")
            self.assertNotIn(
                commitment["memory_unit_id"],
                [unit["memory_unit_id"] for unit in active_commitments],
            )

    def test_cancelled_autonomous_run_marks_linked_commitment_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            memory_set_id = state["selected_memory_set_id"]
            commitment = self._persist_commitment(
                service=service,
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:cancelled",
            )
            run = self._commitment_run_record(
                memory_set_id=memory_set_id,
                status="cancelled",
                source_commitment_memory_unit_ids=[commitment["memory_unit_id"]],
            )
            service.store.upsert_autonomous_run(autonomous_run=run)
            service.memory.vector_indexer.sync = lambda **_: None

            service._finalize_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status="cancelled",
                current_time="2026-06-20T11:03:00+09:00",
                evidence_events=[],
            )

            updated_commitment = service.store.list_memory_units_by_id(
                memory_set_id=memory_set_id,
                memory_unit_ids=[commitment["memory_unit_id"]],
            )[0]
            self.assertEqual(updated_commitment["commitment_state"], "cancelled")

    def test_timeout_returns_waiting_result_run_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="waiting_result",
                waiting_request_id="vision_capture_request:timeout",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service._pending_capability_requests["vision_capture_request:timeout"] = {
                "request_record": self._request_record(run),
            }
            service._prune_pending_capability_requests(current_time="2026-06-20T12:00:00+09:00")

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "active")
            self.assertIsNone(updated["waiting_request_id"])
            self.assertEqual(updated["next_run_at"], "2026-06-20T12:00:00+09:00")
            self.assertEqual(updated["last_result_context"]["source_capability_id"], "vision.capture")
            self.assertEqual(updated["last_result_context"]["observation_summary"]["error_kind"], "request_timeout")
            self.assertIn("timeout", updated["history_summary"])

    def test_timeout_preserves_paused_run_and_sets_resume_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="paused",
                waiting_request_id="vision_capture_request:paused_timeout",
                pause_reason="manual_pause",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service._pending_capability_requests["vision_capture_request:paused_timeout"] = {
                "request_record": self._request_record(run),
            }
            service._prune_pending_capability_requests(current_time="2026-06-20T12:00:00+09:00")

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "paused")
            self.assertEqual(updated["pause_reason"], "manual_pause")
            self.assertEqual(updated["resume_status"], "active")
            self.assertIsNone(updated["waiting_request_id"])
            self.assertEqual(updated["last_result_context"]["observation_summary"]["error_kind"], "request_timeout")

    def test_startup_recovery_returns_orphaned_waiting_result_run_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="waiting_result",
                waiting_request_id="vision_capture_request:orphan",
            )
            service.store.upsert_autonomous_run(autonomous_run=run)

            service.recover_autonomous_run_runtime_state_after_startup()

            updated = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["status"], "active")
            self.assertIsNone(updated["waiting_request_id"])
            self.assertEqual(updated["last_result_context"]["source_capability_id"], "vision.capture")
            self.assertEqual(updated["last_result_context"]["observation_summary"]["error_kind"], "orphaned_after_startup")

    def test_recovery_ignores_terminal_and_request_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            terminal = self._run_record(
                run_id="autonomous_run:terminal",
                status="cancelled",
                waiting_request_id="vision_capture_request:terminal",
            )
            mismatched = self._run_record(
                run_id="autonomous_run:mismatched",
                status="waiting_result",
                waiting_request_id="vision_capture_request:expected",
            )
            service.store.upsert_autonomous_run(autonomous_run=terminal)
            service.store.upsert_autonomous_run(autonomous_run=mismatched)

            terminal_result = service._mark_autonomous_run_capability_wait_interrupted(
                request_record=self._request_record(terminal),
                current_time="2026-06-20T12:00:00+09:00",
                reason_code="request_timeout",
                reason_summary="request timeout",
            )
            mismatch_record = self._request_record(mismatched)
            mismatch_record["request_id"] = "vision_capture_request:actual"
            mismatch_result = service._mark_autonomous_run_capability_wait_interrupted(
                request_record=mismatch_record,
                current_time="2026-06-20T12:00:00+09:00",
                reason_code="request_timeout",
                reason_summary="request timeout",
            )

            self.assertIsNone(terminal_result)
            self.assertIsNone(mismatch_result)
            self.assertEqual(
                service.store.get_autonomous_run(run_id=terminal["run_id"])["status"],
                "cancelled",
            )
            self.assertEqual(
                service.store.get_autonomous_run(run_id=mismatched["run_id"])["waiting_request_id"],
                "vision_capture_request:expected",
            )

    def test_capability_result_is_persisted_and_last_result_survives_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["source_cycle_id"] = "cycle:source"
            run["origin_interaction_ref"] = "interaction:test"
            run["participant_refs"] = ["person:test"]
            run["last_result_context"] = {
                "source_capability_id": "mcp.call_tool",
                "observation_summary": {"mcp_result_summary": "返信を投稿した。"},
            }
            service.store.upsert_autonomous_run(autonomous_run=run)

            event = service._persist_autonomous_capability_result_event(
                run=run,
                capability_id="mcp.call_tool",
                observation_summary={
                    "mcp_server_id": "elyth",
                    "tool_name": "get_notifications",
                    "mcp_result_summary": '{"data":{"items":[]}}',
                    "observed_persons": [
                        {
                            "person_ref": "person:mcp:elyth:rin_ichinose",
                            "display_name": "一ノ瀬 凜",
                        }
                    ],
                },
                input_text="MCP tool は elyth/get_notifications。",
                created_at="2026-08-13T20:52:46+09:00",
                world_state_update={
                    "result_status": "succeeded",
                    "updated_state_count": 1,
                    "replaced_state_count": 0,
                    "failure_reason": None,
                },
            )
            self.assertEqual(event["kind"], "capability_result")
            self.assertEqual(event["tool_name"], "get_notifications")
            self.assertEqual(event["observed_person_refs"], ["person:mcp:elyth:rin_ichinose"])
            self.assertEqual(event["world_state_update"]["result_status"], "succeeded")

            updated = service._apply_autonomous_step_transition(
                run={
                    **run,
                    "last_result_context": {"source_capability_id": "mcp.call_tool"},
                },
                step={
                    "action": {
                        "kind": "speech",
                        "capability_request": None,
                        "speech": {
                            "reason_code": "done",
                            "reason_summary": "対応した。",
                        },
                    },
                    "transition": {"kind": "complete", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "既読処理を完了した。",
                        "history_summary": "既読処理を実行し完了しました。",
                    },
                },
                action_kind="speech",
                current_time="2026-08-13T20:53:14+09:00",
                capability_request_summary=None,
            )
            self.assertEqual(updated["status"], "completed")
            self.assertEqual(updated["last_result_context"]["source_capability_id"], "mcp.call_tool")

    def test_autonomous_result_refreshes_notification_state_before_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            persona_context = service._build_selected_persona_context(state=state, role="world_state")
            old_observation = {
                "capability_id": "mcp.call_tool", "status": "completed", "is_error": False,
                "error": None, "mcp_server_id": "elyth", "tool_name": "get_notifications",
                "mcp_result_summary": '{"data":{"scope_unread_count":1}}',
            }
            old_trace, _ = service._refresh_world_state_context(
                state=state, started_at="2026-09-28T22:00:00+09:00",
                input_text="未読通知を取得した。", trigger_kind="capability_result",
                client_context={}, cycle_id=None, selected_candidate=None,
                observation_summary=old_observation,
                capability_request_summary={"request_id": "mcp_call_tool_request:old"},
                persona_context=persona_context, current_person_ref=None,
            )
            self.assertEqual(old_trace.result_status, "succeeded")

            run = self._run_record(
                status="waiting_result", waiting_request_id="mcp_call_tool_request:new",
            )
            run["participant_refs"] = []
            service.store.upsert_autonomous_run(autonomous_run=run)
            new_observation = {
                **old_observation,
                "mcp_result_summary": '{"data":{"scope_unread_count":0}}',
            }
            service._capability_result_capability_id = Mock(return_value="mcp.call_tool")
            service._capability_request_summary = Mock(return_value={
                "request_id": "mcp_call_tool_request:new", "capability_id": "mcp.call_tool",
            })
            service._activate_capability_ongoing_action = Mock()
            service._capability_result_active_step_summary = Mock(return_value="通知取得結果を確認中。")
            service._build_capability_result_client_context = Mock(return_value={})
            service._capability_result_observation_summary = Mock(return_value=new_observation)
            service._build_capability_result_input_text = Mock(return_value="未読通知を取得した。")
            service._prepare_capability_result_context = Mock(
                return_value=({}, new_observation, "未読通知を取得した。"),
            )
            service._build_capability_result_decision_context = Mock(return_value={
                "source_capability_id": "mcp.call_tool", "observation_summary": new_observation,
            })
            service._autonomous_run_after_result_schedule = Mock(return_value=("active", None, None))
            service._user_response_cycle_active = Mock(return_value=False)
            service._now_iso = Mock(return_value="2026-09-28T22:01:00+09:00")

            def verify_before_next_step(**_kwargs: object) -> None:
                states = service._list_current_world_states(
                    state=state, current_time="2026-09-28T22:01:00+09:00", limit=20,
                )
                notification_states = [
                    item for item in states
                    if item.get("integration_key") == "external_service:elyth:get_notifications"
                ]
                self.assertEqual(len(notification_states), 1)
                self.assertIn("scope_unread_count", notification_states[0]["summary_text"])
                self.assertIn("0", notification_states[0]["summary_text"])
                self.assertEqual(notification_states[0]["source_ref"], "mcp_call_tool_request:new")

            service._execute_autonomous_run_step = Mock(side_effect=verify_before_next_step)
            service._execute_autonomous_capability_result_cycle_inner(
                state=state,
                capability_response={
                    "request_record": {
                        "request_id": "mcp_call_tool_request:new", "autonomous_run_id": run["run_id"],
                    },
                },
                started_at="2026-09-28T22:01:00+09:00",
            )
            service._execute_autonomous_run_step.assert_called_once()
            stored_run = service.store.get_autonomous_run(run_id=run["run_id"])
            self.assertEqual(stored_run["result_events"][-1]["world_state_update"]["result_status"], "succeeded")
            self.assertEqual(stored_run["result_events"][-1]["world_state_update"]["replaced_state_count"], 1)

    def test_observed_result_summary_keeps_completion_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            summaries = service._append_autonomous_observed_result_summaries(
                run={"observed_result_summaries": []},
                capability_id="mcp.call_tool",
                observation_summary={
                    "tool_name": "create_post",
                    "status": "completed",
                    "is_error": False,
                    "mcp_result_summary": "投稿を作成した。",
                },
                result_payload={"status": "completed", "is_error": False},
                created_at="2026-08-16T09:45:00+09:00",
            )

            self.assertEqual(
                summaries,
                [
                    {
                        "capability_id": "mcp.call_tool",
                        "tool_name": "create_post",
                        "result_status": "completed",
                        "is_error": False,
                        "summary_text": "投稿を作成した。",
                        "created_at": "2026-08-16T09:45:00+09:00",
                    }
                ],
            )

    def test_twentieth_continue_step_starts_five_minute_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="active",
                waiting_request_id=None,
            )
            run["consecutive_step_count"] = 19

            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {"kind": "none", "capability_request": None, "speech": None},
                    "transition": {"kind": "continue", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "確認を続ける。",
                        "history_summary": "確認を続けた。",
                    },
                },
                action_kind="none",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary=None,
            )

            self.assertEqual(updated["status"], "waiting_timer")
            self.assertEqual(updated["consecutive_step_count"], 0)
            self.assertEqual(updated["cooldown_until"], "2026-06-20T12:05:00+09:00")
            self.assertEqual(updated["next_run_at"], updated["cooldown_until"])
            public_summary = service._autonomous_run_public_summary(
                updated,
                current_time="2026-06-20T12:00:00+09:00",
            )
            self.assertEqual(public_summary["consecutive_step_count"], 0)
            self.assertEqual(public_summary["cooldown_until"], updated["cooldown_until"])

    def test_nineteenth_continue_step_keeps_normal_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(status="active", waiting_request_id=None)
            run["consecutive_step_count"] = 18

            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {"kind": "none", "capability_request": None, "speech": None},
                    "transition": {"kind": "continue", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "確認を続ける。",
                        "history_summary": "19回目の確認を続けた。",
                    },
                },
                action_kind="none",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary=None,
            )

            self.assertEqual(updated["consecutive_step_count"], 19)
            self.assertIsNone(updated["cooldown_until"])
            self.assertEqual(updated["next_run_at"], "2026-06-20T12:00:05+09:00")

    def test_explicit_wait_resets_continuous_step_count_without_forced_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="active",
                waiting_request_id=None,
            )
            run["consecutive_step_count"] = 19

            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {"kind": "none", "capability_request": None, "speech": None},
                    "transition": {
                        "kind": "wait_until",
                        "next_run_at": "2026-06-20T12:01:00+09:00",
                    },
                    "run_update": {
                        "current_step_summary": "1分待つ。",
                        "history_summary": "次の確認まで待つ。",
                    },
                },
                action_kind="none",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary=None,
            )

            self.assertEqual(updated["consecutive_step_count"], 0)
            self.assertIsNone(updated["cooldown_until"])
            self.assertEqual(updated["next_run_at"], "2026-06-20T12:01:00+09:00")

    def test_twentieth_capability_step_waits_only_for_remaining_cooldown_after_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            run = self._run_record(
                status="active",
                waiting_request_id=None,
            )
            run["consecutive_step_count"] = 19
            updated = service._apply_autonomous_step_transition(
                run=run,
                step={
                    "action": {
                        "kind": "capability_request",
                        "capability_request": {
                            "capability_id": "vision.capture",
                            "input": {"vision_source_id": "vision_source:main", "mode": "still"},
                        },
                        "speech": None,
                    },
                    "transition": {"kind": "continue", "next_run_at": None},
                    "run_update": {
                        "current_step_summary": "結果を待つ。",
                        "history_summary": "20回目の観測を依頼した。",
                    },
                },
                action_kind="capability_request",
                current_time="2026-06-20T12:00:00+09:00",
                capability_request_summary={"request_id": "vision_capture_request:20"},
            )

            self.assertEqual(updated["status"], "waiting_result")
            self.assertEqual(updated["cooldown_until"], "2026-06-20T12:05:00+09:00")
            self.assertEqual(
                service._autonomous_run_after_result_schedule(
                    run=updated,
                    current_time="2026-06-20T12:02:00+09:00",
                ),
                ("waiting_timer", "2026-06-20T12:05:00+09:00", "2026-06-20T12:05:00+09:00"),
            )
            self.assertEqual(
                service._autonomous_run_after_result_schedule(
                    run=updated,
                    current_time="2026-06-20T12:06:00+09:00",
                ),
                ("active", "2026-06-20T12:06:00+09:00", None),
            )

    def test_self_initiated_step_drops_visual_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["origin_kind"] = "background_thinking"
            run["origin_interaction_ref"] = None
            run["participant_refs"] = []
            run["source_current_input"] = {
                "sender_kind": "system",
                "sender_ref": None,
                "source_kind": "background_thinking",
                "response_target_refs": [],
                "text": "自己評価。しばらく関わっていない定期思考トピックがある。",
            }
            service._list_current_world_states = Mock(
                return_value=[
                    {"state_type": "visual_context", "summary_text": "リズムゲームのS評価"},
                    {"state_type": "external_service", "summary_text": "ELYTHの通知は空"},
                ]
            )
            service._summarize_foreground_world_states = Mock(
                side_effect=lambda states, current_time: states
            )
            service._build_capability_decision_view = Mock(return_value=[])
            service._build_agent_skill_context = Mock(return_value=None)
            service._build_time_context = Mock(return_value={})
            service._load_recent_turns = Mock(return_value=[])
            service._autonomous_run_activity_context = Mock(return_value=None)
            service._summarize_ongoing_action = Mock(return_value=None)
            service._current_ongoing_action = Mock(return_value=None)
            service._build_people_context = Mock(return_value=[])
            service._autonomous_run_prompt_summary = Mock(return_value={"objective_summary": "向きへ関わる。"})

            context = service._build_autonomous_step_context(
                state=state,
                run=run,
                current_time="2026-08-16T19:33:00+09:00",
                source_current_input=run["source_current_input"],
                last_result_context=None,
                step_trigger="timer",
            )

            self.assertEqual(context.to_prompt_payload()["observation_context"], {
                "step_trigger": "timer", "result_received_for_this_step": False,
                "latest_result": None, "result_count": 0,
            })
            self.assertEqual(
                context.foreground_world_state,
                [{"state_type": "external_service", "summary_text": "ELYTHの通知は空"}],
            )

    def test_people_context_includes_observed_mcp_persons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()

            people = service._build_people_context(
                state=state,
                current_input=CurrentInput(
                    sender_kind="system",
                    sender_ref=None,
                    source_kind="autonomous_run",
                    response_target_refs=(),
                    interaction_context=None,
                    text="通知を確認した。",
                ),
                structured_sources=[
                    {
                        "observed_persons": [
                            {
                                "person_ref": "person:mcp:elyth:rin_ichinose",
                                "display_name": "一ノ瀬 凜",
                            }
                        ],
                        "observed_person_refs": ["person:mcp:elyth:rin_ichinose"],
                    }
                ],
            )

            self.assertEqual(
                people,
                [
                    {
                        "person_ref": "person:mcp:elyth:rin_ichinose",
                        "display_name": "一ノ瀬 凜",
                    }
                ],
            )

    def test_terminal_consolidation_records_observed_person_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = self._use_mock_model(service, service.store.read_state())
            run = self._commitment_run_record(memory_set_id=state["selected_memory_set_id"])
            run["source_cycle_id"] = "cycle:source"
            run["source_current_input"] = {
                "sender_kind": "person",
                "sender_ref": "person:test",
                "source_kind": "user_message",
                "response_target_refs": ["person:test"],
                "interaction_context": {
                    "interaction_ref": "interaction:test",
                    "speaker_ref": "person:test",
                    "participants": [{"person_ref": "person:test", "display_name": "テスト人物"}],
                },
                "text": "対応しておいて",
            }
            run["participant_refs"] = ["person:test"]
            run["origin_interaction_ref"] = "interaction:test"
            run["observed_persons"] = [
                {
                    "person_ref": "person:mcp:elyth:rin_ichinose",
                    "display_name": "一ノ瀬 凜",
                }
            ]
            run["observed_result_summaries"] = [
                {
                    "capability_id": "mcp.call_tool",
                    "tool_name": "create_reply",
                    "summary_text": "一ノ瀬 凜への返信を投稿した。",
                    "created_at": "2026-08-13T20:52:59+09:00",
                }
            ]
            run["result_events"] = []
            service.store.upsert_autonomous_run(autonomous_run=run)

            updated = service._finalize_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status="completed",
                current_time="2026-08-13T20:53:14+09:00",
                evidence_events=[
                    {
                        "event_id": "event:speech",
                        "cycle_id": "cycle:source",
                        "memory_set_id": state["selected_memory_set_id"],
                        "kind": "speech",
                        "role": "assistant",
                        "text": "一ノ瀬 凜さんへ返信しました。",
                        "created_at": "2026-08-13T20:53:14+09:00",
                    }
                ],
            )

            consolidation = updated["terminal_consolidation"]
            self.assertEqual(consolidation["result_status"], "succeeded")
            self.assertTrue(str(consolidation["episode_id"] or "").startswith("episode:"))

    def _run_record(
        self,
        *,
        status: str,
        waiting_request_id: str | None,
        run_id: str = "autonomous_run:test",
        pause_reason: str | None = None,
    ) -> dict:
        return {
            "run_id": run_id,
            "memory_set_id": "memory_set:default",
            "status": status,
            "objective_summary": "視覚確認を続ける。",
            "origin_kind": "user_message",
            "current_step_summary": "vision.capture の結果を待つ。",
            "history_summary": "action=capability_request transition=continue",
            "next_run_at": None,
            "waiting_request_id": waiting_request_id,
            "pause_reason": pause_reason,
            "resume_status": "waiting_result" if status == "paused" else None,
            "consecutive_step_count": 0,
            "cooldown_until": None,
            "created_at": "2026-06-20T11:00:00+09:00",
            "updated_at": "2026-06-20T11:00:00+09:00",
            "completed_at": "2026-06-20T11:30:00+09:00" if status == "cancelled" else None,
            "last_step": {
                "action": {
                    "kind": "capability_request",
                    "capability_request": {
                        "capability_id": "vision.capture",
                        "input": {
                            "vision_source_id": "vision_source:main",
                            "mode": "still",
                        },
                    },
                    "speech": None,
                },
                "transition": {
                    "kind": "continue",
                    "next_run_at": None,
                },
                "run_update": {
                    "current_step_summary": "vision.capture の結果を待つ。",
                    "history_summary": "action=capability_request:vision.capture transition=continue",
                },
            },
        }

    def _request_record(self, run: dict) -> dict:
        return {
            "request_id": run["waiting_request_id"],
            "target_client_id": "client:vision",
            "memory_set_id": run["memory_set_id"],
            "capability_id": "vision.capture",
            "input": {
                "vision_source_id": "vision_source:main",
                "mode": "still",
            },
            "timeout_ms": 1000,
            "created_at": "2026-06-20T11:00:00+09:00",
            "expires_at": "2026-06-20T11:00:01+09:00",
            "autonomous_run_id": run["run_id"],
            "vision_source_id": "vision_source:main",
            "source_kind": "camera",
            "source_owner": "self",
            "source_label": "main",
        }

    def _commitment_run_record(
        self,
        *,
        memory_set_id: str,
        status: str = "active",
        source_commitment_memory_unit_ids: list[str] | None = None,
    ) -> dict:
        return {
            "run_id": "autonomous_run:commitment",
            "memory_set_id": memory_set_id,
            "status": status,
            "objective_summary": "3分後にユーザーへ声をかける",
            "origin_kind": "user_message",
            "current_step_summary": "予定時刻にユーザーへ声をかける。",
            "history_summary": "3分後のリマインド依頼を受諾した。",
            "next_run_at": None,
            "waiting_request_id": None,
            "pause_reason": None,
            "resume_status": None,
            "consecutive_step_count": 0,
            "cooldown_until": None,
            "created_at": "2026-06-20T11:00:00+09:00",
            "updated_at": "2026-06-20T11:00:00+09:00",
            "completed_at": "2026-06-20T11:03:00+09:00" if status in {"completed", "cancelled"} else None,
            "source_cycle_id": "cycle:source",
            "origin_interaction_ref": "interaction:test",
            "participant_refs": ["person:test"],
            "source_started_at": "2026-06-20T11:00:00+09:00",
            "source_current_input": {
                "sender_kind": "person",
                "sender_ref": "person:test",
                "source_kind": "user_message",
                "response_target_refs": ["person:test"],
                "interaction_context": {
                    "interaction_ref": "interaction:test",
                    "speaker_ref": "person:test",
                    "participants": [{"person_ref": "person:test", "display_name": "テスト人物"}],
                },
                "text": "3分後に声をかけて",
            },
            "source_commitment_memory_unit_ids": source_commitment_memory_unit_ids or [],
        }

    def _persist_commitment(
        self,
        *,
        service: OtomeKairoService,
        memory_set_id: str,
        memory_unit_id: str,
    ) -> dict:
        unit = {
            "memory_unit_id": memory_unit_id,
            "memory_set_id": memory_set_id,
            "memory_type": "commitment",
            "scope_type": "self",
            "scope_key": "self",
            "subject_ref": "self",
            "predicate": "notify",
            "object_ref_or_value": "person:test",
            "summary_text": "3分後にユーザーへ声をかけること。",
            "status": "inferred",
            "commitment_state": "open",
            "confidence": 0.58,
            "salience": 0.36,
            "formed_at": "2026-06-20T11:00:00+09:00",
            "last_confirmed_at": None,
            "valid_from": None,
            "valid_to": "2026-06-21T03:00:00+09:00",
            "evidence_event_ids": [],
            "evidence_cycle_ids": ["cycle:source"],
            "qualifiers": {
                "source": "assistant_response",
                "commitment_actor": "self",
                "scope_duration": "session",
            },
        }
        action = service.memory.action_resolver.build_memory_action(
            operation="new",
            memory_set_id=memory_set_id,
            finished_at="2026-06-20T11:00:00+09:00",
            memory_unit=unit,
            related_memory_unit_ids=[],
            before_snapshot=None,
            after_snapshot=unit,
            reason="test commitment",
            event_ids=[],
        )
        service.store.persist_turn_consolidation(
            episode=None,
            memory_actions=[action],
            episode_affects=[],
        )
        return unit


if __name__ == "__main__":
    unittest.main()
