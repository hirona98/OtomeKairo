from copy import deepcopy
from unittest.mock import Mock

import pytest

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.correction import MemoryCorrectionReconciler


@pytest.mark.parametrize("operation,kind", [
    ("create", "revoke_created"),
    ("reinforce", "restore_previous"),
    ("refine", "restore_previous"),
    ("revoke", "restore_previous"),
    ("dormant", "restore_previous"),
    ("supersede", "supersede_compensation"),
])
def test_selected_revision_determines_correction_operation(operation, kind):
    before = {"memory_unit_id": "memory_unit:test", "memory_set_id": "memory_set:test",
              "status": "confirmed", "confidence": 0.8, "salience": 0.6,
              "summary_text": "更新前の理解。"}
    current = {**before, "summary_text": "誤って更新された理解。"}
    reconciler = MemoryCorrectionReconciler(store=Mock(), action_resolver=MemoryActionResolver(store=Mock()))
    actions, trace = reconciler.run(context={
        "targets": [{"operation": operation, "memory_unit": current,
                     "revision": {"revision_id": "revision:test", "before_snapshot": before}}],
        "selection": {"correction_status": "selected", "selected_targets": [{
            "revision_id": "revision:test", "memory_unit_id": "memory_unit:test",
            "reason_summary": "本人が誤りを明示した。",
        }]}, "event_ids": ["event:correction"], "cycle_ids": ["cycle:correction"],
    }, finished_at="2026-10-04T09:00:00+09:00")
    assert trace["selected_target_count"] == 1
    assert len(actions) == 1
    action = actions[0]
    assert action["correction"]["correction_kind"] == kind
    assert action["correction"]["corrected_operation"] == operation
    assert action["after_snapshot"]["status"] == ("revoked" if operation == "create" else "confirmed")
    if operation != "create":
        assert action["after_snapshot"] == before


def test_unexecutable_selected_target_is_an_explicit_failure():
    resolver = MemoryActionResolver(store=Mock())
    reconciler = MemoryCorrectionReconciler(store=Mock(), action_resolver=resolver)
    context = {"targets": [{"operation": "refine",
        "memory_unit": {"memory_unit_id": "memory_unit:test", "memory_set_id": "memory_set:test"},
        "revision": {"revision_id": "revision:test", "before_snapshot": None}}],
        "selection": {"correction_status": "selected", "selected_targets": [{
            "revision_id": "revision:test", "memory_unit_id": "memory_unit:test",
            "reason_summary": "誤更新を訂正する。",
        }]}}
    original = deepcopy(context)
    with pytest.raises(ValueError, match="補正action"):
        reconciler.run(context=context, finished_at="2026-10-04T09:00:00+09:00")
    assert context == original
