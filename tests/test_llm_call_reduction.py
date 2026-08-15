from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from otomekairo.llm.contracts import (
    validate_event_evidence_contract,
    validate_memory_interpretation_contract,
    validate_memory_reflection_summary_contract,
)
from otomekairo.llm.mock import MockLLMClient
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.correction import MemoryCorrectionReconciler
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.memory.reflection.summary import MemoryReflectionSummaryMixin
from otomekairo.memory.utils import source_text_hash
from otomekairo.memory.vector import MemoryVectorIndexer
from otomekairo.recall.event_evidence import PRECISE_EVENT_EVIDENCE_LIMIT, RecallEventEvidenceMixin
from otomekairo.store.file_store import FileStore


class _DirtyScopeHarness(MemoryReflectionSummaryMixin):
    def __init__(self) -> None:
        self.llm = Mock()
        self.action_resolver = Mock()
        self.action_resolver.resolve_memory_actions.return_value = []


class LlmCallReductionTests(unittest.TestCase):
    def test_precise_event_evidence_limit_stays_eight(self) -> None:
        self.assertEqual(PRECISE_EVENT_EVIDENCE_LIMIT, 8)

    def test_dirty_scope_ignores_untouched_active_memory(self) -> None:
        harness = _DirtyScopeHarness()
        dirty = harness._reflective_dirty_scope_index(
            episode_groups={("relationship", "self|person:a"): [{"primary_scope_type": "relationship"}]},
            memory_groups={
                ("relationship", "self|person:a"): [{"summary_text": "関係"}],
                ("topic", "topic:old"): [{"summary_text": "古い話題"}, {"summary_text": "続き"}],
            },
            summary_groups={
                ("relationship", "self|person:a"): [{"summary_text": "既存"}],
                ("topic", "topic:old"): [{"summary_text": "既存要約"}],
            },
            memory_actions=[],
            affect_state_updates=[],
            previous_failed_scopes=[],
            trigger_reasons=[],
        )
        self.assertIn(("relationship", "self|person:a"), dirty)
        self.assertNotIn(("topic", "topic:old"), dirty)
        self.assertEqual(dirty[("relationship", "self|person:a")], ["episode"])

    def test_dirty_scope_includes_memory_action_affect_failure_and_missing_summary(self) -> None:
        harness = _DirtyScopeHarness()
        dirty = harness._reflective_dirty_scope_index(
            episode_groups={},
            memory_groups={
                ("self", "self"): [
                    {"summary_text": "自分", "evidence_cycle_ids": ["cycle:1"]},
                    {"summary_text": "続き", "evidence_cycle_ids": ["cycle:2"]},
                    {"summary_text": "反復", "evidence_cycle_ids": ["cycle:3"]},
                ],
                ("entity", "person:b"): [
                    {"summary_text": "相手", "evidence_cycle_ids": ["cycle:4"]},
                    {"summary_text": "別面", "evidence_cycle_ids": ["cycle:5"]},
                    {"summary_text": "三件目", "evidence_cycle_ids": ["cycle:6"]},
                ],
            },
            summary_groups={},
            memory_actions=[
                {
                    "memory_unit": {
                        "scope_type": "topic",
                        "scope_key": "topic:new",
                    }
                }
            ],
            affect_state_updates=[
                {
                    "update_kind": "updated",
                    "target_scope_type": "relationship",
                    "target_scope_key": "self|person:c",
                },
                {
                    "update_kind": "weakened",
                    "target_scope_type": "relationship",
                    "target_scope_key": "self|person:skip",
                },
            ],
            previous_failed_scopes=[{"scope_type": "entity", "scope_key": "place:home"}],
            trigger_reasons=["self_change"],
        )
        self.assertIn(("topic", "topic:new"), dirty)
        self.assertEqual(dirty[("topic", "topic:new")], ["memory_action"])
        self.assertIn(("relationship", "self|person:c"), dirty)
        self.assertNotIn(("relationship", "self|person:skip"), dirty)
        self.assertIn(("entity", "place:home"), dirty)
        self.assertIn("self_change", dirty[("self", "self")])
        self.assertIn("missing_summary", dirty[("self", "self")])
        self.assertIn("missing_summary", dirty[("entity", "person:b")])

    def test_mood_alone_does_not_dirty_self(self) -> None:
        harness = _DirtyScopeHarness()
        dirty = harness._reflective_dirty_scope_index(
            episode_groups={},
            memory_groups={("self", "self"): [{"summary_text": "自分"}]},
            summary_groups={("self", "self"): [{"summary_text": "既存"}]},
            memory_actions=[],
            affect_state_updates=[],
            previous_failed_scopes=[],
            trigger_reasons=["elapsed_24h"],
        )
        self.assertEqual(dirty, {})

    def test_reflection_summary_generation_batches_scopes_into_one_call(self) -> None:
        harness = _DirtyScopeHarness()
        harness.llm.generate_memory_reflection_summary.return_value = {
            "summaries": [
                {"scope_ref": "scope:0", "summary_text": "関係の傾向が続いている。"},
                {"scope_ref": "scope:1", "summary_text": "自分側の応答の型が見えている。"},
            ]
        }
        prepared = [
            {
                "scope_ref": "scope:0",
                "scope_type": "relationship",
                "scope_key": "self|person:a",
                "evidence_pack": {"scope_ref": "scope:0", "scope_type": "relationship"},
            },
            {
                "scope_ref": "scope:1",
                "scope_type": "self",
                "scope_key": "self",
                "evidence_pack": {"scope_ref": "scope:1", "scope_type": "self"},
            },
        ]
        summary_generation = harness._empty_summary_generation()
        texts = harness._generate_reflective_summary_texts(
            prepared_scopes=prepared,
            persona_context=object(),
            reflection_summary_model_config={"model": "mock"},
            summary_generation=summary_generation,
        )
        self.assertEqual(harness.llm.generate_memory_reflection_summary.call_count, 1)
        self.assertEqual(summary_generation["llm_call_count"], 1)
        self.assertEqual(set(texts), {"scope:0", "scope:1"})

    def test_reflection_batch_contract_is_envelope_only(self) -> None:
        validate_memory_reflection_summary_contract(
            {
                "summaries": [
                    {"scope_ref": "scope:0", "summary_text": "最近の関係では距離感が安定している。"},
                    {"scope_ref": "scope:1", "summary_text": ""},
                ]
            }
        )

    def test_event_evidence_batch_contract_is_envelope_only(self) -> None:
        validate_event_evidence_contract(
            {
                "evidence": [
                    {
                        "event_ref": "event:0",
                        "anchor": "前回の相談の場面",
                        "topic": "体調",
                        "decision_or_result": None,
                        "tone_or_note": None,
                    },
                    {
                        "event_ref": "event:1",
                        "anchor": None,
                        "topic": None,
                        "decision_or_result": None,
                        "tone_or_note": None,
                    },
                ]
            }
        )

    def test_reflection_keeps_valid_items_when_one_item_is_invalid(self) -> None:
        harness = _DirtyScopeHarness()
        harness.llm.generate_memory_reflection_summary.return_value = {
            "summaries": [
                {"scope_ref": "scope:0", "summary_text": ""},
                {"scope_ref": "scope:1", "summary_text": "自分側の応答の型が見えている。"},
            ]
        }
        prepared = [
            {
                "scope_ref": "scope:0",
                "scope_type": "relationship",
                "scope_key": "self|person:a",
                "evidence_pack": {"scope_ref": "scope:0"},
            },
            {
                "scope_ref": "scope:1",
                "scope_type": "self",
                "scope_key": "self",
                "evidence_pack": {"scope_ref": "scope:1"},
            },
        ]
        summary_generation = harness._empty_summary_generation()
        texts = harness._generate_reflective_summary_texts(
            prepared_scopes=prepared,
            persona_context=object(),
            reflection_summary_model_config={"model": "mock"},
            summary_generation=summary_generation,
        )
        self.assertEqual(set(texts), {"scope:1"})
        self.assertEqual(len(summary_generation["failed_scopes"]), 1)
        self.assertEqual(summary_generation["failed_scopes"][0]["scope_key"], "self|person:a")

    def test_event_evidence_keeps_valid_items_when_one_item_is_invalid(self) -> None:
        mixin = RecallEventEvidenceMixin()
        generated = mixin._event_evidence_items_from_batch_payload(
            {
                "evidence": [
                    {
                        "event_ref": "event:0",
                        "anchor": "前回の相談の場面",
                        "topic": None,
                        "decision_or_result": None,
                        "tone_or_note": None,
                    },
                    {
                        "event_ref": "event:1",
                        "anchor": None,
                        "topic": None,
                        "decision_or_result": None,
                        "tone_or_note": None,
                    },
                ]
            }
        )
        self.assertIsInstance(generated["event:0"], dict)
        self.assertIsInstance(generated["event:1"], Exception)

    def test_memory_interpretation_allows_invalid_correction_without_failing_turn_contract(self) -> None:
        base = {
            "episode": {
                "episode_type": "conversation",
                "episode_series_id": None,
                "primary_scope_type": "self",
                "primary_scope_key": "self",
                "summary_text": "短い会話だった。",
                "outcome_text": None,
                "open_loops": [],
                "salience": 0.4,
            },
            "candidate_memory_units": [],
            "episode_affects": [],
        }
        validate_memory_interpretation_contract(base)
        validate_memory_interpretation_contract(
            {
                **base,
                "correction_status": "selected",
                "selected_targets": [],
            }
        )
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        self.assertIsNone(
            consolidator._memory_interpretation_correction_selection(
                {
                    **base,
                    "correction_status": "selected",
                    "selected_targets": [],
                }
            )
        )
        self.assertEqual(
            consolidator._memory_interpretation_correction_selection(
                {
                    **base,
                    "correction_status": "no_correction",
                    "selected_targets": [],
                }
            ),
            {"correction_status": "no_correction", "selected_targets": []},
        )

    def test_mock_reflection_and_event_evidence_use_batch_payloads(self) -> None:
        client = MockLLMClient()
        reflection = client.generate_memory_reflection_summary(
            {"model": "mock"},
            {
                "scopes": [
                    {
                        "scope_ref": "scope:0",
                        "scope_type": "topic",
                        "scope_key": "topic:health",
                        "scope_label": "体調",
                        "evidence_counts": {"open_loops": 1},
                    }
                ]
            },
        )
        self.assertEqual(reflection["summaries"][0]["scope_ref"], "scope:0")
        self.assertTrue(reflection["summaries"][0]["summary_text"])

        evidence = client.generate_event_evidence(
            {"model": "mock"},
            {
                "primary_recall_focus": "episodic",
                "time_reference": "past",
                "events": [
                    {
                        "event_ref": "event:0",
                        "kind": "decision",
                        "retrieval_sections": ["episodic_evidence"],
                        "reason_summary": "様子を見ることにした。",
                    },
                    {
                        "event_ref": "event:1",
                        "kind": "speech",
                        "retrieval_sections": ["active_topics"],
                        "text": "また話そう。",
                    },
                ],
            },
        )
        self.assertEqual([item["event_ref"] for item in evidence["evidence"]], ["event:0", "event:1"])

    def test_correction_apply_does_not_call_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            resolver = Mock()
            resolver.build_revoked_memory_unit.return_value = {
                "memory_set_id": "memory_set:test",
                "memory_unit_id": "memory_unit:created",
                "status": "revoked",
            }
            resolver.build_memory_action.return_value = {
                "operation": "correct",
                "memory_unit_id": "memory_unit:created",
                "revision_id": "revision:new",
                "correction": {
                    "correction_group_id": "correction:1",
                    "corrects_revision_id": "revision:old",
                    "correction_kind": "revoke_created",
                },
            }
            reconciler = MemoryCorrectionReconciler(store=store, action_resolver=resolver)
            actions, trace = reconciler.run(
                context={
                    "targets": [
                        {
                            "operation": "create",
                            "memory_unit": {
                                "memory_set_id": "memory_set:test",
                                "memory_unit_id": "memory_unit:created",
                                "status": "inferred",
                            },
                            "revision": {"revision_id": "revision:old"},
                        }
                    ],
                    "selection": {
                        "correction_status": "selected",
                        "selected_targets": [
                            {
                                "revision_id": "revision:old",
                                "memory_unit_id": "memory_unit:created",
                                "correction_kind": "revoke_created",
                                "reason_summary": "さっきの理解は誤りだった。",
                            }
                        ],
                    },
                    "event_ids": ["event:1"],
                    "cycle_ids": ["cycle:1"],
                },
                finished_at="2026-07-20T12:00:00+09:00",
            )
            self.assertEqual(len(actions), 1)
            self.assertEqual(trace["selection_status"], "selected")
            self.assertEqual(trace["selected_target_count"], 1)

    def test_broken_correction_selection_fails_only_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            reconciler = MemoryCorrectionReconciler(store=store, action_resolver=Mock())
            actions, trace = reconciler.run(
                context={
                    "targets": [{"revision": {"revision_id": "revision:old"}, "memory_unit": {}}],
                    "selection": {"correction_status": "selected", "selected_targets": []},
                },
                finished_at="2026-07-20T12:00:00+09:00",
            )
            self.assertEqual(actions, [])
            self.assertEqual(trace["result_status"], "failed")

    def test_vector_sync_skips_embedding_when_text_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            state = store.read_state()
            memory_set_id = state["selected_memory_set_id"]
            state["memory_sets"][memory_set_id]["embedding"]["embedding_dimension"] = 3
            llm = Mock()
            llm.generate_embeddings.return_value = [[0.1, 0.2, 0.3]]
            indexer = MemoryVectorIndexer(store=store, llm=llm)
            episode = {
                "memory_set_id": memory_set_id,
                "episode_id": "episode:same",
                "summary_text": "同じ本文",
                "outcome_text": None,
                "open_loops": [],
                "primary_scope_type": "self",
                "primary_scope_key": "self",
                "episode_type": "conversation",
                "salience": 0.4,
            }
            indexer.sync(
                state=state,
                finished_at="2026-07-20T12:00:00+09:00",
                episode=episode,
                memory_actions=[],
            )
            self.assertEqual(llm.generate_embeddings.call_count, 1)
            indexer.sync(
                state=state,
                finished_at="2026-07-20T13:00:00+09:00",
                episode=episode,
                memory_actions=[],
            )
            self.assertEqual(llm.generate_embeddings.call_count, 1)

    def test_semantic_compare_reembeds_when_stored_text_hash_differs(self) -> None:
        resolver = MemoryActionResolver.__new__(MemoryActionResolver)
        resolver.llm = Mock()
        resolver.llm.generate_embeddings.return_value = [[1.0, 0.0], [0.0, 1.0]]
        store = Mock()
        store.get_memory_unit_embeddings.return_value = {
            "memory_unit:old": {
                "embedding": [0.5, 0.5],
                "text_hash": "stale-hash",
            }
        }
        resolver.store = store
        matches = resolver._annotate_semantic_matches(
            matches=[
                {
                    "memory_unit_id": "memory_unit:old",
                    "memory_type": "interpretation",
                    "summary_text": "今の本文",
                    "predicate": "thinks",
                    "object_ref_or_value": "topic:now",
                    "qualifiers": {},
                }
            ],
            candidate={
                "memory_type": "interpretation",
                "status": "inferred",
                "summary_text": "新しい本文",
                "predicate": "thinks",
                "object_ref_or_value": "topic:now",
                "qualifiers": {},
            },
            embedding_definition={"embedding_dimension": 2},
            memory_set_id="memory_set:test",
        )
        self.assertEqual(resolver.llm.generate_embeddings.call_count, 1)
        self.assertEqual(len(resolver.llm.generate_embeddings.call_args.kwargs["texts"]), 2)
        self.assertIn("_semantic_similarity", matches[0])
        self.assertNotEqual(source_text_hash("今の本文"), "stale-hash")

    def test_reflection_watermark_uses_last_evaluated_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            memory_set_id = store.read_state()["selected_memory_set_id"]
            store.upsert_reflection_run(
                reflection_run={
                    "reflection_run_id": "reflection_run:updated",
                    "memory_set_id": memory_set_id,
                    "started_at": "2026-07-20T10:00:00+09:00",
                    "finished_at": "2026-07-20T10:01:00+09:00",
                    "result_status": "updated",
                }
            )
            store.upsert_reflection_run(
                reflection_run={
                    "reflection_run_id": "reflection_run:no_change",
                    "memory_set_id": memory_set_id,
                    "started_at": "2026-07-20T11:00:00+09:00",
                    "finished_at": "2026-07-20T11:01:00+09:00",
                    "result_status": "no_change",
                }
            )
            store.upsert_reflection_run(
                reflection_run={
                    "reflection_run_id": "reflection_run:failed",
                    "memory_set_id": memory_set_id,
                    "started_at": "2026-07-20T12:00:00+09:00",
                    "finished_at": "2026-07-20T12:01:00+09:00",
                    "result_status": "failed",
                }
            )
            evaluated = store.get_latest_reflection_run(
                memory_set_id,
                result_statuses=("updated", "no_change"),
            )
            self.assertIsNotNone(evaluated)
            assert evaluated is not None
            self.assertEqual(evaluated["reflection_run_id"], "reflection_run:no_change")


if __name__ == "__main__":
    unittest.main()
