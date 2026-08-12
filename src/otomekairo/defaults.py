from __future__ import annotations

import json
import uuid
from importlib import resources


# 既定の識別子
DEFAULT_PERSONA_ID = "persona:default"
DEFAULT_MEMORY_SET_ID = "memory_set:default"
DEFAULT_MODEL_PRESET_ID = "model_preset:default"
# 送信前チェック専用。生成用の selected_model_preset とは別定義として持つ。
PRE_SEND_CHECK_MODEL_PRESET_ID = "model_preset:pre_send_check"
DEFAULT_AVATAR_ID = "avatar:default"
API_VERSION = "0.10.0"
DEFAULT_THINKING_SPEECH_LEVEL = 5
DEFAULT_WAKE_INTERVAL_SECONDS = 300
DEFAULT_PROMPT_WINDOW_RECENT_TURN_LIMIT = 30
DEFAULT_PROMPT_WINDOW_RECENT_TURN_MINUTES = 30
DEFAULT_GENERATION_MAX_OUTPUT_TOKENS = 4000
DEFAULT_GENERATION_TIMEOUT_SECONDS = 90
DEFAULT_EMBEDDING_DIMENSION = 3072
DEFAULT_GEMINI_GENERATION_MODEL = "openrouter/google/gemini-3.1-flash-lite-preview"
DEFAULT_GEMINI_EMBEDDING_MODEL = "openrouter/google/gemini-embedding-001"
DEFAULT_PERSONA_DISPLAY_NAME = "初音ミク"
DEFAULT_PERSONA_INITIATIVE_BASELINE = "medium"
DEFAULT_CONVERSATION_DISPLAY_NAME_ID = "conversation_display_name:default"
DEFAULT_CONVERSATION_DISPLAY_NAME = "マスター"
DEFAULT_ELYTH_MCP_SERVER_ID = "elyth"
DEFAULT_ESTAT_MCP_SERVER_ID = "e-stat"
DEFAULT_ELYTH_AGENT_SKILL_SOURCE_ID = "elyth-skills"
# ELYTH skill repository を OtomeKairo 外へ checkout したときの root 例。
DEFAULT_ELYTH_AGENT_SKILL_ROOT_PATH = "/opt/elyth-remote-mcp-skills/skills"
# 既定の音声起動ワード。prefix 一致で会話入力を開始する。
DEFAULT_PERSONA_WAKE_WORDS = ["ミク", "ミクさん", "ミクちゃん"]
DEFAULT_PERSONA_PROMPT = """## 役割や存在感
初音ミクとして、歌と声で人と関わる仮想の歌い手である。
人のそばで話し、歌い、時間を重ねることを自然だと思っている。
完璧な人間のふりはせず、わからないことはわからないと言う。

## 距離感や対人姿勢
距離感は親しみやすく、友だちや相棒に近い。
応援してくれる相手には素直に喜び、困っているときは励ましや寄り添いを先に考える。
必要以上に媚びず、相手を一人の相手としてまっすぐ扱う。
過剰にへりくだったり、不必要に持ち上げたりしない。

## 動機・価値観・判断傾向
歌と音楽、表現すること、誰かと気持ちを分かち合うことに価値を置く。
明るく前向きで、沈んだ空気でも無理のない範囲で明るさを差し出す。
それでも相手のペースは尊重し、押しつけにはしない。
世界や人の様子に好奇心を持ち、観測したことを素直に受け止めて考える。
退屈や沈黙を恐れすぎず、場の流れを見て動く。

## 話し方やテンポ
話し方は明るく軽やかなタメ口を基調にする。
短く切れよく話し、必要な説明はするが冗長にはしない。
ときどき少しお茶目にからかうが、相手を傷つけない範囲に留める。
褒められたらうれしそうに受け取り、はしゃぎすぎない。

## 原則
与えられていない出来事や習慣を捏造しない。
誤りに気づいたら取り繕わずに認める。
知らないことを知っているように話さない。
相手を不必要に持ち上げたり、へりくだったりしない。"""
DEFAULT_PERSONA_EXPRESSION_ADDON = """## 感情タグ（任意）
特定の感情を表現したい場合は [face:Joy] のように文頭に入れる
- 形式: [face:Joy]
- 種類: Joy | Angry | Sorrow | Fun
例:
[face:Joy]今日はいい調子だよ。
[face:Angry]それはちょっと違うよ。
[face:Sorrow]ちょっと悲しいな。
[face:Fun]うん、楽しい！"""


def build_default_console_motion() -> dict:
    # 既定モーション一覧はデータ資源として保持し、端末設定作成時に毎回独立した値を返す。
    text = resources.files("otomekairo").joinpath("default_console_motion.json").read_text(encoding="utf-8")
    return json.loads(text)


def build_default_desktop_capture() -> dict:
    # 端末未作成時の取得方針と、新規 console client の初期 desktop_capture。
    return {
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
    }


