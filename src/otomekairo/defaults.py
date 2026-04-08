from __future__ import annotations

import uuid


# 既定の識別子
DEFAULT_PERSONA_ID = "persona:default"
DEFAULT_MEMORY_SET_ID = "memory_set:default"
DEFAULT_MODEL_PRESET_ID = "model_preset:default"

DEFAULT_RECALL_PROFILE_ID = "model_profile:gemini_recall"
DEFAULT_DECISION_PROFILE_ID = "model_profile:gemini_decision"
DEFAULT_REPLY_PROFILE_ID = "model_profile:gemini_reply"
DEFAULT_MEMORY_PROFILE_ID = "model_profile:gemini_memory"
DEFAULT_EMBED_PROFILE_ID = "model_profile:gemini_embedding"
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
        "model_profiles": build_default_model_profiles(),
        "model_presets": {
            DEFAULT_MODEL_PRESET_ID: build_default_model_preset(),
        },
    }


def build_default_model_profiles() -> dict:
    # 生成プロファイル
    generation_profile = {
        "kind": "generation",
        "model": DEFAULT_GEMINI_GENERATION_MODEL,
        "auth": {
            "type": "bearer",
            "token": "",
        },
    }

    # 埋め込みプロファイル
    embedding_profile = {
        "kind": "embedding",
        "model": DEFAULT_GEMINI_EMBEDDING_MODEL,
        "auth": {
            "type": "bearer",
            "token": "",
        },
    }

    # 結果
    return {
        DEFAULT_RECALL_PROFILE_ID: {
            "model_profile_id": DEFAULT_RECALL_PROFILE_ID,
            "display_name": "OpenRouter Gemini Recall",
            **generation_profile,
        },
        DEFAULT_DECISION_PROFILE_ID: {
            "model_profile_id": DEFAULT_DECISION_PROFILE_ID,
            "display_name": "OpenRouter Gemini Decision",
            **generation_profile,
        },
        DEFAULT_REPLY_PROFILE_ID: {
            "model_profile_id": DEFAULT_REPLY_PROFILE_ID,
            "display_name": "OpenRouter Gemini Reply",
            **generation_profile,
        },
        DEFAULT_MEMORY_PROFILE_ID: {
            "model_profile_id": DEFAULT_MEMORY_PROFILE_ID,
            "display_name": "OpenRouter Gemini Memory",
            **generation_profile,
        },
        DEFAULT_EMBED_PROFILE_ID: {
            "model_profile_id": DEFAULT_EMBED_PROFILE_ID,
            "display_name": "OpenRouter Gemini Embedding",
            **embedding_profile,
        },
    }


def build_default_model_preset() -> dict:
    # 結果
    return {
        "model_preset_id": DEFAULT_MODEL_PRESET_ID,
        "display_name": "Default OpenRouter Gemini Preset",
        "roles": {
            "reply_generation": {
                "model_profile_id": DEFAULT_REPLY_PROFILE_ID,
                "max_turns_window": 10,
                "max_tokens": 4096,
                "reply_web_search_enabled": True,
            },
            "decision_generation": {
                "model_profile_id": DEFAULT_DECISION_PROFILE_ID,
                "max_tokens": 4096,
            },
            "recall_hint_generation": {
                "model_profile_id": DEFAULT_RECALL_PROFILE_ID,
                "max_tokens": 2048,
            },
            "memory_interpretation": {
                "model_profile_id": DEFAULT_MEMORY_PROFILE_ID,
                "max_tokens": 4096,
            },
            "embedding": {
                "model_profile_id": DEFAULT_EMBED_PROFILE_ID,
                "similar_episodes_limit": 40,
                "embedding_dimension": 3072,
            },
        },
    }


def normalize_state(state: dict) -> tuple[dict, bool]:
    changed = False

    if "memory_enabled" not in state:
        state["memory_enabled"] = True
        changed = True

    desktop_watch = state.get("desktop_watch")
    if not isinstance(desktop_watch, dict):
        state["desktop_watch"] = {
            "enabled": False,
            "interval_seconds": DEFAULT_DESKTOP_WATCH_INTERVAL_SECONDS,
            "target_client_id": None,
        }
        changed = True
    else:
        if "enabled" not in desktop_watch:
            desktop_watch["enabled"] = False
            changed = True
        if "interval_seconds" not in desktop_watch:
            desktop_watch["interval_seconds"] = DEFAULT_DESKTOP_WATCH_INTERVAL_SECONDS
            changed = True
        if "target_client_id" not in desktop_watch:
            desktop_watch["target_client_id"] = None
            changed = True

    for persona_id, persona in state.get("personas", {}).items():
        if "persona_text" not in persona:
            persona["persona_text"] = persona.get("display_name", persona_id)
            changed = True
        if "second_person_label" not in persona:
            persona["second_person_label"] = "あなた"
            changed = True
        if "addon_text" not in persona:
            persona["addon_text"] = ""
            changed = True

    for profile_id, profile in state.get("model_profiles", {}).items():
        if "display_name" not in profile:
            profile["display_name"] = profile_id
            changed = True

    return state, changed
