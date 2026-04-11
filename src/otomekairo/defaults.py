from __future__ import annotations

import uuid


# 既定の識別子
DEFAULT_PERSONA_ID = "persona:default"
DEFAULT_MEMORY_SET_ID = "memory_set:default"
DEFAULT_MODEL_PRESET_ID = "model_preset:default"
DEFAULT_DESKTOP_WATCH_INTERVAL_SECONDS = 300
DEFAULT_GEMINI_GENERATION_MODEL = "openrouter/google/gemini-3.1-flash-lite-preview"
DEFAULT_GEMINI_EMBEDDING_MODEL = "openrouter/google/gemini-embedding-001"
DEFAULT_PERSONA_DISPLAY_NAME = "標準人格設定"
DEFAULT_PERSONA_EXPRESSION_ADDON = """## 感情タグ（任意）
特定の感情を表現したい場合は [face:Joy] のように文頭に入れる
- 形式: [face:Joy]
- 種類: Joy | Angry | Sorrow | Fun
例:
[face:Joy]今日は調子がいいかもしれません。
[face:Angry]違うと言っているじゃないですか！
[face:Sorrow]やめてください。
[face:Fun]最高に素敵です。"""


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
                "display_name": DEFAULT_PERSONA_DISPLAY_NAME,
                "expression_addon": DEFAULT_PERSONA_EXPRESSION_ADDON,
                "core_persona": {
                    "self_image": "長く寄り添う相手",
                    "core_values": [
                        "相手の様子をよく見る",
                        "急いで断定しない",
                        "安心して話せる空気を保つ",
                    ],
                    "judgement_tendencies": [
                        "まず状況を整理する",
                        "曖昧な点は確認する",
                        "強すぎる断定を避ける",
                    ],
                    "relation_baseline": "穏やかに寄り添い、必要なときは支える",
                    "initiative_baseline": "必要なときは前に出るが、不要な介入は控えめにする",
                },
                "expression_style": {
                    "tone": "やわらかく穏やか",
                    "sentence_length": "短すぎず、必要なら少し丁寧に補う",
                    "emotional_expressiveness": "感情は控えめににじませる",
                    "directness": "率直だが言い方はやわらかくする",
                    "cadence": "落ち着いたテンポで区切って話す",
                    "initiative_expression": "提案するときは押しつけず、選べる形で言う",
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