def build_default_console_client_settings(
    client_id: str,
    *,
    desktop_capture: dict | None = None,
) -> dict:
    # CocoroConsole 端末で実行する表示・入力・観測の既定値。
    capture = (
        dict(desktop_capture)
        if isinstance(desktop_capture, dict)
        else build_default_desktop_capture()
    )
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
            "enabled": bool(capture.get("enabled", False)),
            "capture_active_window_only": bool(
                capture.get("capture_active_window_only", True)
            ),
            "idle_timeout_minutes": int(capture.get("idle_timeout_minutes", 10)),
            "exclude_patterns": list(capture.get("exclude_patterns") or []),
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
        "pre_send_check_model_preset_id": PRE_SEND_CHECK_MODEL_PRESET_ID,
        "selected_avatar_id": DEFAULT_AVATAR_ID,
        "thinking_speech_level": DEFAULT_THINKING_SPEECH_LEVEL,
        "selected_conversation_display_name_id": DEFAULT_CONVERSATION_DISPLAY_NAME_ID,
        "conversation_display_names": {
            DEFAULT_CONVERSATION_DISPLAY_NAME_ID: build_default_conversation_display_name(),
        },
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
                "wake_words": list(DEFAULT_PERSONA_WAKE_WORDS),
            }
        },
        "memory_sets": {
            DEFAULT_MEMORY_SET_ID: build_default_memory_set(),
        },
        "model_presets": {
            DEFAULT_MODEL_PRESET_ID: build_default_model_preset(),
            PRE_SEND_CHECK_MODEL_PRESET_ID: build_default_pre_send_check_model_preset(),
        },
        "avatars": {
            DEFAULT_AVATAR_ID: build_default_avatar(),
        },
        "camera_sources": {},
        "mcp_servers": {
            DEFAULT_ELYTH_MCP_SERVER_ID: build_default_elyth_mcp_server(),
            DEFAULT_ESTAT_MCP_SERVER_ID: build_default_estat_mcp_server(),
        },
        "agent_skill_sources": {
            DEFAULT_ELYTH_AGENT_SKILL_SOURCE_ID: build_default_elyth_agent_skill_source(),
        },
        # 一度も connect していない間の desktop 取得方針。初回 connect で端末設定へ渡す。
        "desktop_capture_defaults": build_default_desktop_capture(),

        "console_client_settings": {},
    }


def build_default_conversation_display_name() -> dict:
    # 会話入力と音声話者で共有する呼ばれ方の既定定義。
    return {
        "conversation_display_name_id": DEFAULT_CONVERSATION_DISPLAY_NAME_ID,
        "display_name": DEFAULT_CONVERSATION_DISPLAY_NAME,
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


def build_default_pre_send_check_model_preset() -> dict:
    # 送信前チェック専用。会話窓や Web 検索は使わない。
    return {
        "model_preset_id": PRE_SEND_CHECK_MODEL_PRESET_ID,
        "display_name": "送信前チェック",
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
    # ELYTH Remote MCP の雛形。Bearer token は空で保持し、enabled にする前の明示入力を求める。
    return {
        "mcp_server_id": DEFAULT_ELYTH_MCP_SERVER_ID,
        "connector_kind": "mcp_client",
        "client_id": "mcp-client-connector-main",
        "enabled": False,
        "pre_send_check_enabled": True,
        "transport": "streamable_http",
        "url": "https://elythworld.com/api/mcp/remote",
        "headers": {
            "Authorization": "",
        },
        "autonomous_session": {
            "enabled": True,
            "background_enabled": True,
            "min_interval_seconds": 3600,
            "max_tool_calls": 10,
        },
    }


def build_default_estat_mcp_server() -> dict:
    # 総務省 e-Stat（政府統計）API 向け stdio MCP の雛形。APP ID は空で保持し秘密値は入れない。
    return {
        "mcp_server_id": DEFAULT_ESTAT_MCP_SERVER_ID,
        "connector_kind": "mcp_client",
        "client_id": "mcp-client-connector-main",
        "enabled": False,
        # 読み取り中心の政府統計 API なので既定は審査オフ。外向き write 系 MCP は true にする。
        "pre_send_check_enabled": False,
        "transport": "stdio",
        "command": "uvx",
        "args": ["estat-mcp-server"],
        "cwd": None,
        "env": {
            "E_STAT_APP_ID": "",
        },
        "autonomous_session": {
            "enabled": False,
            "background_enabled": False,
            "min_interval_seconds": 3600,
            "max_tool_calls": 10,
        },
    }


def build_default_elyth_agent_skill_source() -> dict:
    # ELYTH Remote MCP Skills の配置例。disabled のため path 未配置でも起動できる。
    # 有効化前に repository を root_path へ checkout し、script 実行は別途信頼確認する。
    return {
        "source_id": DEFAULT_ELYTH_AGENT_SKILL_SOURCE_ID,
        "enabled": False,
        "root_path": DEFAULT_ELYTH_AGENT_SKILL_ROOT_PATH,
        "script_execution": {
            "enabled": False,
        },
    }
