from __future__ import annotations

RECALL_HINT_RECENT_TURN_LIMIT = 6
VISUAL_OBSERVATION_IMAGE_LIMIT = 1
VISUAL_OBSERVATION_DATA_URI_PREFIX = "data:image/"
WORLD_STATE_CONTEXT_KEYS_BY_TYPE = (
    ("visual_context", "visual_context"),
    ("external_service", "external_service_context"),
    ("body", "body_context"),
    ("device", "device_context"),
    ("schedule", "schedule_context"),
    ("social_context", "social_context_context"),
    ("environment", "environment_context"),
    ("location", "location_context"),
)
WORLD_STATE_FOREGROUND_LIMIT = 4
WORLD_STATE_MAX_ACTIVE = 12
WORLD_STATE_USER_INPUT_REQUEST_TERMS = (
    "確認",
    "教えて",
    "知りたい",
    "チェック",
)
WORLD_STATE_USER_INPUT_CURRENT_STATE_TERMS_BY_TYPE = {
    "body": (
        "体調",
        "身体",
        "疲労",
        "眠気",
        "姿勢",
    ),
    "device": (
        "端末",
        "接続",
        "電源",
        "バッテリー",
        "ネットワーク",
    ),
    "environment": (
        "環境",
        "周囲",
        "部屋",
        "騒音",
        "明るさ",
        "作業環境",
    ),
    "location": (
        "場所",
        "居場所",
        "現在地",
        "作業場所",
        "どこ",
    ),
    "social_context": (
        "会話",
        "連絡",
        "通知",
        "チャット",
        "Slack",
        "Discord",
        "会議",
        "打ち合わせ",
        "やり取り",
    ),
}
INITIATIVE_BASELINE_SCORES = {
    "low": 0.18,
    "medium": 0.3,
    "high": 0.42,
}
INITIATIVE_DRIVE_KIND_SCORES = {
    "follow_through": 0.2,
    "relationship_attunement": 0.18,
    "user_attention": 0.16,
    "self_regulation": 0.14,
    "topic_continuation": 0.12,
    "resume_when_ready": 0.1,
}
INITIATIVE_DRIVE_FRESHNESS_ADJUSTMENTS = {
    "fresh": 0.06,
    "warm": 0.03,
    "stale": -0.02,
}
INITIATIVE_AUTONOMOUS_PROBE_SCORE = 0.08
INITIATIVE_AUTONOMOUS_PROBE_THRESHOLD = 0.34
DESKTOP_SCENE_SIMILARITY_THRESHOLD = 0.3
WORLD_STATE_HINT_SCORES = {
    "low": 0.35,
    "medium": 0.65,
    "high": 0.85,
}
WORLD_STATE_TTL_SECONDS_BY_TYPE = {
    "visual_context": {
        "visual_summary_text": {"short": 600, "medium": 900, "long": 1800},
        "summary_text": {"short": 600, "medium": 900, "long": 1800},
    },
    "environment": {
        "capability_result.environment_summary": {"short": 900, "medium": 2400, "long": 7200},
        "client_context.environment_summary": {"short": 900, "medium": 2400, "long": 7200},
        "capability_result.client_context.environment_summary": {"short": 900, "medium": 2400, "long": 7200},
        "environment_summary": {"short": 900, "medium": 2400, "long": 7200},
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
    "location": {
        "capability_result.location_summary": {"short": 1800, "medium": 3600, "long": 14400},
        "client_context.location_summary": {"short": 1800, "medium": 3600, "long": 14400},
        "capability_result.client_context.location_summary": {"short": 1800, "medium": 3600, "long": 14400},
        "location_summary": {"short": 1800, "medium": 3600, "long": 14400},
        "summary_text": {"short": 1800, "medium": 3600, "long": 14400},
    },
    "external_service": {
        "capability_result.status_text": {"short": 1800, "medium": 7200, "long": 21600},
        "client_context.external_service_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "capability_result.client_context.external_service_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "status_text": {"short": 1800, "medium": 7200, "long": 21600},
        "external_service_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "summary_text": {"short": 1200, "medium": 3600, "long": 10800},
    },
    "body": {
        "capability_result.body_state_summary": {"short": 900, "medium": 2400, "long": 7200},
        "client_context.body_state_summary": {"short": 900, "medium": 2400, "long": 7200},
        "capability_result.client_context.body_state_summary": {"short": 900, "medium": 2400, "long": 7200},
        "body_state_summary": {"short": 900, "medium": 2400, "long": 7200},
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
    "device": {
        "capability_result.device_state_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "client_context.device_state_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "capability_result.client_context.device_state_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "device_state_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
    "schedule": {
        "capability_result.schedule_slots": {"short": 3600, "medium": 10800, "long": 21600},
        "capability_result.client_context.schedule_slots": {"short": 3600, "medium": 10800, "long": 21600},
        "client_context.schedule_slots": {"short": 2400, "medium": 7200, "long": 18000},
        "capability_result.schedule_summary": {"short": 1800, "medium": 5400, "long": 14400},
        "client_context.schedule_summary": {"short": 1800, "medium": 5400, "long": 14400},
        "capability_result.client_context.schedule_summary": {"short": 1800, "medium": 5400, "long": 14400},
        "schedule_summary": {"short": 1800, "medium": 5400, "long": 14400},
        "pending_intent": {"short": 900, "medium": 3600, "long": 10800},
        "summary_text": {"short": 1800, "medium": 5400, "long": 14400},
    },
    "social_context": {
        "capability_result.social_context_summary": {"short": 900, "medium": 2400, "long": 7200},
        "client_context.social_context_summary": {"short": 900, "medium": 2400, "long": 7200},
        "capability_result.client_context.social_context_summary": {"short": 900, "medium": 2400, "long": 7200},
        "social_context_summary": {"short": 900, "medium": 2400, "long": 7200},
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
}
