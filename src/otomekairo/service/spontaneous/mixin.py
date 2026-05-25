from __future__ import annotations

from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.service.common import ServiceError, debug_log
from otomekairo.service.spontaneous.capability_result import ServiceSpontaneousCapabilityResultMixin
from otomekairo.service.spontaneous.pending_intent import ServiceSpontaneousPendingIntentMixin
from otomekairo.service.spontaneous.wake import ServiceSpontaneousWakeMixin


# 自発Mixin
class ServiceSpontaneousMixin(
    ServiceSpontaneousCapabilityResultMixin,
    ServiceSpontaneousWakeMixin,
    ServiceSpontaneousPendingIntentMixin,
):
    def trigger_wake(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # クライアントコンテキスト
        client_context = payload.get("client_context", {})
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        # 実行
        debug_log("Wake", f"manual trigger context_keys={self._debug_context_keys(client_context)}")
        return self._execute_wake_cycle(
            state=state,
            client_context=client_context,
            trigger_kind="wake",
        )

    def submit_capability_result(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._require_token(token)

        request_id = payload.get("request_id")
        client_id = payload.get("client_id")
        capability_id = payload.get("capability_id")
        result_payload = payload.get("result")

        if not isinstance(request_id, str) or not request_id.strip():
            raise ServiceError(400, "invalid_request_id", "request_id must be a non-empty string.")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_client_id", "client_id must be a non-empty string.")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ServiceError(400, "invalid_capability_id", "capability_id must be a non-empty string.")
        if not isinstance(result_payload, dict):
            raise ServiceError(400, "invalid_result_payload", "result must be an object.")

        normalized_request_id = request_id.strip()
        normalized_client_id = client_id.strip()
        normalized_capability_id = capability_id.strip()
        if capability_manifests().get(normalized_capability_id) is None:
            raise ServiceError(400, "invalid_capability_id", "capability_id is unknown.")
        normalized_result_payload = self._normalize_capability_result_payload(
            capability_id=normalized_capability_id,
            result_payload=result_payload,
        )
        accepted_at = self._now_iso()
        return self._submit_async_capability_result_response(
            state=state,
            capability_id=normalized_capability_id,
            request_id=normalized_request_id,
            client_id=normalized_client_id,
            result_payload=normalized_result_payload,
            accepted_at=accepted_at,
            log_channel=self._capability_result_log_channel(normalized_capability_id),
            accepted_detail=self._capability_result_accepted_detail(
                capability_id=normalized_capability_id,
                result_payload=normalized_result_payload,
            ),
        )
