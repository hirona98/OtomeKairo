import unittest

from otomekairo.llm.prompts import _compact_recall_pack
from otomekairo.recall.builder import RecallBuilder


class RecallEvidenceStatusTests(unittest.TestCase):
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
        selected_episode = builder._recall_pack_selection_candidate_source_item(candidate_ref="candidate:1", item=episode)
        self.assertEqual(selected_episode["formed_at"], episode["formed_at"])
