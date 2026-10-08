from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from otomekairo.memory.utils import llm_local_time_text, local_datetime, parse_iso
from otomekairo.service.input.mixin import ServiceInputMixin


def test_llm_time_keeps_the_scheduled_seconds_at_both_sides_of_a_deadline():
    due = datetime.fromisoformat("2026-10-04T09:05:47+09:00")
    with patch("otomekairo.memory.utils.local_datetime", side_effect=datetime.fromisoformat):
        before = llm_local_time_text((due - timedelta(seconds=1)).isoformat())
        at = llm_local_time_text(due.isoformat())
    assert "9時05分46秒" in before
    assert "9時05分47秒" in at
    assert before != at


def test_shared_time_context_retains_seconds_for_speech_and_completion_review():
    with patch("otomekairo.memory.utils.local_datetime", side_effect=datetime.fromisoformat):
        service = ServiceInputMixin()
        context = service._build_time_context(current_time="2026-10-04T09:05:47+09:00")
    assert "9時05分47秒" in context["current_time_text"]


@pytest.mark.parametrize("parser", [parse_iso, local_datetime, llm_local_time_text])
def test_timestamp_without_an_offset_is_an_explicit_failure(parser):
    with pytest.raises(ValueError, match="timezone offset"):
        parser("2026-10-04T09:00:47")


def test_explicit_offsets_preserve_the_same_instant():
    japan = "2026-10-04T09:00:47+09:00"
    utc = "2026-10-04T00:00:47+00:00"
    assert parse_iso(japan) == parse_iso(utc)
    assert local_datetime(japan) == local_datetime(utc)
