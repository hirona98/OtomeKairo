from __future__ import annotations

from otomekairo.service_common import ServiceError, debug_log
from otomekairo.service_config_inspection import ServiceConfigInspectionMixin
from otomekairo.service_config_resources import ServiceConfigResourcesMixin
from otomekairo.service_config_stream import ServiceConfigStreamMixin
from otomekairo.service_config_validation import ServiceConfigValidationMixin


# 設定Mixin
class ServiceConfigMixin(
    ServiceConfigStreamMixin,
    ServiceConfigInspectionMixin,
    ServiceConfigResourcesMixin,
    ServiceConfigValidationMixin,
):
    def _require_token(self, token: str | None) -> dict[str, object]:
        # 読み込み状態
        state = self.store.read_state()
        issued = state["console_access_token"]

        # 検証
        if issued is None:
            debug_log("Auth", "token rejected reason=bootstrap_required")
            raise ServiceError(401, "bootstrap_required", "A console_access_token has not been issued yet.")
        if token != issued:
            debug_log("Auth", f"token rejected reason=invalid_token supplied={bool(token)}")
            raise ServiceError(401, "invalid_token", "The console_access_token is missing or invalid.")
        return state

    def _bootstrap_state(self, state: dict[str, object]) -> str:
        # token 発行有無だけを外向き状態にする。
        if state["console_access_token"] is None:
            return "unregistered"
        return "registered"
