import pytest

from observation_fixtures import image_context


@pytest.mark.parametrize("persons", [[], [{"person_ref": "person:camera", "display_name": "撮影対象"}]])
def test_observation_context_preserves_only_structured_identification(persons):
    context = image_context(persons=persons)
    observation = context.to_prompt_payload()["input_materials"]["observations"][0]
    assert observation["observed_persons"] == persons
