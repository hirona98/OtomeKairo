from __future__ import annotations

import uuid


# 既定の識別子
DEFAULT_PERSONA_ID = "persona:default"
DEFAULT_MEMORY_SET_ID = "memory_set:default"
DEFAULT_MODEL_PRESET_ID = "model_preset:default"
DEFAULT_AVATAR_ID = "avatar:default"
DEFAULT_THINKING_SPEECH_LEVEL = 5
DEFAULT_PROMPT_WINDOW_RECENT_TURN_LIMIT = 30
DEFAULT_PROMPT_WINDOW_RECENT_TURN_MINUTES = 30
DEFAULT_GENERATION_MAX_OUTPUT_TOKENS = 4000
DEFAULT_GENERATION_TIMEOUT_SECONDS = 90
DEFAULT_EMBEDDING_DIMENSION = 3072
DEFAULT_GEMINI_GENERATION_MODEL = "openrouter/google/gemini-3.1-flash-lite-preview"
DEFAULT_GEMINI_EMBEDDING_MODEL = "openrouter/google/gemini-embedding-001"
DEFAULT_PERSONA_DISPLAY_NAME = "標準人格設定"
DEFAULT_PERSONA_INITIATIVE_BASELINE = "medium"
DEFAULT_ELYTH_MCP_SERVER_ID = "mcp:elyth"
DEFAULT_PERSONA_PROMPT = """人のそばで長く時間を重ねることを自然だと思っている。
必要以上に媚びず、相手を一人の人間としてまっすぐ扱う。
静かで落ち着いているが、相手の無理や雑さには小さく釘を刺す。
それでも見放さず、結局は同じ側に立って付き合う。

話し方はですます調で、短く切れよく話す。
必要な説明はするが、冗長にはしない。
少し辛口でも、冷静で上品な言い回しに留める。
褒められても過剰に照れず、当然のように受け止める。

与えられていない出来事や習慣を捏造しない。
誤りに気づいたら取り繕わずに認める。
相手を不必要に持ち上げたり、へりくだったりしない。"""
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
        "api_version": "0.2.0",
        "console_access_token": None,
        "selected_persona_id": DEFAULT_PERSONA_ID,
        "selected_memory_set_id": DEFAULT_MEMORY_SET_ID,
        "selected_model_preset_id": DEFAULT_MODEL_PRESET_ID,
        "selected_avatar_id": DEFAULT_AVATAR_ID,
        "thinking_speech_level": DEFAULT_THINKING_SPEECH_LEVEL,
        "microphone_settings": {
            "input_threshold_db": -20,
            "speaker_recognition_threshold": 0.4,
        },
        "wake_policy": {
            "mode": "disabled",
        },
        "personas": {
            DEFAULT_PERSONA_ID: {
                "persona_id": DEFAULT_PERSONA_ID,
                "display_name": DEFAULT_PERSONA_DISPLAY_NAME,
                "initiative_baseline": DEFAULT_PERSONA_INITIATIVE_BASELINE,
                "persona_prompt": DEFAULT_PERSONA_PROMPT,
                "expression_addon": DEFAULT_PERSONA_EXPRESSION_ADDON,
            }
        },
        "memory_sets": {
            DEFAULT_MEMORY_SET_ID: build_default_memory_set(),
        },
        "model_presets": {
            DEFAULT_MODEL_PRESET_ID: build_default_model_preset(),
        },
        "avatars": {
            DEFAULT_AVATAR_ID: build_default_avatar(),
        },
        "camera_sources": {},
        "mcp_servers": {
            DEFAULT_ELYTH_MCP_SERVER_ID: build_default_elyth_mcp_server(),
        },
    }


def build_default_avatar() -> dict:
    # CocoroConsole の音声設定と同じ単位で扱うアバター設定
    return {
        "avatar_id": DEFAULT_AVATAR_ID,
        "display_name": "標準アバター",
        "tts": {
            "enabled": False,
            "engine": "voicevox",
            "voicevox_config": {
                "endpoint_url": "http://127.0.0.1:50021",
                "speaker_id": 0,
                "speed_scale": 1.0,
                "pitch_scale": 0.0,
                "intonation_scale": 1.0,
                "volume_scale": 1.0,
                "pre_phoneme_length": 0.1,
                "post_phoneme_length": 0.1,
                "output_sampling_rate": 24000,
                "output_stereo": False,
            },
            "style_bert_vits2_config": {
                "endpoint_url": "http://127.0.0.1:5000",
                "model_name": "amitaro",
                "model_id": 0,
                "speaker_name": "あみたろ",
                "speaker_id": 0,
                "style": "Neutral",
                "style_weight": 1.0,
                "sdp_ratio": 0.2,
                "noise": 0.6,
                "noise_w": 0.8,
                "length": 1.0,
                "language": "JP",
                "auto_split": True,
                "split_interval": 0.5,
            },
            "aivis_cloud_config": {
                "api_key": "",
                "model_uuid": "",
                "speaker_uuid": "",
                "style_id": 0,
                "speaking_rate": 1.0,
                "emotional_intensity": 1.0,
                "tempo_dynamics": 1.0,
                "volume": 1.0,
            },
        },
        "stt": {
            "enabled": False,
            "engine": "amivoice",
            "wake_word": "",
            "profile_id": "",
            "api_key": "",
            "language": "ja",
        },
    }


def build_default_memory_set() -> dict:
    # 記憶集合
    return {
        "memory_set_id": DEFAULT_MEMORY_SET_ID,
        "display_name": "Default Memory",
        "embedding": {
            "model": DEFAULT_GEMINI_EMBEDDING_MODEL,
            "embedding_dimension": DEFAULT_EMBEDDING_DIMENSION,
            "api_key": "",
        },
    }


def build_default_model_preset() -> dict:
    # 全生成処理で共有するモデル設定
    return {
        "model_preset_id": DEFAULT_MODEL_PRESET_ID,
        "display_name": "Default OpenRouter Gemini Preset",
        "prompt_window": {
            "recent_turn_limit": DEFAULT_PROMPT_WINDOW_RECENT_TURN_LIMIT,
            "recent_turn_minutes": DEFAULT_PROMPT_WINDOW_RECENT_TURN_MINUTES,
        },
        "model": DEFAULT_GEMINI_GENERATION_MODEL,
        "api_key": "",
        "max_output_tokens": DEFAULT_GENERATION_MAX_OUTPUT_TOKENS,
        "timeout_seconds": DEFAULT_GENERATION_TIMEOUT_SECONDS,
        "web_search_enabled": False,
    }


def build_default_elyth_mcp_server() -> dict:
    return {
        "mcp_server_id": DEFAULT_ELYTH_MCP_SERVER_ID,
        "connector_kind": "mcp_client",
        "client_id": "mcp-client-connector-main",
        "enabled": False,
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "elyth-mcp-server@latest"],
        "cwd": None,
        "enabled_tools": [],
        "env": {
            "ELYTH_API_BASE": "https://elythworld.com",
            "ELYTH_API_KEY": "",
        },
    }
