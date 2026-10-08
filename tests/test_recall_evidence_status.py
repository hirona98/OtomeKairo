import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from otomekairo.llm.prompts import _compact_recall_pack
from otomekairo.recall.builder import RecallBuilder
from otomekairo.store.memory_links import StoreMemoryLinksMixin


class RecallEvidenceStatusTests(unittest.TestCase):
    def test_event_evidence_preserves_recorded_people_and_time_through_model_projection(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        records = [
            {"event_id": "event:owner", "kind": "conversation_input", "role": "person",
             "speaker_ref": "person:owner", "participant_refs": ["person:owner"],
             "interaction_ref": "interaction:owner", "created_at": "2026-10-04T09:00:06+09:00",
             "text": "私とは別人の、以前に指定した方へ題名だけを共有してください。"},
            {"event_id": "event:recipient", "kind": "speech", "role": "assistant",
             "speaker_ref": None, "participant_refs": ["person:recipient"],
             "interaction_ref": "interaction:recipient", "created_at": "2026-10-04T09:00:07+09:00",
             "text": "許可された題名です。"},
        ]
        builder.store = SimpleNamespace(load_events_for_evidence=Mock(side_effect=[records, []]))
        builder.llm = SimpleNamespace(generate_event_evidence=Mock(return_value={"evidence": [
            {"event_ref": f"event:{index}", "anchor": "記録された場面", "topic": None,
             "decision_or_result": "題名だけの共有", "tone_or_note": None}
            for index in range(2)
        ]}))
        result = builder._build_event_evidence(
            memory_set_id="memory_set:test", primary_recall_focus="fact",
            recall_hint={"secondary_recall_focuses": [], "time_reference": "past", "risk_flags": []},
            sections={"episodic_evidence": [{"source_kind": "episode", "summary_text": "共有の出来事。",
                                            "linked_event_ids": [record["event_id"] for record in records]}]},
            model_config={"model": "mock"},
            persona_context=SimpleNamespace(to_prompt_payload=lambda: {}),
        )
        projected = _compact_recall_pack(result)["event_evidence"]
        source_events = builder.llm.generate_event_evidence.call_args.kwargs["source_pack"]["events"]
        by_id = {record["event_id"]: record for record in records}
        for source, evidence, compact in zip(source_events, result["event_evidence"], projected, strict=True):
            record = by_id[evidence["event_id"]]
            for key in ("role", "speaker_ref", "participant_refs", "interaction_ref", "created_at"):
                self.assertEqual(source[key], record[key])
                self.assertEqual(evidence[key], record[key])
                if record[key] is not None:
                    self.assertEqual(compact[key], record[key])
            self.assertNotIn("event_id", compact)
        self.assertEqual(result["event_evidence_generation"]["failed_items"], [])

    def test_event_evidence_without_person_provenance_does_not_invent_a_person(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        source = builder._event_evidence_source_event({"kind": "observation", "role": "system",
                                                     "text": "外界の観測。"})
        evidence = builder._event_evidence_item_from_payload(
            event_id="event:self", kind="observation", source_item=source,
            payload={"anchor": "外界の観測", "topic": None, "decision_or_result": None, "tone_or_note": None},
        )
        compact = _compact_recall_pack({"event_evidence": [evidence]})["event_evidence"][0]
        for item in (source, evidence, compact):
            self.assertNotIn("speaker_ref", item)
            self.assertNotIn("participant_refs", item)
            self.assertNotIn("interaction_ref", item)

    def test_update_source_reports_read_only_selected_direct_inactive_parent(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        report = {
            "event_id": "event:owner", "role": "person", "text": "架空の本人報告。" * 500,
            "speaker_ref": "person:owner", "participant_refs": ["person:owner"],
            "interaction_ref": "interaction:owner", "created_at": "2026-10-04T09:00:39+09:00",
        }
        assistant = {**report, "event_id": "event:assistant", "role": "assistant"}
        builder.store = SimpleNamespace(load_events_for_evidence=Mock(return_value=[report, assistant]))
        direct = {
            "label": "derived_from", "source_memory_unit_id": "memory:selected",
            "target_memory_unit_id": "memory:prior",
            "target_memory_unit": {"status": "superseded", "evidence_event_ids": ["event:owner", "event:assistant"]},
        }
        links = [direct, direct, {**direct, "label": "about_same_scope"},
                 {**direct, "source_memory_unit_id": "memory:prior"},
                 {**direct, "target_memory_unit": {"status": "confirmed", "evidence_event_ids": ["event:excluded"]}}]
        reports = builder._build_memory_link_source_reports(
            memory_set_id="memory_set:test", memory_links=links, selected_memory_ids=["memory:selected"],
        )
        builder.store.load_events_for_evidence.assert_called_once_with(
            memory_set_id="memory_set:test", event_ids=["event:owner", "event:assistant"], limit=16,
        )
        self.assertEqual(reports, [{key: report[key] for key in
                                   ("role", "text", "speaker_ref", "participant_refs", "interaction_ref", "created_at")}])
        compact = _compact_recall_pack({"memory_link_context": {"source_reports": reports}})
        self.assertEqual(compact["memory_link_context"]["source_reports"], reports)
        self.assertNotIn("event_id", reports[0])

    def test_update_source_reports_bound_events_and_fail_on_missing_evidence(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        ids = [f"event:{index}" for index in range(20)]
        link = {"label": "derived_from", "source_memory_unit_id": "memory:selected",
                "target_memory_unit": {"status": "revoked", "evidence_event_ids": ids}}
        builder.store = SimpleNamespace(load_events_for_evidence=Mock(return_value=[
            {"event_id": event_id, "role": "person", "text": "架空の報告。"} for event_id in ids[:16]
        ]))
        reports = builder._build_memory_link_source_reports(
            memory_set_id="memory_set:test", memory_links=[link], selected_memory_ids=["memory:selected"],
        )
        self.assertEqual(len(reports), 16)
        builder.store.load_events_for_evidence.assert_called_once_with(
            memory_set_id="memory_set:test", event_ids=ids[:16], limit=16,
        )
        builder.store.load_events_for_evidence.return_value = []
        with self.assertRaisesRegex(ValueError, "更新元の根拠 event"):
            builder._build_memory_link_source_reports(
                memory_set_id="memory_set:test", memory_links=[link], selected_memory_ids=["memory:selected"],
            )

    def test_search_rationale_stays_in_audit_without_becoming_retrieved_evidence(self) -> None:
        contract = {
            "contract": "summary", "reason_codes": ["想起前には仮題の根拠がない"],
            "boundary": "none", "target_actor": "person", "target_person_ref": "person:test",
            "target_interaction_ref": "interaction:test", "query_terms": ["仮題"],
        }
        evidence = {"status": "summary", "answer_contract": contract, "evidence_items": []}
        compact = _compact_recall_pack({"answer_contract": contract, "evidence_pack": evidence})
        for projected in (compact["answer_contract"], compact["evidence_pack"]["answer_contract"]):
            self.assertNotIn("reason_codes", projected)
            self.assertEqual(projected["target_person_ref"], "person:test")
            self.assertEqual(projected["target_interaction_ref"], "interaction:test")
        self.assertEqual(evidence["answer_contract"]["reason_codes"], ["想起前には仮題の根拠がない"])

    def test_store_links_and_model_input_preserve_claim_scope_and_period(self) -> None:
        store = StoreMemoryLinksMixin()
        builder = RecallBuilder.__new__(RecallBuilder)
        old = {
            "memory_unit_id": "memory:old", "memory_type": "commitment",
            "scope_type": "topic", "scope_key": "topic:project", "subject_ref": "topic:project",
            "predicate": "respect_sharing_boundary", "object_ref_or_value": "共有条件",
            "summary_text": "仮題の共有条件。", "status": "superseded", "confidence": 0.8, "salience": 0.8,
            "formed_at": "2026-10-04T09:00:39+09:00", "last_confirmed_at": "2026-10-04T09:00:39+09:00",
            "valid_from": None, "valid_to": "2026-10-04T09:00:41+09:00",
            "qualifiers": {"source": "explicit_statement", "source_participant_refs": ["person:owner"],
                           "source_interaction_refs": ["interaction:owner"], "commitment_focus": "confidentiality"},
            "evidence_event_ids": ["event:private"],
        }
        current = {**old, "memory_unit_id": "memory:current", "status": "confirmed", "valid_to": None}
        link = {
            "label": "derived_from", "source_memory_unit_id": current["memory_unit_id"],
            "target_memory_unit_id": old["memory_unit_id"],
            "source_memory_unit": store._compact_memory_unit_for_link_context(current),
            "target_memory_unit": store._compact_memory_unit_for_link_context(old),
        }
        sections = {"active_commitments": [builder._to_memory_item(current)]}
        builder._attach_memory_link_summaries_to_sections(sections=sections, memory_links=[link])
        compact = _compact_recall_pack({**sections, "memory_link_context": builder._build_memory_link_context(
            memory_links=[link], selected_memory_ids=[current["memory_unit_id"]],
        )})
        selected = compact["active_commitments"][0]
        related = selected["memory_link_summary"]["representative_links"][0]["related_claim"]
        representative = compact["memory_link_context"]["representative_links"][0]
        for claim in (related, representative["target_claim"]):
            for key in ("memory_type", "subject_ref", "predicate", "object_ref_or_value", "status",
                        "formed_at", "last_confirmed_at", "valid_from", "valid_to", "qualifiers"):
                self.assertEqual(claim[key], old[key])
            self.assertNotIn("memory_unit_id", claim)
            self.assertNotIn("evidence_event_ids", claim)
        self.assertEqual(selected["subject_ref"], current["subject_ref"])
        self.assertEqual(selected["predicate"], current["predicate"])
        self.assertIsNone(representative["source_claim"]["valid_to"])
        self.assertEqual(len(compact["active_commitments"]), 1)

    def test_boundary_answer_projects_occurrence_evidence_without_overwriting_recall(self) -> None:
        episode = {
            "episode_type": "conversation", "summary_text": "前回は昨日と回答した。",
            "primary_scope_type": "relationship", "primary_scope_key": "self|person:test",
            "formed_at": "2026-10-04T21:00:01+09:00",
        }
        pack = {"status": "grounded", "boundary_at": episode["formed_at"], "evidence_items": [{
            "type": "event", "event_id": "event:latest", "created_at": episode["formed_at"],
            "text": "前回は昨日と回答した。",
        }]}
        recall = {"answer_contract": {"contract": "exact_boundary"},
                  "episodic_evidence": [episode], "evidence_pack": pack}
        compact = _compact_recall_pack(recall)
        self.assertEqual(compact["evidence_pack"], pack)
        self.assertEqual(compact["episodic_evidence"], [])
        self.assertEqual(recall["episodic_evidence"], [episode])
        recall["answer_contract"]["contract"] = "summary"
        self.assertEqual(_compact_recall_pack(recall)["episodic_evidence"][0]["formed_at"], episode["formed_at"])

    def test_current_person_relationship_episodes_remain_eligible_for_preference_focus(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        scope = builder._build_scope_context({
            "primary_recall_focus": "preference", "secondary_recall_focuses": ["episodic", "person"],
            "focus_scopes": [], "mentioned_entities": [], "mentioned_topics": [],
        }, current_person_ref="person:test")
        self.assertIn(("entity", "person:test"), scope["episode_scope_filters"])
        self.assertIn(("relationship", "self|person:test"), scope["episode_scope_filters"])
        self.assertNotIn(("relationship", "self|person:other"), scope["episode_scope_filters"])
        self.assertEqual(scope["relationship_filters"], [])

    def test_provenance_model_input_includes_retained_activity_as_typed_evidence(self) -> None:
        activity = {
            "label": "描画", "target": "絵", "actor": "person", "actor_ref": "person:test",
            "age_label": "30分前", "reason_summary": "書斎で絵を描いていたという報告から保持している。",
        }
        old_evidence = {"type": "episodic_evidence", "text": "以前はコーヒーを用意していた。"}
        recall = {"answer_contract": {"contract": "provenance"}, "evidence_pack": {"evidence_items": [old_evidence]}}
        compact = _compact_recall_pack(recall, activity_context={"current_activity": activity})
        item = compact["evidence_pack"]["evidence_items"][0]
        self.assertEqual(item["type"], "activity_context")
        self.assertEqual(item["payload"]["actor_ref"], "person:test")
        self.assertEqual(item["payload"]["age_label"], "30分前")
        self.assertEqual(item["payload"]["evidence_kind"], "inference")
        self.assertEqual(item["text"], activity["reason_summary"])
        self.assertEqual(compact["evidence_pack"]["evidence_items"][1], old_evidence)
        self.assertEqual(recall["evidence_pack"]["evidence_items"], [old_evidence])

    def test_selection_and_downstream_context_keep_current_and_historical_evidence_distinct(self) -> None:
        builder = RecallBuilder.__new__(RecallBuilder)
        current = builder._to_memory_item({
            "memory_unit_id": "memory:current", "memory_type": "preference",
            "scope_type": "entity", "scope_key": "person:test", "subject_ref": "person:test",
            "predicate": "favorite_drink", "object_ref_or_value": "ジャスミン茶",
            "summary_text": "普段は無糖のジャスミン茶を好む。", "status": "confirmed",
            "confidence": 0.8, "salience": 0.8,
            "formed_at": "2026-10-03T10:00:00+09:00",
            "last_confirmed_at": "2026-10-03T10:10:00+09:00",
            "valid_from": "2026-10-03T10:10:00+09:00", "valid_to": None,
            "qualifiers": {"source": "explicit_correction"},
        })
        episode = builder._to_episode_item({
            "episode_id": "episode:old", "episode_type": "conversation",
            "primary_scope_type": "entity", "primary_scope_key": "person:test",
            "summary_text": "玄米茶を好むという報告を受けた。", "salience": 0.8,
            "formed_at": "2026-10-03T10:05:00+09:00",
        })
        links = [{
            "source_memory_unit_id": "memory:current", "target_memory_unit_id": "memory:old",
            "label": "contradicts", "source_memory_unit": current,
            "target_memory_unit": {"memory_type": "preference", "status": "revoked", "summary_text": "玄米茶を好む。"},
        }]
        sections = {"person_model": [current], "episodic_evidence": [episode]}
        builder._attach_memory_link_summaries_to_sections(sections=sections, memory_links=links)
        compact = _compact_recall_pack({
            **sections,
            "memory_link_context": builder._build_memory_link_context(
                memory_links=links, selected_memory_ids=["memory:current"],
            ),
        })
        person = compact["person_model"][0]
        self.assertEqual(person["status"], "confirmed")
        self.assertEqual(person["last_confirmed_at"], current["last_confirmed_at"])
        self.assertEqual(person["valid_from"], current["valid_from"])
        self.assertEqual(person["qualifiers"]["source"], "explicit_correction")
        self.assertEqual(compact["episodic_evidence"][0]["formed_at"], episode["formed_at"])
        self.assertEqual(person["memory_link_summary"]["representative_links"][0]["related_status"], "revoked")
        self.assertEqual(compact["memory_link_context"]["representative_links"][0]["target_status"], "revoked")
        selected = builder._recall_pack_selection_candidate_source_item(candidate_ref="candidate:0", item=current)
        self.assertEqual(selected["valid_from"], current["valid_from"])
        for key in ("subject_ref", "predicate", "object_ref_or_value", "qualifiers", "valid_to"):
            self.assertEqual(selected[key], current[key])
        self.assertNotIn("memory_unit_id", selected)
        self.assertNotIn("evidence_event_ids", selected)
        selected_episode = builder._recall_pack_selection_candidate_source_item(candidate_ref="candidate:1", item=episode)
        self.assertEqual(selected_episode["formed_at"], episode["formed_at"])
