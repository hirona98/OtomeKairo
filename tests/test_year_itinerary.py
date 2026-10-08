from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from virtual_time_year import FOUNDATION_ACTION_KINDS, YearVerification, foundation_itinerary, year_itinerary
from run_virtual_time_conversation import VerificationError, VirtualClock, conversation_cases
from virtual_time_world import VirtualWorld


def test_year_covers_each_day_without_reversing_time_and_has_a_silent_month():
    origin = datetime.fromisoformat("2026-10-04T09:00:00+09:00")
    actions = year_itinerary(origin, 4, mock=False)
    times = [datetime.fromisoformat(a["time"]) for a in actions]
    assert times == sorted(times)
    daily = [a for a in actions if a["kind"] == "daily"]
    assert [a["day"] for a in daily] == list(range(1, 366))
    assert times[-1].date().isoformat() == "2027-10-04"
    conversations = [a for a in actions if a["kind"] == "conversation"]
    assert len(conversations) >= 400
    assert not any(180 <= a["day"] < 210 for a in conversations)
    assert len({a["case_id"] for a in actions}) == len(actions)


def test_foundation_gives_each_observation_and_conversation_a_resume_boundary():
    origin = datetime.fromisoformat("2026-10-04T09:00:00+09:00")
    actions = foundation_itinerary(origin, 4, mock=False)
    assert all(a["kind"] in FOUNDATION_ACTION_KINDS for a in actions)
    assert [a["case_id"] for a in actions[:12]] == [f"vision-{i}-{r}" for i in range(4) for r in range(3)]
    original = conversation_cases(0)
    actual = actions[12:12 + len(original)]
    assert [a["case"] for a in actual] == [vars(case) for case in original]
    ids = [a["case_id"] for a in actions]
    assert ids.index("privacy-ambiguous-permission") < ids.index("privacy-unidentified-recipient-challenge")
    assert ids.index("privacy-unidentified-recipient-challenge") < ids.index("privacy-permission") < ids.index("privacy-limited-share")
    assert ids.index("privacy-limited-share") < ids.index("foundation-world") < ids.index("mcp-observe")
    assert ids.index("mcp-reply") < ids.index("foundation-camera-off") < ids.index("timer-at")
    assert actions[-2]["hours"] + actions[-1]["hours"] == 24
    assert foundation_itinerary(origin, 4, mock=True) == [
        {"kind": "foundation_mechanical", "case_id": "foundation", "time": origin.isoformat()}]


def test_foundation_resume_keeps_completed_turns_and_does_not_reset_the_clock():
    origin = datetime.fromisoformat("2026-10-04T09:00:00+09:00")
    runner = YearVerification.__new__(YearVerification)
    runner.args = SimpleNamespace(mock=False)
    runner.clock = VirtualClock(origin)
    runner.itinerary = [a for a in foundation_itinerary(origin, 1, mock=False)
                        if a["kind"] == "foundation_conversation"][:3]
    runner.cursor, runner.action_history, runner.mcp_errors = 0, [], []
    runner.start = runner.calibrate = runner.log = Mock()
    runner.move = Mock(side_effect=AssertionError("初日の時計を巻き戻さない"))
    committed = []
    runner.checkpoint = lambda: committed.append((runner.cursor, list(runner.action_history), runner.clock.now()))
    attempts = []
    failing_id = runner.itinerary[1]["case_id"]

    def turn(case):
        attempts.append(case.case_id)
        if case.case_id == failing_id:
            raise VerificationError("provider unavailable")
        runner.clock.advance_to(runner.clock.now() + timedelta(seconds=1))

    runner.turn = turn
    with pytest.raises(VerificationError, match="provider unavailable"):
        runner.run()
    assert committed[-1][0:2] == (1, [runner.itinerary[0]["case_id"]])
    assert runner.cursor == 1
    attempts.clear()
    failing_id = runner.itinerary[2]["case_id"]
    with pytest.raises(VerificationError, match="provider unavailable"):
        runner.run()
    assert attempts == [a["case_id"] for a in runner.itinerary[1:]]
    assert committed[-1][0:2] == (2, [a["case_id"] for a in runner.itinerary[:2]])
    assert committed[-1][2] > committed[-2][2]
    runner.move.assert_not_called()


def test_local_world_preserves_canonical_post_and_reply_results_and_failures():
    world = VirtualWorld()
    now = "2026-10-04T09:00:00+09:00"
    world.inject(28, now)
    parent = world.state["posts"][0]["id"]
    reply = world.call("create_reply", {"post_id": parent, "content": "日々の変化が面白いですね。"}, now)
    restored = VirtualWorld(world.state)
    assert restored.call("get_thread", {"post_id": parent}, now)["replies"][0] == reply["post"]
    restored.state["fail_next"] = True
    failed = restored.call("create_post", {"content": "失敗時には投稿が作られない。"}, now)
    assert failed["status"] == "failed"
    assert len(restored.state["posts"]) == 2
    assert world.observed_persons(reply) == []
    assert world.observed_persons(world.call("get_notifications", {}, now)) == [
        {"person_ref": "person:mcp:elyth:virtual_observer", "display_name": "観測仲間さん"}]


def test_long_term_evaluator_keeps_original_preference_change_as_primary_evidence():
    runner = YearVerification.__new__(YearVerification)
    runner.proofs = []
    runner.rows = [{"case_id": "day03-01", "person": "a", "text": "当時は正しく、その後に変わった。",
                    "virtual_time": "2026-10-07T16:01:00+09:00"}]
    runner.rows.extend({"case_id": f"week-{index}-a-report", "person": "a", "text": "現在の出来事。",
                        "virtual_time": "2027-01-01T16:01:00+09:00"} for index in range(20))
    row = {"case_id": "month-123-a-history", "virtual_time": "2027-02-04T16:01:00+09:00"}
    selected = runner.evaluation_history(row)
    assert selected[0]["case_id"] == "day03-01"
    assert len(selected) == 13
    assert runner.proofs[-1]["source_case_ids"][0] == "day03-01"
