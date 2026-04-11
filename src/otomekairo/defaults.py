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
            DEFAULT_MEMORY_SET_ID: build_default_memory_set(),
        },
        "model_presets": {
            DEFAULT_MODEL_PRESET_ID: build_default_model_preset(),
        },
    }


def build_default_memory_set() -> dict:
    # 記憶集合
    return {
        "memory_set_id": DEFAULT_MEMORY_SET_ID,
        "display_name": "Default Memory",
        "embedding": {
            "model": DEFAULT_GEMINI_EMBEDDING_MODEL,
            "api_key": "",
        },
    }


def build_default_model_preset() -> dict:
    # 生成用モデル群
    return {
        "model_preset_id": DEFAULT_MODEL_PRESET_ID,
        "display_name": "Default OpenRouter Gemini Preset",
        "roles": {
            "observation_interpretation": {
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "api_key": "",
                "reasoning_effort": "low",
            },
            "decision_generation": {
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "api_key": "",
            },
            "expression_generation": {
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "api_key": "",
            },
            "memory_interpretation": {
                "model": DEFAULT_GEMINI_GENERATION_MODEL,
                "api_key": "",
            },
        },
    }
