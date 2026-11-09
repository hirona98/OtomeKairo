import json
from identifier_fixtures import id_allocator

from otomekairo.observations.acquisition import ObservationAcquisition

STAMP = "2026-10-28T10:00:00+09:00"
IMAGE = "data:image/png;base64,YWJj"


def image_context(*, summary="机と椅子が見える。", origin_kind="conversation_attachment", comparisons=None, persons=()):
    acquisition = ObservationAcquisition("memory_set:test", 300, id_allocator())
    acquisition.add_image(
        data_uri=IMAGE, source_ref="vision_source:test", source_kind="camera" if origin_kind == "vision_capture" else origin_kind,
        source_label="テスト画像", source_owner="self" if origin_kind == "vision_capture" else None,
        place_ref=None, origin_kind=origin_kind, observed_at=STAMP, received_at=STAMP,
        time_basis="captured" if origin_kind == "vision_capture" else "received",
        target={"observed_persons": list(persons)}, comparison=comparisons,
    )
    context = acquisition.freeze(snapshot_at=STAMP, sensor_observations=(), sensor_reports=())
    result = {"observation_ref": context.observation_refs()[0], "summary_text": summary, "confidence_hint": "medium",
              "change_state": "first_seen", "change_basis": "no_previous_observation",
              "change_reason_summary": "この取得元の初回観測である。"}
    return acquisition.apply(context, [result])


def input_payload(*, observations=()):
    return {
        "recall_hint": {"primary_recall_focus": "fact", "secondary_recall_focuses": [], "confidence": 0.8,
                        "time_reference": "none", "focus_scopes": [], "mentioned_entities": [],
                        "mentioned_topics": [], "risk_flags": []},
        "answer_contract": {"contract": "summary", "reason_codes": [], "boundary": "none", "target_actor": "any",
                            "target_person_ref": None, "target_interaction_ref": None, "query_terms": []},
        "observation_interpretations": list(observations),
    }


def text_payloads(messages):
    for message in messages:
        if message["role"] != "user":
            continue
        content = message["content"]
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        for part in parts:
            if part["type"] == "text" and part["text"].startswith("<<<"):
                yield json.loads(part["text"].splitlines()[1])
