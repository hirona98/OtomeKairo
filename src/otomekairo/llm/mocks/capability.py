from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import InitiativeCandidateFamily, InitiativeContext
from otomekairo.llm.contracts import validate_visual_observation_contract


MOCK_CAPABILITY_REQUEST_RULES = (
    (
        "camera.ptz",
        "_should_mock_camera_ptz_request",
        "_build_mock_camera_ptz_request_input",
        "OtomeKairo 自身のカメラの向きや画角を調整する必要がある。",
    ),
    (
        "vision.capture",
        "_should_mock_vision_capture_request",
        "_build_mock_vision_capture_request_input",
        "現在の画面状態を観測する必要がある。",
    ),
)


class LLMMockCapabilityMixin:
    def generate_visual_observation_summary(
        self,
        model_config: dict,
        source_pack: dict[str, Any],
        images: list[str],
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(model_config)

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

        change_state, change_basis, change_reason_summary = self._mock_visual_observation_change(
            source_pack=source_pack,
            summary_text=summary_text,
        )
        payload = {
            "summary_text": summary_text,
            "confidence_hint": "medium",
            "change_state": change_state,
            "change_basis": change_basis,
            "change_reason_summary": change_reason_summary,
        }
        validate_visual_observation_contract(payload)
        return payload

    def _mock_visual_observation_change(
        self,
        *,
        source_pack: dict[str, Any],
        summary_text: str,
    ) -> tuple[str, str, str]:
        change_context = source_pack.get("change_context") if isinstance(source_pack, dict) else None
        if not isinstance(change_context, dict):
            return "first_seen", "no_previous_observation", "前回観測が無いため初回観測として扱う。"
        prompted = change_context.get("last_prompted_observation_context")
        if isinstance(prompted, dict) and prompted.get("summary_text") == summary_text:
            return "same_as_recent_speech", "recent_speech_repetition", "直近発話に使った視覚観測と同じ内容。"
        previous = change_context.get("previous_observation_context")
        if not isinstance(previous, dict):
            return "first_seen", "no_previous_observation", "前回観測が無いため初回観測として扱う。"
        if previous.get("summary_text") == summary_text:
            return "stable", "semantic_stability", "前回観測と意味上同じ内容。"
        return "changed", "semantic_change", "前回観測から意味上変化している。"

    def _build_mock_vision_capture_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        _ = normalized
        return self._mock_vision_capture_input(capability_decision_view)

    def _build_mock_camera_ptz_request_input(
        self,
        *,
        normalized: str,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        operation = self._mock_camera_ptz_operation(normalized)
        if operation is None:
            return None
        amount = self._mock_camera_ptz_amount(normalized)
        return self._mock_camera_ptz_input(
            capability_decision_view=capability_decision_view,
            operation=operation,
            amount=amount,
        )

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

    def _should_mock_camera_ptz_request(
        self,
        *,
        normalized: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> bool:
        if isinstance(ongoing_action_summary, dict) and ongoing_action_summary.get("status") == "waiting_result":
            return False
        if not self._mock_capability_available(capability_decision_view, "camera.ptz"):
            return False
        operation = self._mock_camera_ptz_operation(normalized)
        if operation is None:
            return False
        amount = self._mock_camera_ptz_amount(normalized)
        if (
            self._mock_camera_ptz_input(
                capability_decision_view=capability_decision_view,
                operation=operation,
                amount=amount,
            )
            is None
        ):
            return False
        camera_markers = (
            "カメラ",
            "視野",
            "視界",
            "画角",
            "向き",
            "ズーム",
            "pan",
            "tilt",
        )
        return any(marker in normalized for marker in camera_markers)

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

    def _mock_camera_ptz_input(
        self,
        *,
        capability_decision_view: list[dict[str, Any]] | None,
        operation: str,
        amount: str,
    ) -> dict[str, str] | None:
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") != "camera.ptz" or item.get("available") is not True:
                continue
            source_id = self._mock_default_camera_ptz_source_id(
                value=item.get("vision_sources"),
                operation=operation,
                amount=amount,
            )
            if source_id is None:
                return None
            return {
                "vision_source_id": source_id,
                "operation": operation,
                "amount": amount,
            }
        return None

    def _mock_camera_ptz_operation(self, normalized: str) -> str | None:
        lowered = normalized.lower()
        if "zoom out" in lowered or "ズームアウト" in normalized or "引いて" in normalized or "広く" in normalized:
            return "zoom_out"
        if "zoom in" in lowered or "ズームイン" in normalized or "ズーム" in normalized or "拡大" in normalized:
            return "zoom_in"
        if "上" in normalized or "up" in lowered or "tilt up" in lowered:
            return "move_up"
        if "下" in normalized or "down" in lowered or "tilt down" in lowered:
            return "move_down"
        if "左" in normalized or "left" in lowered or "pan left" in lowered:
            return "move_left"
        if "右" in normalized or "right" in lowered or "pan right" in lowered:
            return "move_right"
        return None

    def _mock_camera_ptz_amount(self, normalized: str) -> str:
        lowered = normalized.lower()
        if "medium" in lowered or "midium" in lowered:
            return "medium"
        if any(marker in normalized for marker in ("少し", "すこし", "ちょっと", "ちょい", "微調整", "小さく", "小さめ")):
            return "small"
        if "small" in lowered:
            return "small"
        return "medium"

    def _mock_default_camera_ptz_source_id(
        self,
        *,
        value: Any,
        operation: str,
        amount: str,
    ) -> str | None:
        if not isinstance(value, list):
            return None
        candidates = [
            source
            for source in value
            if isinstance(source, dict)
            and source.get("available") is True
            and self._mock_camera_ptz_source_supports(source, operation=operation, amount=amount)
        ]
        for default_name in ("camera", "visual"):
            for source in candidates:
                source_id = source.get("vision_source_id")
                default_for = source.get("default_for")
                if (
                    isinstance(source_id, str)
                    and source_id.strip()
                    and isinstance(default_for, list)
                    and default_name in default_for
                ):
                    return source_id.strip()
        for source in candidates:
            source_id = source.get("vision_source_id")
            if isinstance(source_id, str) and source_id.strip():
                return source_id.strip()
        return None

    def _mock_camera_ptz_source_supports(
        self,
        source: dict[str, Any],
        *,
        operation: str,
        amount: str,
    ) -> bool:
        operations = source.get("supported_operations")
        amounts = source.get("supported_amounts")
        if not isinstance(operations, list) or not isinstance(amounts, list):
            supported_controls = source.get("supported_controls")
            control = supported_controls.get("camera.ptz") if isinstance(supported_controls, dict) else None
            operations = control.get("operations") if isinstance(control, dict) else []
            amounts = control.get("amounts") if isinstance(control, dict) else []
        return operation in operations and amount in amounts

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
