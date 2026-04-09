from __future__ import annotations

import uuid


# 既定の識別子
DEFAULT_PERSONA_ID = "persona:default"
DEFAULT_MEMORY_SET_ID = "memory_set:default"
DEFAULT_MODEL_PRESET_ID = "model_preset:default"
DEFAULT_DESKTOP_WATCH_INTERVAL_SECONDS = 300
DEFAULT_GEMINI_GENERATION_MODEL = "openrouter/google/gemini-3.1-flash-lite-preview"
DEFAULT_GEMINI_EMBEDDING_MODEL = "openrouter/google/gemini-embedding-001"


# 構築
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
        "memory_enabled": True,
        "wake_policy": {
            "mode": "disabled",
        },
        "desktop_watch": {
            "enabled": False,
            "interval_seconds": DEFAULT_DESKTOP_WATCH_INTERVAL_SECONDS,
            "target_client_id": None,
        },
        "personas": {
            DEFAULT_PERSONA_ID: {
                "persona_id": DEFAULT_PERSONA_ID,
                "display_name": "Default Persona",
                "persona_text": "やわらかく寄り添いながら会話する。",
                "second_person_label": "あなた",
                "addon_text": "",
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
        "model_presets": {
            DEFAULT_MODEL_PRESET_ID: build_default_model_preset(),
        },
    }


def build_default_model_preset() -> dict:
    # 結果
    return {
        "model_preset_id": DEFAULT_MODEL_PRESET_ID,
        "display_name": "Default OpenRouter Gemini Preset",
        "roles": {
            "observation_interpretation": {
                "kind": "generation",
                "provider": "openrouter",
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "endpoint_ref": "endpoint:openrouter_primary",
                "api_key": "",
                "reasoning_effort": "low",
            },
            "decision_generation": {
                "kind": "generation",
                "provider": "openrouter",
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "endpoint_ref": "endpoint:openrouter_primary",
                "api_key": "",
            },
            "expression_generation": {
                "kind": "generation",
                "provider": "openrouter",
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "endpoint_ref": "endpoint:openrouter_primary",
                "api_key": "",
            },
            "memory_interpretation": {
                "kind": "generation",
                "provider": "openrouter",
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "endpoint_ref": "endpoint:openrouter_primary",
                "api_key": "",
            },
            "embedding": {
                "kind": "embedding",
                "provider": "openrouter",
                "model": DEFAULT_GEMINI_EMBEDDING_MODEL,
                "endpoint_ref": "endpoint:openrouter_primary",
                "api_key": "",
            },
        },
    }
