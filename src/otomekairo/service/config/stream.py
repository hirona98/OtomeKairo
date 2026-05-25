from __future__ import annotations

from typing import Any

from otomekairo.capabilities import (
    capability_decision_readiness_from_manifest,
    capability_manifests,
)
from otomekairo.event_stream import ServerWebSocket
from otomekairo.service.common import ServiceError, debug_log
from otomekairo.service.config.constants import (
    EVENT_STREAM_CAPABILITY_PERMISSIONS,
    VISION_SOURCE_KINDS,
)


class ServiceConfigStreamMixin:
    def register_event_stream_connection(self, websocket: ServerWebSocket) -> str:
        # レジストリ
        return self._event_stream_registry.add_connection(
            websocket,
            permissions=list(EVENT_STREAM_CAPABILITY_PERMISSIONS),
        )

    def handle_event_stream_message(self, session_id: str, payload: dict[str, Any]) -> None:
        # 型
        message_type = payload.get("type")
        if message_type != "hello":
            raise ServiceError(400, "invalid_event_stream_message", "Only hello messages are supported.")

        # 項目
        client_id = payload.get("client_id")
        caps = payload.get("caps", [])
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_client_id", "hello.client_id must be a non-empty string.")
        if not isinstance(caps, list):
            raise ServiceError(400, "invalid_caps", "hello.caps must be an array.")

        # binding 候補
        manifests = capability_manifests()
        seen_at = self._now_iso()
        accepted_capabilities: dict[str, str] = {}
        rejected_bindings: list[dict[str, Any]] = []
        granted_permissions = set(self._event_stream_registry.session_permissions(session_id))
        for cap in caps:
            if not isinstance(cap, dict):
                raise ServiceError(400, "invalid_caps", "hello.caps must contain capability objects.")
            capability_id = cap.get("id")
            offered_version = cap.get("version")
            if not isinstance(capability_id, str) or not capability_id.strip():
                raise ServiceError(400, "invalid_caps", "hello.caps[].id must be a non-empty string.")
            if not isinstance(offered_version, str) or not offered_version.strip():
                raise ServiceError(400, "invalid_caps", "hello.caps[].version must be a non-empty string.")

            capability_id = capability_id.strip()
            offered_version = offered_version.strip()
            manifest = manifests.get(capability_id)
            if manifest is None:
                rejected_bindings.append(
                    self._build_rejected_capability_binding(
                        client_id=client_id.strip(),
                        capability_id=capability_id,
                        offered_version=offered_version,
                        rejection_reason="unknown_capability",
                        seen_at=seen_at,
                    )
                )
                continue
            if offered_version != manifest["version"]:
                rejected_bindings.append(
                    self._build_rejected_capability_binding(
                        client_id=client_id.strip(),
                        capability_id=capability_id,
                        offered_version=offered_version,
                        rejection_reason="unsupported_version",
                        seen_at=seen_at,
                    )
                )
                continue
            required_permissions = [
                permission
                for permission in manifest.get("required_permissions", [])
                if isinstance(permission, str)
            ]
            missing_permissions = sorted(set(required_permissions) - granted_permissions)
            if missing_permissions:
                rejected_bindings.append(
                    self._build_rejected_capability_binding(
                        client_id=client_id.strip(),
                        capability_id=capability_id,
                        offered_version=offered_version,
                        rejection_reason="permission_denied",
                        seen_at=seen_at,
                    )
                )
                continue
            accepted_capabilities[capability_id] = offered_version
        vision_sources = self._normalize_hello_vision_sources(
            payload=payload,
            client_id=client_id.strip(),
            accepted_capabilities=accepted_capabilities,
            granted_permissions=granted_permissions,
        )

        # 登録
        try:
            self._event_stream_registry.register_hello(
                session_id,
                client_id=client_id.strip(),
                capabilities=accepted_capabilities,
                rejected_bindings=rejected_bindings,
                vision_sources=vision_sources,
            )
        except ValueError as exc:
            raise ServiceError(400, "invalid_vision_sources", str(exc)) from exc
        debug_log(
            "EventStream",
            (
                f"hello client_id={client_id.strip()} "
                f"accepted={sorted(accepted_capabilities)} rejected={len(rejected_bindings)} "
                f"vision_sources={len(vision_sources)}"
            ),
        )

    def patch_capability_state(
        self,
        token: str | None,
        capability_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 入力
        normalized_capability_id = capability_id.strip() if isinstance(capability_id, str) else ""
        if not normalized_capability_id:
            raise ServiceError(400, "invalid_capability_id", "capability_id must be a non-empty string.")
        manifest = capability_manifests().get(normalized_capability_id)
        if manifest is None:
            raise ServiceError(404, "capability_not_found", "The requested capability_id does not exist.")
        if set(payload.keys()) != {"paused"}:
            raise ServiceError(400, "invalid_capability_state", "capability state patch requires only paused.")
        paused = payload.get("paused")
        if not isinstance(paused, bool):
            raise ServiceError(400, "invalid_capability_paused", "paused must be a boolean.")

        # runtime state を更新する。in-flight request は破棄せず、以後の新規 dispatch だけを止める。
        self._set_capability_runtime_paused(
            capability_id=normalized_capability_id,
            paused=paused,
        )
        generated_at = self._now_iso()
        bindings = self._event_stream_registry.list_capability_bindings()
        vision_sources = bindings.get("vision_sources", [])
        active_ongoing_action = self._current_ongoing_action(
            state=state,
            current_time=generated_at,
        )
        availability = self._build_capability_availability(
            manifest=manifest,
            current_time=generated_at,
            bound_client_ids=bindings["accepted"].get(normalized_capability_id, []),
            rejected_bindings=bindings["rejected"],
            vision_sources=vision_sources if normalized_capability_id == "vision.capture" else None,
            active_ongoing_action=active_ongoing_action,
        )
        debug_log(
            "Capability",
            f"state patched capability={normalized_capability_id} paused={paused}",
        )
        return {
            "generated_at": generated_at,
            "capability": availability,
        }

    def unregister_event_stream_connection(self, session_id: str) -> None:
        # レジストリ
        self._event_stream_registry.remove_connection(session_id)

    def close_event_streams(self) -> None:
        # レジストリ
        self._event_stream_registry.close_all()

    def _build_rejected_capability_binding(
        self,
        *,
        client_id: str,
        capability_id: str,
        offered_version: str,
        rejection_reason: str,
        seen_at: str,
    ) -> dict[str, Any]:
        return {
            "client_id": client_id,
            "capability_id": capability_id,
            "offered_version": offered_version,
            "rejection_reason": rejection_reason,
            "seen_at": seen_at,
        }

    def _normalize_hello_vision_sources(
        self,
        *,
        payload: dict[str, Any],
        client_id: str,
        accepted_capabilities: dict[str, str],
        granted_permissions: set[str],
    ) -> list[dict[str, Any]]:
        raw_sources = payload.get("vision_sources")
        if "vision.capture" not in accepted_capabilities:
            if raw_sources is None:
                return []
            if isinstance(raw_sources, list) and not raw_sources:
                return []
            raise ServiceError(
                400,
                "invalid_vision_sources",
                "hello.vision_sources requires accepted vision.capture capability.",
            )
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ServiceError(
                400,
                "invalid_vision_sources",
                "hello.vision_sources must be a non-empty array when vision.capture is accepted.",
            )

        normalized_sources: list[dict[str, Any]] = []
        seen_source_ids: set[str] = set()
        for source in raw_sources:
            if not isinstance(source, dict):
                raise ServiceError(400, "invalid_vision_sources", "hello.vision_sources must contain objects.")
            source_id = self._normalize_vision_source_text(
                source.get("vision_source_id"),
                "hello.vision_sources[].vision_source_id",
            )
            if not source_id.startswith("vision_source:"):
                raise ServiceError(
                    400,
                    "invalid_vision_sources",
                    "hello.vision_sources[].vision_source_id must start with vision_source:.",
                )
            if source_id in seen_source_ids:
                raise ServiceError(400, "invalid_vision_sources", "hello.vision_sources contains duplicate ids.")
            seen_source_ids.add(source_id)

            capability_id = self._normalize_vision_source_text(
                source.get("capability_id"),
                "hello.vision_sources[].capability_id",
            )
            if capability_id != "vision.capture":
                raise ServiceError(
                    400,
                    "invalid_vision_sources",
                    "hello.vision_sources[].capability_id must be vision.capture.",
                )
            kind = self._normalize_vision_source_text(source.get("kind"), "hello.vision_sources[].kind")
            if kind not in VISION_SOURCE_KINDS:
                raise ServiceError(
                    400,
                    "invalid_vision_sources",
                    "hello.vision_sources[].kind must be desktop, camera, or virtual.",
                )
            label = self._normalize_vision_source_text(source.get("label"), "hello.vision_sources[].label")
            aliases = self._normalize_vision_source_text_list(
                source.get("aliases", []),
                "hello.vision_sources[].aliases",
            )
            default_for = self._normalize_vision_source_text_list(
                source.get("default_for", []),
                "hello.vision_sources[].default_for",
            )
            required_permissions = self._normalize_vision_source_text_list(
                source.get("required_permissions", []),
                "hello.vision_sources[].required_permissions",
            )
            missing_permissions = sorted(set(required_permissions) - granted_permissions)
            if missing_permissions:
                raise ServiceError(
                    400,
                    "invalid_vision_sources",
                    "hello.vision_sources[].required_permissions are not granted.",
                )
            normalized_sources.append(
                {
                    "vision_source_id": source_id,
                    "kind": kind,
                    "label": self._clamp(label, limit=80),
                    "aliases": aliases[:8],
                    "default_for": default_for[:8],
                    "client_id": client_id,
                    "capability_id": capability_id,
                    "required_permissions": required_permissions,
                }
            )
        return normalized_sources

    def _normalize_vision_source_text(self, value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ServiceError(400, "invalid_vision_sources", f"{label} must be a non-empty string.")
        return value.strip()

    def _normalize_vision_source_text_list(self, value: Any, label: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ServiceError(400, "invalid_vision_sources", f"{label} must be an array.")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ServiceError(400, "invalid_vision_sources", f"{label} must contain non-empty strings.")
            text = item.strip()
            if text in seen:
                continue
            seen.add(text)
            normalized.append(self._clamp(text, limit=80))
        return normalized

    def _build_capability_availability(
        self,
        *,
        manifest: dict[str, Any],
        current_time: str,
        bound_client_ids: list[str],
        rejected_bindings: list[dict[str, Any]],
        vision_sources: list[dict[str, Any]] | None = None,
        active_ongoing_action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        capability_id = manifest["id"]
        related_rejections = [
            binding
            for binding in rejected_bindings
            if binding.get("capability_id") == capability_id
        ]
        binding_status = "no_binding"
        if bound_client_ids:
            binding_status = "bound"
        elif related_rejections:
            binding_status = "rejected_only"

        required_permissions = list(manifest.get("required_permissions", []))
        missing_permissions: list[str] = []
        if not bound_client_ids and any(
            binding.get("rejection_reason") == "permission_denied"
            for binding in related_rejections
        ):
            missing_permissions = required_permissions
        state_policy = manifest.get("state_policy", {})
        runtime_state = self._capability_runtime_state_snapshot(
            capability_id=capability_id,
            current_time=current_time,
            active_ongoing_action=active_ongoing_action,
        )
        same_capability_waiting = (
            isinstance(state_policy, dict)
            and bool(state_policy.get("blocks_parallel_capability"))
            and isinstance(active_ongoing_action, dict)
            and active_ongoing_action.get("status") == "waiting_result"
            and active_ongoing_action.get("last_capability_id") == capability_id
        )
        parallel_blocked = (
            isinstance(state_policy, dict)
            and bool(state_policy.get("blocks_parallel_capability"))
            and isinstance(active_ongoing_action, dict)
            and active_ongoing_action.get("status") == "waiting_result"
            and active_ongoing_action.get("last_capability_id") != capability_id
        )
        paused = runtime_state.get("paused") is True
        busy = runtime_state.get("busy") is True or same_capability_waiting
        cooldown_active = runtime_state.get("cooldown_active") is True
        unavailable_active = runtime_state.get("unavailable_active") is True
        available = (
            bool(bound_client_ids)
            and not missing_permissions
            and not paused
            and not busy
            and not unavailable_active
            and not parallel_blocked
        )
        normalized_vision_sources = self._inspection_vision_sources(vision_sources)
        has_vision_source = bool(normalized_vision_sources)
        if capability_id == "vision.capture" and available and not has_vision_source:
            available = False
        unavailable_reason = None
        if not available:
            if missing_permissions:
                unavailable_reason = "permission_denied"
            elif paused:
                unavailable_reason = "paused"
            elif unavailable_active:
                unavailable_reason = runtime_state.get("unavailable_reason") or "unavailable"
            elif busy:
                unavailable_reason = "busy"
            elif not bound_client_ids:
                unavailable_reason = "no_binding"
            elif capability_id == "vision.capture" and not has_vision_source:
                unavailable_reason = "no_vision_source"
            elif parallel_blocked:
                unavailable_reason = "parallel_blocked"

        result = {
            "capability_id": capability_id,
            "manifest_version": manifest["version"],
            "kind": manifest["kind"],
            "available": available,
            "unavailable_reason": unavailable_reason,
            "binding": {
                "status": binding_status,
                "eligible_client_count": len(bound_client_ids),
                "bound_client_ids": list(bound_client_ids),
            },
            "permissions": {
                "required": required_permissions,
                "missing": missing_permissions,
            },
            "state": {
                "paused": paused,
                "busy": busy,
                "busy_request_id": runtime_state.get("busy_request_id"),
                "busy_action_id": runtime_state.get("busy_action_id"),
                "cooldown_active": cooldown_active,
                "cooldown_until": runtime_state.get("cooldown_until"),
                "last_failure_at": runtime_state.get("last_failure_at"),
                "last_failure_summary": runtime_state.get("last_failure_summary"),
                "last_result_at": runtime_state.get("last_result_at"),
                "last_result_summary": runtime_state.get("last_result_summary"),
                "unavailable_active": unavailable_active,
                "unavailable_reason": runtime_state.get("unavailable_reason"),
                "unavailable_until": runtime_state.get("unavailable_until"),
                "parallel_blocked_by_action_id": (
                    active_ongoing_action.get("action_id")
                    if parallel_blocked and isinstance(active_ongoing_action, dict)
                    else None
                ),
            },
        }
        readiness = capability_decision_readiness_from_manifest(manifest)
        if readiness is not None:
            result["readiness"] = readiness
        if capability_id == "vision.capture":
            result["vision_sources"] = normalized_vision_sources
        return result

    def _inspection_vision_sources(self, vision_sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for source in vision_sources or []:
            if not isinstance(source, dict):
                continue
            source_id = source.get("vision_source_id")
            kind = source.get("kind")
            label = source.get("label")
            if not isinstance(source_id, str) or not source_id.strip():
                continue
            if not isinstance(kind, str) or not kind.strip():
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            normalized.append(
                {
                    "vision_source_id": source_id.strip(),
                    "kind": kind.strip(),
                    "label": self._clamp(label.strip(), limit=80),
                    "aliases": [
                        value
                        for value in source.get("aliases", [])
                        if isinstance(value, str) and value.strip()
                    ][:8],
                    "default_for": [
                        value
                        for value in source.get("default_for", [])
                        if isinstance(value, str) and value.strip()
                    ][:8],
                    "available": source.get("available") is True,
                    "required_permissions": [
                        value
                        for value in source.get("required_permissions", [])
                        if isinstance(value, str) and value.strip()
                    ],
                    "unavailable_reason": source.get("unavailable_reason"),
                }
            )
        return normalized

    def _build_capability_decision_view(
        self,
        *,
        state: dict[str, Any] | None = None,
        current_time: str | None = None,
    ) -> list[dict[str, Any]] | None:
        manifests = capability_manifests()
        bindings = self._event_stream_registry.list_capability_bindings()
        accepted_bindings = bindings["accepted"]
        rejected_bindings = bindings["rejected"]
        vision_sources = bindings.get("vision_sources", [])
        active_ongoing_action = None
        if isinstance(state, dict):
            active_ongoing_action = self._current_ongoing_action(
                state=state,
                current_time=current_time or self._now_iso(),
            )
        decision_view: list[dict[str, Any]] = []
        for capability_id, manifest in sorted(manifests.items()):
            availability = self._build_capability_availability(
                manifest=manifest,
                current_time=current_time or self._now_iso(),
                bound_client_ids=accepted_bindings.get(capability_id, []),
                rejected_bindings=rejected_bindings,
                vision_sources=vision_sources if capability_id == "vision.capture" else None,
                active_ongoing_action=active_ongoing_action,
            )
            item = {
                "id": capability_id,
                "version": manifest["version"],
                "available": availability["available"],
                "kind": manifest["kind"],
                "what_it_does": self._clamp(str(manifest.get("decision_description") or "").strip(), limit=80),
                "when_to_use": [
                    self._clamp(str(entry).strip(), limit=80)
                    for entry in manifest.get("when_to_use", [])
                    if isinstance(entry, str) and entry.strip()
                ][:3],
                "do_not_use_when": [
                    self._clamp(str(entry).strip(), limit=80)
                    for entry in manifest.get("do_not_use_when", [])
                    if isinstance(entry, str) and entry.strip()
                ][:3],
                "required_input": self._capability_required_input_summary(manifest),
                "risk_level": manifest.get("risk_level"),
                "unavailable_reason": availability["unavailable_reason"],
            }
            readiness = capability_decision_readiness_from_manifest(manifest)
            if readiness is not None:
                item["readiness"] = readiness
            if capability_id == "vision.capture":
                item["vision_sources"] = availability.get("vision_sources", [])
            decision_view.append(item)
        if not decision_view:
            return None
        return decision_view

    def _capability_required_input_summary(self, manifest: dict[str, Any]) -> str | None:
        input_schema = manifest.get("input_schema")
        if not isinstance(input_schema, dict):
            return None
        properties = input_schema.get("properties", {})
        required_names = input_schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required_names, list):
            return None
        parts: list[str] = []
        for field_name in required_names[:4]:
            if not isinstance(field_name, str) or not field_name.strip():
                continue
            property_schema = properties.get(field_name, {})
            if (
                isinstance(property_schema, dict)
                and isinstance(property_schema.get("enum"), list)
                and len(property_schema["enum"]) == 1
            ):
                parts.append(f"{field_name}={property_schema['enum'][0]}")
            else:
                parts.append(field_name)
        if not parts:
            return None
        return ", ".join(parts)
