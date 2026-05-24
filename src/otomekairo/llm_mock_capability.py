from __future__ import annotations

from typing import Any

from otomekairo.llm_contexts import InitiativeCandidateFamily, InitiativeContext
from otomekairo.llm_contracts import validate_visual_observation_contract


MOCK_CAPABILITY_REQUEST_RULES = (
    (
        "vision.capture",
        "_should_mock_vision_capture_request",
        "_build_mock_vision_capture_request_input",
        "現在の画面状態を観測する必要がある。",
    ),
    (
        "schedule.status",
        "_should_mock_schedule_status_request",
        "_build_mock_schedule_status_request_input",
        "近い予定やカレンダー状態を確認する必要がある。",
    ),
    (
        "body.status",
        "_should_mock_body_status_request",
        "_build_mock_body_status_request_input",
        "身体や体調の現在状態を確認する必要がある。",
    ),
    (
        "social.status",
        "_should_mock_social_status_request",
        "_build_mock_social_status_request_input",
        "対人文脈や連絡状況の現在状態を確認する必要がある。",
    ),
    (
        "environment.status",
        "_should_mock_environment_status_request",
        "_build_mock_environment_status_request_input",
        "周囲や作業環境の現在状態を確認する必要がある。",
    ),
    (
        "location.status",
        "_should_mock_location_status_request",
        "_build_mock_location_status_request_input",
        "場所や移動に関わる現在状態を確認する必要がある。",
    ),
    (
        "device.status",
        "_should_mock_device_status_request",
        "_build_mock_device_status_request_input",
        "端末や接続の現在状態を確認する必要がある。",
    ),
    (
        "external.status",
        "_should_mock_external_status_request",
        "_build_mock_external_status_request_input",
        "外部サービスの現在状態を確認する必要がある。",
    ),
)


