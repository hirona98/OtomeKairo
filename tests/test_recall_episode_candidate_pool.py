from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from otomekairo.defaults import build_default_state
from otomekairo.recall.builder import RecallBuilder
from otomekairo.store.file_store import FileStore


@pytest.mark.parametrize("primary_focus", ["episodic", "preference"])
def test_episode_retrieval_pool_reaches_llm_before_final_adoption_limit(tmp_path, primary_focus):
    store = FileStore(tmp_path)
    records = [{
        "episode_id": f"episode:{index}", "cycle_id": f"cycle:{index}",
        "memory_set_id": "memory_set:default", "episode_type": "conversation",
        "primary_scope_type": "entity", "primary_scope_key": "person:test",
        "summary_text": f"本人が報告した出来事 {index}。", "salience": 1 - index / 100,
        "formed_at": "2026-10-04T09:00:00+09:00", "linked_event_ids": [],
    } for index in range(25)]
    with store._memory_db() as conn:
        for record in records:
            store._insert_episode(conn, record)
    builder = RecallBuilder(store=store, llm=SimpleNamespace())
    association = builder._empty_association_sections()
    association["episodic_evidence"] = [
        {**builder._to_episode_item(records[index]), "retrieval_lane": "association"}
        for index in (0, 24)
    ]
    builder._build_association_sections = Mock(return_value=association)
    packs = []

    def select(**kwargs):
        pack = kwargs["source_pack"]
        packs.append(pack)
        candidates = next(section["candidates"] for section in pack["candidate_sections"]
                          if section["section_name"] == "episodic_evidence")
        return {"section_selection": [{
            "section_name": "episodic_evidence",
            "candidate_refs": [candidate["candidate_ref"] for candidate in reversed(candidates)],
        }], "conflict_summaries": []}

    builder.llm.generate_recall_pack_selection = Mock(side_effect=select)
    result = builder.build_recall_pack(
        state=build_default_state(), augmented_query_text="報告とその後の変化を順番に教えて。",
        recall_hint={"primary_recall_focus": primary_focus, "secondary_recall_focuses": [],
                     "time_reference": "past", "focus_scopes": [], "mentioned_entities": [],
                     "mentioned_topics": [], "risk_flags": []},
        current_person_ref="person:test", current_time="2027-10-04T09:00:00+09:00",
    )
    candidates = next(section["candidates"] for section in packs[0]["candidate_sections"]
                      if section["section_name"] == "episodic_evidence")
    assert len(candidates) == 25  # 24 structured records plus one distinct association record.
    assert candidates[-1]["retrieval_lane"] == "association"
    assert len(result["episodic_evidence"]) == 6
    assert result["selected_episode_ids"] == [f"episode:{index}" for index in range(24, 18, -1)]
    assert len(result["recall_pack_selection"]["dropped_candidate_refs"]) == 19
    builder._build_association_sections.assert_called_once()
