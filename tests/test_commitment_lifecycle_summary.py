import pytest

from otomekairo.llm.commitment import validate_summaries
from otomekairo.llm.contracts import LLMError


def test_lifecycle_summary_requires_exactly_one_complete_description_per_target():
    validate_summaries({"summaries": [
        {"memory_unit_id": "memory:one", "summary_text": "投稿と報告を完了した。"},
        {"memory_unit_id": "memory:two", "summary_text": "観測は終え、残りの報告を取り消した。"},
    ]}, ["memory:one", "memory:two"])


@pytest.mark.parametrize("summaries", [
    [],
    [{"memory_unit_id": "memory:one", "summary_text": "完了した。"}],
    [{"memory_unit_id": "memory:one", "summary_text": "完了した。"}] * 2,
    [{"memory_unit_id": "memory:other", "summary_text": "完了した。"}],
    [{"memory_unit_id": "memory:one", "summary_text": ""}],
    [{"memory_unit_id": "memory:one", "summary_text": "完了した。", "extra": True}],
])
def test_missing_duplicate_unknown_or_invalid_description_is_an_explicit_failure(summaries):
    with pytest.raises(LLMError):
        validate_summaries({"summaries": summaries}, ["memory:one", "memory:two"])
