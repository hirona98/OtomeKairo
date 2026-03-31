from __future__ import annotations

import uuid


# Block: DefaultIdentifiers
DEFAULT_PERSONA_ID = "persona:default"
DEFAULT_MEMORY_SET_ID = "memory_set:default"
DEFAULT_MODEL_PRESET_ID = "model_preset:default"

DEFAULT_RECALL_PROFILE_ID = "model_profile:mock_recall"
DEFAULT_DECISION_PROFILE_ID = "model_profile:mock_decision"
DEFAULT_REPLY_PROFILE_ID = "model_profile:mock_reply"
DEFAULT_MEMORY_PROFILE_ID = "model_profile:mock_memory"
DEFAULT_EMBED_PROFILE_ID = "model_profile:mock_embedding"


# Block: Builder
def build_default_state() -> dict:
    server_id = f"server:{uuid.uuid4().hex}"
    return {
        "server_id": server_id,
        "server_display_name": "OtomeKairo",
        "api_version": "0.1.0",
        "console_access_token": None,
        "selected_persona_id": DEFAULT_PERSONA_ID,
        "selected_memory_set_id": DEFAULT_MEMORY_SET_ID,
        "selected_model_preset_id": DEFAULT_MODEL_PRESET_ID,
        "wake_policy": {
            "mode": "disabled",
        },
        "personas": {
            DEFAULT_PERSONA_ID: {
                "persona_id": DEFAULT_PERSONA_ID,
                "display_name": "Default Persona",
                "core_persona": {
                    "self_image": "long-term companion",
                    "judgement_style": "careful and warm",
                    "relation_baseline": "supportive",
                },
                "expression_style": {
                    "tone": "gentle",
                    "sentence_length": "medium",
                    "emotional_expressiveness": "moderate",
                },
            }
        },
        "memory_sets": {
            DEFAULT_MEMORY_SET_ID: {
                "memory_set_id": DEFAULT_MEMORY_SET_ID,
                "display_name": "Default Memory",
                "description": "Empty starter memory set for the MVP slice.",
            }
        },
        "model_profiles": {
            DEFAULT_RECALL_PROFILE_ID: {
                "model_profile_id": DEFAULT_RECALL_PROFILE_ID,
                "kind": "generation",
                "provider": "mock",
                "model_name": "mock-recall",
            },
            DEFAULT_DECISION_PROFILE_ID: {
                "model_profile_id": DEFAULT_DECISION_PROFILE_ID,
                "kind": "generation",
                "provider": "mock",
                "model_name": "mock-decision",
            },
            DEFAULT_REPLY_PROFILE_ID: {
                "model_profile_id": DEFAULT_REPLY_PROFILE_ID,
                "kind": "generation",
                "provider": "mock",
                "model_name": "mock-reply",
            },
            DEFAULT_MEMORY_PROFILE_ID: {
                "model_profile_id": DEFAULT_MEMORY_PROFILE_ID,
                "kind": "generation",
                "provider": "mock",
                "model_name": "mock-memory",
            },
            DEFAULT_EMBED_PROFILE_ID: {
                "model_profile_id": DEFAULT_EMBED_PROFILE_ID,
                "kind": "embedding",
                "provider": "mock",
                "model_name": "mock-embedding",
            },
        },
        "model_presets": {
            DEFAULT_MODEL_PRESET_ID: {
                "model_preset_id": DEFAULT_MODEL_PRESET_ID,
                "display_name": "Default Mock Preset",
                "roles": {
                    "reply_generation": {
                        "model_profile_id": DEFAULT_REPLY_PROFILE_ID,
                    },
                    "decision_generation": {
                        "model_profile_id": DEFAULT_DECISION_PROFILE_ID,
                    },
                    "recall_hint_generation": {
                        "model_profile_id": DEFAULT_RECALL_PROFILE_ID,
                    },
                    "memory_interpretation": {
                        "model_profile_id": DEFAULT_MEMORY_PROFILE_ID,
                    },
                    "embedding": {
                        "model_profile_id": DEFAULT_EMBED_PROFILE_ID,
                    },
                },
            }
        },
    }
