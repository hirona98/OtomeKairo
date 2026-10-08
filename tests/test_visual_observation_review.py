from unittest.mock import patch

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import LLMError


PERSONA = PersonaContext('Test', '観測を確かめて判断する。', None, '判断の基底。')
DRAFT = {'summary_text': '赤い花の鉢が見える。', 'confidence_hint': 'high',
         'change_state': 'first_seen', 'change_basis': 'no_previous_observation',
         'change_reason_summary': '同じsourceの前回観測がない。'}
REVIEWED = {**DRAFT, 'summary_text': '背の高い白い容器に赤い花が見える。'}


def test_only_image_reviewed_observation_reaches_downstream():
    image = 'data:image/jpeg;base64,fixture'
    source = {'image_input_kind': 'vision_capture_result'}
    with patch.object(LLMClient, '_generate_structured_payload', side_effect=[DRAFT, REVIEWED]) as generate:
        result = LLMClient().generate_visual_observation_summary(
            model_config={'model': 'test'}, persona_context=PERSONA, source_pack=source, images=[image])
    assert result == REVIEWED
    review = generate.call_args_list[1].kwargs
    assert review['operation'] == 'visual_observation_review'
    assert review['response_format']['json_schema']['name'] == 'visual_observation_review'
    content = review['messages'][1]['content']
    assert any(part.get('image_url', {}).get('url') == image for part in content)
    assert any(DRAFT['summary_text'] in part.get('text', '') for part in content)
    assert source == {'image_input_kind': 'vision_capture_result'}


def test_image_review_failure_does_not_publish_unreviewed_draft():
    with patch.object(LLMClient, '_generate_structured_payload', side_effect=[DRAFT, LLMError('画像照合失敗')]):
        with pytest.raises(LLMError):
            LLMClient().generate_visual_observation_summary(
                model_config={'model': 'test'}, persona_context=PERSONA,
                source_pack={'image_input_kind': 'conversation_attachment'}, images=['data:image/jpeg;base64,fixture'])


def test_image_review_preserves_complete_draft_and_source_context():
    description = '机の上に容器があり、形状を確認できる。' * 30
    candidate = {**DRAFT, 'summary_text': description}
    previous = '以前の画像の説明。' * 100
    with patch.object(LLMClient, '_generate_structured_payload', side_effect=[candidate, REVIEWED]) as generate:
        LLMClient().generate_visual_observation_summary(
            model_config={'model': 'test'}, persona_context=PERSONA,
            source_pack={'change_context': {'previous_observation_context': {'summary_text': previous}}},
            images=['data:image/jpeg;base64,fixture'])
    texts = [part['text'] for part in generate.call_args_list[1].kwargs['messages'][1]['content'] if part['type'] == 'text']
    assert any(description in text for text in texts)
    assert any(previous in text for text in texts)
