from otomekairo.llm.contexts import fresh_visual_observations_from_capability_view
from otomekairo.llm.contexts import CurrentInput
from otomekairo.service.app import OtomeKairoService


def test_current_visual_source_keeps_acquisition_reference_and_complete_description():
    service = OtomeKairoService.__new__(OtomeKairoService)
    description = "人物が机に座っている。" * 50
    sources = service._fresh_wake_observation_visual_sources({"wake_observations": [
        {"status": "succeeded", "capability_id": "vision.capture", "vision_source_id": "camera:test",
         "observation_id": "observation:test", "request_id": "request:current", "source_kind": "camera",
         "visual_summary_text": description},
        {"status": "failed", "capability_id": "vision.capture", "vision_source_id": "camera:failed"},
    ]})
    assert len(sources) == 1
    assert sources[0]["request_id"] == "request:current"
    assert sources[0]["observation_id"] == "observation:test"
    assert sources[0]["summary_text"] == description


def test_turn_memory_context_keeps_fresh_visual_evidence_separate_from_recall():
    service = OtomeKairoService.__new__(OtomeKairoService)
    current = {"vision_source_id": "camera:test", "fresh_source": "wake_observation",
               "request_id": "request:current", "summary_text": "人物が机に座っている。"}
    old = {"visual_observation_id": "observation:previous", "summary_text": "人物は映っていない。"}
    view = [{"id": "vision.capture", "fresh_world_state_by_vision_source": [current]},
            {"id": "mcp.call_tool", "fresh_world_state_by_vision_source": [{"summary_text": "対象外のデータ。"}]}]
    context = service._build_turn_memory_context(
        trigger_kind="user_message", input_event_kind="conversation_input", input_event_role="person",
        current_input={"sender_ref": "person:test"}, people_context=[], capability_decision_view=view,
        recall_pack={"visual_observations": [old]}, pending_intent_summary=None, pending_intent_selection=None,
        observation_summary=None, capability_request_summary=None, followup_capability_request_summary=None,
        ongoing_action_transition_summary=None, autonomous_run_summary=None,
        configured_activity_topics=[{"topic_id": "topic:activity", "topic_summary": "交流する。"}],
    )
    assert context["fresh_visual_observations"] == [current]
    assert context["recall_pack"]["visual_observations"] == [old]
    assert context["configured_activity_topics"] == [{"topic_id": "topic:activity", "topic_summary": "交流する。"}]
    assert fresh_visual_observations_from_capability_view(None) == []


def test_workspace_has_separate_current_observation_old_state_and_available_capability():
    service = OtomeKairoService.__new__(OtomeKairoService)
    observation = {"vision_source_id": "camera:test", "fresh_source": "wake_observation",
                   "request_id": "request:new", "summary_text": "屋外の芝生が見える。"}
    old_state = {"state_type": "visual_context", "integration_key": "visual_context:camera:test",
                 "source_kind": "capability_result", "source_ref": "request:old",
                 "summary_text": "室内の人物が見える。", "age_label": "直前"}
    result = service._build_workspace_context(
        current_input=CurrentInput(sender_kind="person", sender_ref="person:test", source_kind="user_message",
                                   response_target_refs=("person:test",), interaction_context=None, text="今の映像を教えて。"),
        recall_pack={}, drive_state_summary=None, foreground_world_state=[old_state], activity_context=None,
        ongoing_action_summary=None, autonomous_run_summaries=None,
        capability_decision_view=[{"id": "vision.capture", "available": True,
            "what_it_does": "カメラを観測する。", "fresh_world_state_by_vision_source": [observation]}],
        initiative_context=None, capability_result_context=None, visual_observation_context=None,
        self_state_context=None, relationship_context=None, prediction_error_context=None, default_mode_context=None,
    )
    candidates = {c["factor_ref"]: c for c in result["workspace_candidates"]}
    current = candidates["visual_observation:wake:camera:test"]
    assert current["summary_text"] == observation["summary_text"]
    assert current["metadata"]["request_id"] == "request:new"
    assert candidates["world_state:visual_context:camera:test"]["metadata"]["source_ref"] == "request:old"
    assert candidates["capability:vision.capture"]["kind"] == "capability"