class LLMMockCapabilityMixin:
    def generate_visual_observation_summary(
        self,
        role_definition: dict,
        source_pack: dict[str, Any],
        images: list[str],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # context
        _ = images
        image_input_kind = str(source_pack.get("image_input_kind") or "").strip() if isinstance(source_pack, dict) else ""
        client_context = source_pack.get("client_context", {}) if isinstance(source_pack, dict) else {}
        active_app = ""
        window_title = ""
        if isinstance(client_context, dict):
            active_app = str(client_context.get("active_app") or "").strip()
            window_title = str(client_context.get("window_title") or "").strip()

        # summary
        if image_input_kind == "conversation_attachment":
            summary_text = "添付画像には、会話で確認したい主題が中央に写り、その周囲の表示や文脈も読み取れる。"
        elif active_app and window_title:
            if active_app in {"Slack", "Discord", "Teams"}:
                channel_name = window_title.split("|", 1)[0].strip()
                summary_text = (
                    f"{active_app} の {channel_name} が写っており、左に一覧、中央に会話ログ、"
                    "周辺に関連ペインが見えている。"
                )
            else:
                summary_text = (
                    f"{active_app} の {window_title} が写っており、中央の主要内容と周辺の操作領域が見えている。"
                )
        elif active_app:
            summary_text = f"{active_app} の画面が写っており、主要内容と周辺 UI が見えている。"
        elif window_title:
            summary_text = f"{window_title} を中心にした画面が写っており、主題と周辺表示が見えている。"
        else:
            summary_text = "現在の画像内容が見えており、主題となる内容と周辺の表示が読み取れる。"

        payload = {
            "summary_text": summary_text,
            "confidence_hint": "medium",
        }
        validate_visual_observation_contract(payload)
        return payload

    def _build_mock_vision_capture_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = normalized
        return self._mock_vision_capture_input(capability_decision_view)

    def _build_mock_schedule_status_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = capability_decision_view
        return self._mock_schedule_status_input(normalized)

    def _build_mock_body_status_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = capability_decision_view
        return self._mock_body_status_input(normalized)

    def _build_mock_social_status_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = capability_decision_view
        return self._mock_social_status_input(normalized)

    def _build_mock_environment_status_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = capability_decision_view
        return self._mock_environment_status_input(normalized)

    def _build_mock_location_status_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = capability_decision_view
        return self._mock_location_status_input(normalized)

    def _build_mock_device_status_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = capability_decision_view
        return self._mock_device_status_input(normalized)

    def _build_mock_external_status_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = capability_decision_view
        return self._mock_external_status_input(normalized)

    def _mock_autonomous_initiative_capability_request(
        self,
        *,
        initiative_context: InitiativeContext | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        if initiative_context is None:
            return None
        selected_family = self._selected_initiative_family_entry(initiative_context)
        if selected_family is None:
            return None
        preferred_result_kind = str(selected_family.preferred_result_kind or "").strip()
        if preferred_result_kind != "capability_request":
            return None
        preferred_capability_id = str(selected_family.preferred_capability_id or "").strip()
        preferred_capability_input = selected_family.preferred_capability_input
        if (
            preferred_capability_id
            and isinstance(preferred_capability_input, dict)
            and self._mock_capability_available(capability_decision_view, preferred_capability_id)
        ):
            return {
                "capability_id": preferred_capability_id,
                "input": preferred_capability_input,
            }
        ongoing_action_summary = initiative_context.ongoing_action_summary
        if not isinstance(ongoing_action_summary, dict):
            return None
        capability_id = str(ongoing_action_summary.get("last_capability_id") or "").strip()
        vision_input = self._mock_vision_capture_input(capability_decision_view)
        if (
            capability_id == "vision.capture"
            and self._mock_capability_available(capability_decision_view, capability_id)
            and vision_input is not None
        ):
            return {
                "capability_id": capability_id,
                "input": vision_input,
            }
        return None

    def _selected_initiative_family_entry(
        self,
        initiative_context: InitiativeContext,
    ) -> InitiativeCandidateFamily | None:
        return initiative_context.selected_family_entry()

    def _should_mock_vision_capture_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict) and ongoing_action_summary.get("status") == "waiting_result":
            return False
        if not self._mock_capability_available(capability_decision_view, "vision.capture"):
            return False
        if self._mock_vision_capture_input(capability_decision_view) is None:
            return False
        markers = (
            "画面",
            "スクリーン",
            "視覚",
            "カメラ",
            "今見えて",
            "見えている",
            "表示",
            "ウィンドウ",
            "キャプチャ",
            "デスクトップ",
        )
        if not any(marker in normalized for marker in markers):
            return False
        action_markers = (
            "見て",
            "見える",
            "確認",
            "読んで",
            "教えて",
            "何",
            "どう",
        )
        return any(marker in normalized for marker in action_markers)

    def _should_mock_external_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "external.status"):
            return False
        if self._mock_external_status_service(normalized) is None:
            return False
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "状況",
            "状態",
            "知りたい",
        )
        return any(marker in normalized for marker in action_markers)

    def _should_mock_schedule_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "schedule.status"):
            return False
        schedule_markers = (
            "予定",
            "カレンダー",
            "スケジュール",
            "このあと",
            "今日",
            "近日",
        )
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "知りたい",
        )
        return any(marker in normalized for marker in schedule_markers) and any(
            marker in normalized for marker in action_markers
        )

    def _mock_schedule_status_input(self, normalized: str) -> dict[str, str]:
        if "今日" in normalized:
            return {"range": "today"}
        if "このあと" in normalized:
            return {"range": "upcoming"}
        return {"range": "near_future"}

    def _should_mock_body_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "body.status"):
            return False
        body_markers = (
            "身体",
            "体",
            "体調",
            "疲れ",
            "眠気",
            "眠い",
            "姿勢",
            "肩",
            "首",
        )
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "知りたい",
        )
        return any(marker in normalized for marker in body_markers) and any(
            marker in normalized for marker in action_markers
        )

    def _mock_body_status_input(self, normalized: str) -> dict[str, str]:
        if "眠" in normalized:
            return {"scope": "sleepiness"}
        if "肩" in normalized or "首" in normalized or "姿勢" in normalized:
            return {"scope": "posture"}
        return {"scope": "body"}

    def _should_mock_social_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "social.status"):
            return False
        if self._mock_external_status_service(normalized) is not None:
            return False
        social_markers = (
            "会話",
            "連絡",
            "通知",
            "チャット",
            "Slack",
            "Discord",
            "Teams",
            "会議",
            "打ち合わせ",
            "やり取り",
        )
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "知りたい",
        )
        return any(marker in normalized for marker in social_markers) and any(
            marker in normalized for marker in action_markers
        )

    def _mock_social_status_input(self, normalized: str) -> dict[str, str]:
        if "通知" in normalized or "連絡" in normalized:
            return {"scope": "messages"}
        if "会議" in normalized or "打ち合わせ" in normalized:
            return {"scope": "meeting"}
        return {"scope": "current_social_context"}

    def _should_mock_environment_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "environment.status"):
            return False
        environment_markers = (
            "環境",
            "周囲",
            "部屋",
            "作業環境",
            "騒音",
            "静か",
            "明るさ",
            "照明",
        )
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "知りたい",
        )
        return any(marker in normalized for marker in environment_markers) and any(
            marker in normalized for marker in action_markers
        )

    def _mock_environment_status_input(self, normalized: str) -> dict[str, str]:
        if "騒音" in normalized or "静か" in normalized:
            return {"scope": "noise"}
        if "明るさ" in normalized or "照明" in normalized:
            return {"scope": "lighting"}
        return {"scope": "workspace"}

    def _should_mock_location_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "location.status"):
            return False
        location_markers = (
            "場所",
            "居場所",
            "位置",
            "移動",
            "外出",
            "自宅",
            "作業場所",
            "どこ",
        )
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "知りたい",
        )
        return any(marker in normalized for marker in location_markers) and any(
            marker in normalized for marker in action_markers
        )

    def _mock_location_status_input(self, normalized: str) -> dict[str, str]:
        if "移動" in normalized or "外出" in normalized:
            return {"scope": "mobility"}
        if "作業場所" in normalized:
            return {"scope": "workspace"}
        return {"scope": "current"}

    def _should_mock_device_status_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict):
            status = str(ongoing_action_summary.get("status") or "").strip()
            if status in {"active", "waiting_result"}:
                return False
        if normalized.startswith("capability result を受信"):
            return False
        if not self._mock_capability_available(capability_decision_view, "device.status"):
            return False
        device_markers = (
            "デバイス",
            "端末",
            "PC",
            "パソコン",
            "接続",
            "電源",
            "バッテリー",
            "ネットワーク",
        )
        action_markers = (
            "確認",
            "教えて",
            "見て",
            "チェック",
            "知りたい",
        )
        return any(marker in normalized for marker in device_markers) and any(
            marker in normalized for marker in action_markers
        )

    def _mock_device_status_input(self, normalized: str) -> dict[str, str]:
        if "バッテリー" in normalized or "電源" in normalized:
            return {"scope": "power"}
        if "ネットワーク" in normalized or "接続" in normalized:
            return {"scope": "connectivity"}
        return {"scope": "device"}

    def _mock_external_status_input(self, normalized: str) -> dict[str, str]:
        service = self._mock_external_status_service(normalized)
        if service is None:
            service = "external_service"
        return {
            "service": service,
        }

    def _mock_external_status_service(self, normalized: str) -> str | None:
        lowered = normalized.lower()
        if "github" in lowered or any(token in normalized for token in ("GitHub", "プルリク", "レビュー")):
            return "github"
        if "calendar" in lowered or any(token in normalized for token in ("カレンダー", "スケジュール")):
            return "calendar"
        return None

    def _mock_vision_capture_input(
        self,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, str] | None:
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") != "vision.capture" or item.get("available") is not True:
                continue
            source_id = self._mock_default_vision_source_id(item.get("vision_sources"))
            if source_id is None:
                return None
            return {
                "vision_source_id": source_id,
                "mode": "still",
            }
        return None

    def _mock_default_vision_source_id(self, value: Any) -> str | None:
        if not isinstance(value, list):
            return None
        for default_name in ("visual", "desktop", "camera"):
            for source in value:
                if not isinstance(source, dict):
                    continue
                source_id = source.get("vision_source_id")
                default_for = source.get("default_for")
                if (
                    isinstance(source_id, str)
                    and source_id.strip()
                    and isinstance(default_for, list)
                    and default_name in default_for
                ):
                    return source_id.strip()
        for source in value:
            if not isinstance(source, dict):
                continue
            source_id = source.get("vision_source_id")
            if isinstance(source_id, str) and source_id.strip():
                return source_id.strip()
        return None

    def _mock_capability_available(
        self,
        capability_decision_view: list[dict[str, Any]] | None,
        capability_id: str,
    ) -> bool:
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == capability_id and item.get("available") is True:
                return True
        return False
