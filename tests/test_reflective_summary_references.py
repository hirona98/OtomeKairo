from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.memory.reflection.constants import REFLECTION_SUMMARY_BATCH_LIMIT
from otomekairo.memory.reflection.summary import MemoryReflectionSummaryMixin
from otomekairo.service.memory import ServiceMemoryMixin
from otomekairo.store.file_store import FileStore


class ReflectiveSummaryReferenceTests(unittest.TestCase):
    def generate(self, payload: Any, *, count: int = 2) -> tuple[dict, dict, Mock]:
        reflection = MemoryReflectionSummaryMixin()
        reflection.llm = Mock()
        reflection.llm.generate_memory_reflection_summary.return_value = payload
        return self.generate_with(reflection, count=count)

    def generate_with(self, reflection: MemoryReflectionSummaryMixin, *, count: int) -> tuple[dict, dict, Mock]:
        trace = reflection._empty_summary_generation()
        reflection.llm.review_memory_reflection_summaries.side_effect = lambda **kw: {"reviews": [
            {"scope_ref": row["scope_ref"], "scope_supported": True, "supported": True, "reason_summary": "照合済み。"}
            for row in kw["source_pack"]["summary_candidates"]
        ]}
        texts = reflection._generate_reflective_summary_texts(
            memory_set_id="memory_set:test",
            prepared_scopes=[
                {
                    "scope_ref": f"scope:{i}",
                    "scope_type": "entity",
                    "scope_key": f"person:{i}",
                    "evidence_pack": {"scope_ref": f"scope:{i}", "episodes": [], "memory_units": []},
                }
                for i in range(count)
            ],
            persona_context=object(),
            reflection_summary_model_config={"model": "mock"},
            summary_generation=trace,
        )
        return texts, trace, reflection.llm

    def test_duplicate_ref_fails_scope_regardless_of_text_or_order(self) -> None:
        valid = {"scope_ref": "scope:0", "summary_text": "継続して見られる傾向。"}
        for other in [valid, {**valid, "summary_text": "別の傾向。"}, {**valid, "summary_text": ""}]:
            for duplicates in [[valid, other], [other, valid]]:
                with self.subTest(duplicates=duplicates):
                    texts, trace, llm = self.generate({"summaries": [
                        duplicates[0],
                        {"scope_ref": "scope:1", "summary_text": "正常な要約。"},
                        duplicates[1],
                    ]})
                    self.assertEqual(texts, {"scope:1": "正常な要約。"})
                    self.assertEqual(trace["response_errors"], [])
                    self.assertEqual(len(trace["failed_scopes"]), 1)
                    failure = trace["failed_scopes"][0]
                    self.assertEqual(failure["scope_key"], "person:0")
                    self.assertEqual(failure["failure_stage"], "contract_validation")
                    self.assertIn("more than once", failure["failure_reason"])
                    self.assertEqual(trace["llm_call_count"], 2)
                    llm.generate_memory_reflection_summary.assert_called_once()

    def test_missing_ref_is_one_real_scope_failure(self) -> None:
        texts, trace, _ = self.generate({"summaries": [
            {"scope_ref": "scope:1", "summary_text": "正常な要約。"},
        ]})
        self.assertEqual(texts, {"scope:1": "正常な要約。"})
        self.assertEqual(len(trace["failed_scopes"]), 1)
        self.assertEqual(trace["failed_scopes"][0]["scope_key"], "person:0")
        self.assertIn("not returned", trace["failed_scopes"][0]["failure_reason"])
        self.assertEqual(trace["response_errors"], [])

    def test_unmapped_items_are_recorded_without_raw_output_and_good_scopes_survive(self) -> None:
        for invalid in [
            None, [], "unmapped output", {},
            {"scope_ref": []}, {"scope_ref": {}}, {"scope_ref": 0}, {"scope_ref": None},
            {"scope_ref": "scope:unknown", "summary_text": "対応不明の本文。"},
        ]:
            with self.subTest(invalid=invalid):
                texts, trace, llm = self.generate({"summaries": [
                    {"scope_ref": "scope:1", "summary_text": "別の正常な要約。"},
                    invalid,
                    {"scope_ref": "scope:0", "summary_text": "正常な要約。"},
                ]})
                self.assertEqual(set(texts), {"scope:0", "scope:1"})
                self.assertEqual(trace["failed_scopes"], [])
                self.assertEqual(len(trace["response_errors"]), 1)
                error = trace["response_errors"][0]
                self.assertEqual(set(error), {"batch_scope_refs", "item_index", "failure_stage", "failure_reason"})
                self.assertEqual(error["batch_scope_refs"], ["scope:0", "scope:1"])
                self.assertEqual(error["item_index"], 1)
                self.assertNotIn("対応不明の本文", json.dumps(trace, ensure_ascii=False))
                llm.generate_memory_reflection_summary.assert_called_once()

    def test_unknown_ref_does_not_replace_a_missing_requested_ref(self) -> None:
        texts, trace, _ = self.generate({"summaries": [
            {"scope_ref": "scope:99", "summary_text": "対応先を推測してはいけない本文。"},
            {"scope_ref": "scope:1", "summary_text": "正常な要約。"},
        ]})
        self.assertEqual(set(texts), {"scope:1"})
        self.assertEqual(len(trace["response_errors"]), 1)
        self.assertEqual([failure["scope_key"] for failure in trace["failed_scopes"]], ["person:0"])

    def test_refs_are_matched_within_each_batch(self) -> None:
        reflection = MemoryReflectionSummaryMixin()
        reflection.llm = Mock()
        limit = REFLECTION_SUMMARY_BATCH_LIMIT
        reflection.llm.generate_memory_reflection_summary.side_effect = [
            {"summaries": [
                {"scope_ref": f"scope:{i}", "summary_text": "最初のバッチの要約。"}
                for i in range(limit)
            ] + [{"scope_ref": f"scope:{limit}", "summary_text": "次のバッチへの誤参照。"}]},
            {"summaries": [
                {"scope_ref": "scope:0", "summary_text": "先のバッチへの誤参照。"},
                {"scope_ref": f"scope:{limit}", "summary_text": "次のバッチの正常な要約。"},
            ]},
        ]
        texts, trace, llm = self.generate_with(reflection, count=limit + 1)
        self.assertEqual(len(texts), limit + 1)
        self.assertEqual(texts["scope:0"], "最初のバッチの要約。")
        self.assertEqual(texts[f"scope:{limit}"], "次のバッチの正常な要約。")
        self.assertEqual(trace["failed_scopes"], [])
        self.assertEqual(len(trace["response_errors"]), 2)
        self.assertEqual(trace["llm_call_count"], 4)
        self.assertEqual(llm.generate_memory_reflection_summary.call_count, 2)

    def test_invalid_envelope_fails_current_batch_and_continues_next(self) -> None:
        for payload in [None, [], {}, {"summaries": None}, {"summaries": [], "extra": True}]:
            with self.subTest(payload=payload):
                reflection = MemoryReflectionSummaryMixin()
                reflection.llm = Mock()
                limit = REFLECTION_SUMMARY_BATCH_LIMIT
                reflection.llm.generate_memory_reflection_summary.side_effect = [
                    payload, {"summaries": [
                        {"scope_ref": f"scope:{limit}", "summary_text": "正常な次のバッチ。"},
                    ]},
                ]
                texts, trace, _ = self.generate_with(reflection, count=limit + 1)
                self.assertEqual(set(texts), {f"scope:{limit}"})
                self.assertEqual(len(trace["failed_scopes"]), limit)
                self.assertTrue(all(row["failure_stage"] == "contract_validation" for row in trace["failed_scopes"]))
                self.assertEqual(trace["llm_call_count"], 3)

    def test_provider_failure_fails_current_batch_and_continues_next(self) -> None:
        reflection = MemoryReflectionSummaryMixin()
        reflection.llm = Mock()
        limit = REFLECTION_SUMMARY_BATCH_LIMIT
        reflection.llm.generate_memory_reflection_summary.side_effect = [
            RuntimeError("provider unavailable"), {"summaries": [
                {"scope_ref": f"scope:{limit}", "summary_text": "正常な次のバッチ。"},
            ]},
        ]
        texts, trace, _ = self.generate_with(reflection, count=limit + 1)
        self.assertEqual(set(texts), {f"scope:{limit}"})
        self.assertEqual(len(trace["failed_scopes"]), limit)
        self.assertTrue(all(row["failure_stage"] == "generate_summary_text" for row in trace["failed_scopes"]))
        self.assertEqual(trace["llm_call_count"], 3)

    def test_no_prepared_scopes_means_no_call(self) -> None:
        texts, trace, llm = self.generate({"summaries": []}, count=0)
        self.assertEqual(texts, {})
        self.assertEqual(trace["failed_scopes"], [])
        self.assertEqual(trace["response_errors"], [])
        self.assertEqual(trace["llm_call_count"], 0)
        llm.generate_memory_reflection_summary.assert_not_called()

    def test_failed_scope_keeps_stored_text_and_retries_alone_on_next_reflection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FileStore(Path(directory))
            state = store.read_state()
            memory_set_id = state["selected_memory_set_id"]
            llm = Mock()
            llm.review_memory_reflection_summaries.side_effect = lambda **kw: {"reviews": [
                {"scope_ref": row["scope_ref"], "scope_supported": True, "supported": True, "reason_summary": "照合済み。"}
                for row in kw["source_pack"]["summary_candidates"]
            ]}
            reflection = MemoryConsolidator(store=store, llm=llm).reflective
            reflection.vector_indexer = Mock()
            seeds = []
            for scope_key in ["person:0", "person:1"]:
                for i in range(4):
                    store.append_events(events=[{
                        "event_id": f"event:{scope_key}:{i}", "cycle_id": f"cycle:{scope_key}:{i}",
                        "memory_set_id": memory_set_id, "created_at": "2026-10-25T09:00:00+09:00",
                        "kind": "conversation_input", "role": "user", "speaker_ref": scope_key,
                        "text": "継続している根拠。",
                    }])
                    candidate = {
                        "memory_type": "summary" if i == 3 else "preference",
                        "scope_type": "entity", "scope_key": scope_key, "subject_ref": scope_key,
                        "predicate": "long_term_pattern" if i == 3 else f"preference_{i}",
                        "object_ref_or_value": None,
                        "summary_text": "従来の要約。" if i == 3 else "継続している根拠。",
                        "status": "inferred", "commitment_state": None,
                        "confidence": 0.7, "salience": 0.5,
                        "valid_from": None, "valid_to": None,
                        "qualifiers": {"summary_scope": "entity"} if i == 3 else {},
                        "reason": "テスト用の根拠。",
                    }
                    actions = reflection.action_resolver.resolve_memory_actions(
                        memory_set_id=memory_set_id, finished_at="2026-10-25T09:00:00+09:00",
                        event_ids=[f"event:{scope_key}:{i}"], cycle_ids=[f"cycle:{scope_key}:{i}"],
                        candidate=candidate, allow_summary=True,
                    )
                    store.persist_memory_actions(memory_actions=actions)
                    seeds.extend(actions)

            llm.generate_memory_reflection_summary.return_value = {"summaries": [
                {"scope_ref": "scope:0", "summary_text": "重複した最初の文面。"},
                {"scope_ref": "scope:1", "summary_text": "正常に更新した要約。"},
                {"scope_ref": "scope:0", "summary_text": "重複した次の文面。"},
                {"scope_ref": "scope:unknown", "summary_text": "対応不明の文面。"},
            ]}
            with patch.object(reflection, "_reflective_trigger_reasons", return_value=["chat_turn_interval"]):
                first = reflection.run(
                    state=state, finished_at="2026-10-25T09:01:00+09:00",
                    episode={}, memory_actions=seeds,
                )
            self.assertEqual(first["result_status"], "updated", first)
            trace = first["summary_generation"]
            self.assertEqual(trace["requested_scope_count"], 2)
            self.assertEqual(trace["succeeded_scope_count"], 1)
            self.assertEqual(len(trace["failed_scopes"]), 1)
            self.assertEqual(len(trace["response_errors"]), 1)
            self.assertEqual(store.get_latest_reflection_run(memory_set_id)["summary_generation"], trace)

            def stored_summaries() -> dict[str, dict]:
                return {
                    unit["scope_key"]: unit
                    for unit in store.list_memory_units_for_reflection(
                        memory_set_id=memory_set_id, include_memory_types=["summary"], limit=10,
                    )
                }

            after_first = stored_summaries()
            self.assertEqual(after_first["person:0"]["summary_text"], "従来の要約。")
            self.assertEqual(after_first["person:1"]["summary_text"], "正常に更新した要約。")
            llm.generate_memory_reflection_summary.assert_called_once()
            llm.reset_mock()
            llm.generate_memory_reflection_summary.return_value = {"summaries": [
                {"scope_ref": "scope:0", "summary_text": "再試行で更新した要約。"},
            ]}
            with patch.object(reflection, "_reflective_trigger_reasons", return_value=["chat_turn_interval"]):
                second = reflection.run(
                    state=state, finished_at="2026-10-25T09:02:00+09:00",
                    episode={}, memory_actions=[],
                )
            self.assertEqual(second["result_status"], "updated", second)
            retry = second["summary_generation"]
            self.assertEqual(retry["requested_scope_count"], 1)
            self.assertEqual(retry["succeeded_scope_count"], 1)
            self.assertEqual(retry["failed_scopes"], [])
            self.assertEqual(retry["response_errors"], [])
            self.assertEqual(retry["dirty_reasons"], [
                {"scope_type": "entity", "scope_key": "person:0", "reasons": ["previous_failure"]},
            ])
            after_retry = stored_summaries()
            self.assertEqual(after_retry["person:0"]["summary_text"], "再試行で更新した要約。")
            self.assertEqual(after_retry["person:1"], after_first["person:1"])
            llm.generate_memory_reflection_summary.assert_called_once()

    def test_scope_and_unmapped_item_audits_are_persisted_separately(self) -> None:
        for include_scope_failure in [False, True]:
            with self.subTest(include_scope_failure=include_scope_failure), tempfile.TemporaryDirectory() as directory:
                store = FileStore(Path(directory))
                service = ServiceMemoryMixin()
                service.store = store
                service._now_iso = lambda: "2026-10-25T09:00:00+09:00"
                summaries = [
                    {"scope_ref": "scope:unknown", "summary_text": "監査に残さない本文。"},
                    {"scope_ref": "scope:1", "summary_text": "正常な要約。"},
                ]
                if not include_scope_failure:
                    summaries.append({"scope_ref": "scope:0", "summary_text": "もう一つの正常な要約。"})
                _, trace, _ = self.generate({"summaries": summaries})
                service._append_reflective_summary_generation_failure_events(
                    cycle_id="cycle:test", memory_set_id=store.read_state()["selected_memory_set_id"],
                    memory_trace={"reflective_consolidation": {"summary_generation": trace}},
                )
                with sqlite3.connect(Path(directory) / "memory.db") as conn:
                    events = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM events")]
                self.assertEqual(len(events), 2 if include_scope_failure else 1)
                by_kind = {event["kind"]: event for event in events}
                response = by_kind["reflective_summary_response_contract_failure"]
                response_error = trace["response_errors"][0]
                self.assertEqual({key: response[key] for key in response_error}, response_error)
                self.assertNotIn("監査に残さない本文", json.dumps(events, ensure_ascii=False))
                if include_scope_failure:
                    scope_event = by_kind["reflective_summary_generation_failure"]
                    failure = trace["failed_scopes"][0]
                    self.assertEqual(
                        {key: scope_event[key] for key in failure}, failure,
                    )
