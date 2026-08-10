from __future__ import annotations

import json
import uuid
from importlib import resources


# 既定の識別子
DEFAULT_PERSONA_ID = "persona:default"
DEFAULT_MEMORY_SET_ID = "memory_set:default"
DEFAULT_MODEL_PRESET_ID = "model_preset:default"
DEFAULT_AVATAR_ID = "avatar:default"
API_VERSION = "0.9.0"
DEFAULT_THINKING_SPEECH_LEVEL = 5
DEFAULT_WAKE_INTERVAL_SECONDS = 300
DEFAULT_PROMPT_WINDOW_RECENT_TURN_LIMIT = 30
DEFAULT_PROMPT_WINDOW_RECENT_TURN_MINUTES = 30
DEFAULT_GENERATION_MAX_OUTPUT_TOKENS = 4000
DEFAULT_GENERATION_TIMEOUT_SECONDS = 90
DEFAULT_EMBEDDING_DIMENSION = 3072
DEFAULT_GEMINI_GENERATION_MODEL = "openrouter/google/gemini-3.1-flash-lite-preview"
DEFAULT_GEMINI_EMBEDDING_MODEL = "openrouter/google/gemini-embedding-001"
DEFAULT_PERSONA_DISPLAY_NAME = "標準人格設定"
DEFAULT_PERSONA_INITIATIVE_BASELINE = "medium"
DEFAULT_ESTAT_MCP_SERVER_ID = "e-stat"
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


def build_default_console_motion() -> dict:
    # 既定モーション一覧はデータ資源として保持し、端末設定作成時に毎回独立した値を返す。
    text = resources.files("otomekairo").joinpath("default_console_motion.json").read_text(encoding="utf-8")
    return json.loads(text)


def build_default_console_client_settings(client_id: str) -> dict:
    # CocoroConsole 端末で実行する表示・入力・観測の既定値。
    return {
        "client_id": client_id,
        "process": {
            "console_api_port": 55600,
            "cocoro_shell_port": 55605,
        },
        "display": {
            "restore_window_position": False,
            "topmost": True,
            "escape_cursor": False,
            "escape_positions": [],
            "touch_virtual_key_enabled": False,
            "virtual_key": "Win+Tab",
            "auto_move": False,
            "show_message_window": True,
            "ambient_occlusion_enabled": False,
            "msaa_level": 4,
            "avatar_shadow_mode": 1,
            "avatar_shadow_resolution": 0,
            "background_shadow_mode": 2,
            "background_shadow_resolution": 2,
            "avatar_window_size": 1200,
            "avatar_position_x": 0.0,
            "avatar_position_y": 0.0,
            "message_window": {
                "max_message_count": 3,
                "max_total_characters": 300,
                "min_window_size": 200.0,
                "max_window_size": 600.0,
                "font_size": 14.0,
                "horizontal_offset": -0.2,
                "vertical_offset": 0.05,
            },
            "window_placements": {},
        },
        "desktop_capture": {
            "enabled": False,
            "capture_active_window_only": True,
            "idle_timeout_minutes": 10,
            "exclude_patterns": [
                ".*CocoroAI.*",
                ".*支払.*",
                ".*決済.*",
                ".*パスワード.*",
                ".*Password.*",
                ".*ログイン.*",
                ".*Login.*",
                ".*プライベート.*",
                ".*Private.*",
                ".*シークレット.*",
                ".*Secret.*",
                ".*incognito.*",
            ],
        },
        "avatar_presentations": [
            {
                "avatar_id": DEFAULT_AVATAR_ID,
                "model": "default",
                "convert_unlit_to_mtoon": False,
                "shadow_exclusion_enabled": True,
                "shadow_excluded_mesh_names": ["Face", "U_Char_1"],
            }
        ],
        "motion": build_default_console_motion(),
    }


# 構築
def build_default_state() -> dict:
    server_id = f"server:{uuid.uuid4().hex}"
    return {
        "server_id": server_id,
        "server_display_name": "OtomeKairo",
        "api_version": API_VERSION,
        "console_access_token": None,
        "selected_persona_id": DEFAULT_PERSONA_ID,
        "selected_memory_set_id": DEFAULT_MEMORY_SET_ID,
        "selected_model_preset_id": DEFAULT_MODEL_PRESET_ID,
        "selected_avatar_id": DEFAULT_AVATAR_ID,
        "thinking_speech_level": DEFAULT_THINKING_SPEECH_LEVEL,
        "selected_conversation_display_name_id": None,
        "conversation_display_names": {},
        "audio_output_settings": {
            "destination": "otomekairo",
            "local_output_device": None,
        },
        "microphone_settings": {
            "input_source": "local_microphone",
            "local_input_device": None,
            "console": None,
            "vad_probability_threshold": 0.5,
            "speaker_recognition_threshold": 0.6,
        },
        "wake_policy": {
            "mode": "disabled",
            "interval_seconds": DEFAULT_WAKE_INTERVAL_SECONDS,
        },
        "personas": {
            DEFAULT_PERSONA_ID: {
                "persona_id": DEFAULT_PERSONA_ID,
                "display_name": DEFAULT_PERSONA_DISPLAY_NAME,
                "initiative_baseline": DEFAULT_PERSONA_INITIATIVE_BASELINE,
                "persona_prompt": DEFAULT_PERSONA_PROMPT,
                "expression_addon": DEFAULT_PERSONA_EXPRESSION_ADDON,
                "wake_words": [],
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
            DEFAULT_ESTAT_MCP_SERVER_ID: build_default_estat_mcp_server(),
        },
        "console_client_settings": {},
    }


def build_default_avatar() -> dict:
    # CocoroConsole の音声設定と同じ単位で扱うアバター設定
    return {
        "avatar_id": DEFAULT_AVATAR_ID,
        "display_name": "初音ミクSD",
        "tts": {
            "enabled": False,
            "engine": "voicevox",
            "voicevox_config": {
                "endpoint_url": "http://127.0.0.1:50021",
                "secondary_endpoint_url": "",
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
                "assist_text": "",
                "assist_text_weight": 0.0,
                "reference_audio_path": "",
            },
            "aivis_cloud_config": {
                "api_key": "",
                "endpoint_url": "",
                "model_uuid": "",
                "speaker_uuid": "",
                "style_id": 0,
                "style_name": "",
                "use_ssml": False,
                "language": "ja",
                "speaking_rate": 1.0,
                "emotional_intensity": 1.0,
                "tempo_dynamics": 1.0,
                "pitch": 0.0,
                "volume": 1.0,
                "output_format": "wav",
                "output_bitrate": 0,
                "output_sampling_rate": 16000,
                "output_audio_channels": "mono",
            },
        },
        "stt": {
            "enabled": False,
            "engine": "amivoice",
            "profile_id": "",
            "api_key": "",
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


def build_default_estat_mcp_server() -> dict:
    # 総務省 e-Stat（政府統計）API 向け stdio MCP の雛形。APP ID は空で保持し秘密値は入れない。
    return {
        "mcp_server_id": DEFAULT_ESTAT_MCP_SERVER_ID,
        "connector_kind": "mcp_client",
        "client_id": "mcp-client-connector-main",
        "enabled": False,
        "transport": "stdio",
        "command": "uvx",
        "args": ["estat-mcp-server"],
        "cwd": None,
        "enabled_tools": [],
        "env": {
            "E_STAT_APP_ID": "",
        },
    }
