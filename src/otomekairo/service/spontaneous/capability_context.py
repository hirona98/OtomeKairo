from __future__ import annotations

from typing import Any

from otomekairo.capabilities import capability_manifests, capability_readiness_result_digest


class ServiceSpontaneousCapabilityContextMixin:
    def _build_capability_result_client_context(self, capability_response: dict[str, Any]) -> dict[str, Any]:
        client_context = capability_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}

        summary = {
            "source": "capability_result",
            "capability_id": self._capability_result_capability_id(capability_response),
            "client_id": capability_response.get("client_id"),
            "active_app": client_context.get("active_app"),
            "window_title": client_context.get("window_title"),
            "locale": client_context.get("locale"),
            "external_service_summary": client_context.get("external_service_summary"),
            "social_context_summary": client_context.get("social_context_summary"),
            "environment_summary": client_context.get("environment_summary"),
            "location_summary": client_context.get("location_summary"),
            "body_state_summary": client_context.get("body_state_summary"),
            "device_state_summary": client_context.get("device_state_summary"),
            "schedule_summary": client_context.get("schedule_summary"),
        }
        schedule_slots = self._capability_result_schedule_slots(capability_response)
        if schedule_slots is not None:
            summary["schedule_slots"] = schedule_slots
        image_count = self._capability_result_payload_image_count(capability_response)
        if image_count is not None:
            summary["image_count"] = image_count
        return summary

    def _capability_result_schedule_slots(self, capability_response: dict[str, Any]) -> list[dict[str, Any]] | None:
        raw_slots = capability_response.get("schedule_slots")
        if isinstance(raw_slots, list):
            return self._normalize_capability_result_summary_schedule_slots(raw_slots)
        client_context = capability_response.get("client_context", {})
        if not isinstance(client_context, dict):
            return None
        raw_slots = client_context.get("schedule_slots")
        if not isinstance(raw_slots, list):
            return None
        return self._normalize_capability_result_summary_schedule_slots(raw_slots)

    def _normalize_capability_result_summary_schedule_slots(self, raw_slots: list[Any]) -> list[dict[str, Any]] | None:
        normalized_slots: list[dict[str, Any]] = []
        seen_slot_keys: set[str] = set()
        for item in raw_slots:
            if not isinstance(item, dict):
                continue
            slot_key = self._client_context_text(item.get("slot_key"), limit=160)
            summary_text = self._client_context_text(item.get("summary_text"), limit=160)
            if slot_key is None or summary_text is None or slot_key in seen_slot_keys:
                continue
            seen_slot_keys.add(slot_key)
            slot_payload: dict[str, Any] = {
                "slot_key": slot_key,
                "summary_text": summary_text,
            }
            for key in ("not_before", "expires_at"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    slot_payload[key] = value.strip()
            normalized_slots.append(slot_payload)
        if not normalized_slots:
            return None
        return normalized_slots[:4]

    def _capability_result_observation_summary(self, capability_response: dict[str, Any]) -> dict[str, Any]:
        request_record = capability_response.get("request_record")
        capability_id = self._capability_result_capability_id(capability_response)
        summary = {
            "source": "capability_result",
            "capability_id": capability_id,
            "error": capability_response.get("error"),
        }
        image_count = self._capability_result_payload_image_count(capability_response)
        if image_count is not None:
            summary["image_count"] = image_count
        if capability_id == "vision.capture":
            summary["image_interpreted"] = False
        client_id = capability_response.get("client_id")
        if isinstance(client_id, str) and client_id.strip():
            summary["client_id"] = client_id.strip()
        client_context = capability_response.get("client_context", {})
        if isinstance(client_context, dict):
            for key in ("active_app", "window_title", "locale"):
                value = client_context.get(key)
                if isinstance(value, str) and value.strip():
                    summary[key] = value.strip()
        manifest = capability_manifests().get(capability_id, {})
        inspection_fields = manifest.get("inspection_fields", [])
        if isinstance(inspection_fields, list):
            request_input = request_record.get("input") if isinstance(request_record, dict) else {}
            if not isinstance(request_input, dict):
                request_input = {}
            for field in inspection_fields:
                if not isinstance(field, str) or field in summary:
                    continue
                if field == "target_client_id":
                    value = request_record.get("target_client_id") if isinstance(request_record, dict) else None
                else:
                    value = capability_response.get(field)
                    if value is None:
                        value = request_input.get(field)
                    if value is None and isinstance(request_record, dict):
                        value = request_record.get(field)
                    if value is None and isinstance(client_context, dict):
                        value = client_context.get(field)
                if isinstance(value, str):
                    normalized = value.strip()
                    if not normalized:
                        continue
                    summary[field] = normalized
                elif isinstance(value, (int, float, bool)):
                    summary[field] = value
                elif field == "schedule_slots" and isinstance(value, list):
                    normalized_slots = self._normalize_capability_result_summary_schedule_slots(value)
                    if normalized_slots is not None:
                        summary[field] = normalized_slots
        readiness_digest = capability_readiness_result_digest(capability_id, summary)
        if isinstance(readiness_digest, dict):
            summary["readiness_digest"] = readiness_digest
        return summary

    def _prepare_capability_result_context(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        capability_id: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        input_text: str,
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        hook_name = self._capability_result_context_hook_name(capability_id)
        if hook_name == "vision_capture":
            client_context, observation_summary = self._interpret_capability_result_capture(
                state=state,
                started_at=started_at,
                client_context=client_context,
                observation_summary=observation_summary,
                input_text=input_text,
                capability_response=capability_response,
            )
            input_text = self._build_capability_result_input_text(
                client_context=client_context,
                capability_response=capability_response,
            )
        elif hook_name == "camera_ptz":
            client_context, observation_summary, input_text = self._prepare_camera_ptz_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "external_status":
            client_context, observation_summary, input_text = self._prepare_external_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "schedule_status":
            client_context, observation_summary, input_text = self._prepare_schedule_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "device_status":
            client_context, observation_summary, input_text = self._prepare_device_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "body_status":
            client_context, observation_summary, input_text = self._prepare_body_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "environment_status":
            client_context, observation_summary, input_text = self._prepare_environment_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "location_status":
            client_context, observation_summary, input_text = self._prepare_location_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "social_status":
            client_context, observation_summary, input_text = self._prepare_social_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "mcp_call_tool":
            client_context, observation_summary, input_text = self._prepare_mcp_call_tool_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        return client_context, observation_summary, input_text

    def _prepare_camera_ptz_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        request_record = capability_response.get("request_record")
        request_input = request_record.get("input") if isinstance(request_record, dict) else {}
        if not isinstance(request_input, dict):
            request_input = {}
        enriched_client_context = dict(client_context)
        for key, limit in (
            ("vision_source_id", 96),
            ("source_kind", 32),
            ("source_owner", 32),
            ("source_label", 80),
        ):
            value = self._client_context_text(enriched_client_context.get(key), limit=limit)
            if value is None and isinstance(request_record, dict):
                value = self._client_context_text(request_record.get(key), limit=limit)
            if value is not None:
                enriched_client_context[key] = value
        for key in ("status", "operation", "amount"):
            value = self._client_context_text(capability_response.get(key), limit=32)
            if value is None:
                value = self._client_context_text(request_input.get(key), limit=32)
            if value is not None:
                enriched_client_context[key] = value
        enriched_observation_summary = dict(observation_summary)
        for key in (
            "vision_source_id",
            "source_kind",
            "source_owner",
            "source_label",
            "status",
            "operation",
            "amount",
        ):
            value = enriched_client_context.get(key)
            if isinstance(value, str) and value.strip():
                enriched_observation_summary[key] = value.strip()
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_external_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        status_text = self._capability_result_status_text(capability_response)
        enriched_client_context = dict(client_context)
        if status_text is not None and self._client_context_text(enriched_client_context.get("external_service_summary"), limit=160) is None:
            enriched_client_context["external_service_summary"] = status_text
        enriched_observation_summary = dict(observation_summary)
        if status_text is not None:
            enriched_observation_summary["status_text"] = status_text
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_schedule_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        schedule_summary = self._client_context_text(capability_response.get("schedule_summary"), limit=160)
        schedule_slots = self._capability_result_schedule_slots(capability_response)
        enriched_client_context = dict(client_context)
        if schedule_summary is not None:
            enriched_client_context["schedule_summary"] = schedule_summary
        if schedule_slots is not None:
            enriched_client_context["schedule_slots"] = schedule_slots
        enriched_observation_summary = dict(observation_summary)
        if schedule_summary is not None:
            enriched_observation_summary["schedule_summary"] = schedule_summary
        if schedule_slots is not None:
            enriched_observation_summary["schedule_slots"] = schedule_slots
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_device_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        device_state_summary = self._client_context_text(capability_response.get("device_state_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if device_state_summary is not None:
            enriched_client_context["device_state_summary"] = device_state_summary
        enriched_observation_summary = dict(observation_summary)
        if device_state_summary is not None:
            enriched_observation_summary["device_state_summary"] = device_state_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_body_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        body_state_summary = self._client_context_text(capability_response.get("body_state_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if body_state_summary is not None:
            enriched_client_context["body_state_summary"] = body_state_summary
        enriched_observation_summary = dict(observation_summary)
        if body_state_summary is not None:
            enriched_observation_summary["body_state_summary"] = body_state_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_environment_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        environment_summary = self._client_context_text(capability_response.get("environment_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if environment_summary is not None:
            enriched_client_context["environment_summary"] = environment_summary
        enriched_observation_summary = dict(observation_summary)
        if environment_summary is not None:
            enriched_observation_summary["environment_summary"] = environment_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_location_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        location_summary = self._client_context_text(capability_response.get("location_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if location_summary is not None:
            enriched_client_context["location_summary"] = location_summary
        enriched_observation_summary = dict(observation_summary)
        if location_summary is not None:
            enriched_observation_summary["location_summary"] = location_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_social_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        social_context_summary = self._client_context_text(capability_response.get("social_context_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if social_context_summary is not None:
            enriched_client_context["social_context_summary"] = social_context_summary
        enriched_observation_summary = dict(observation_summary)
        if social_context_summary is not None:
            enriched_observation_summary["social_context_summary"] = social_context_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_mcp_call_tool_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        enriched_client_context = dict(client_context)
        for key in ("mcp_server_id", "tool_name", "status"):
            value = self._client_context_text(capability_response.get(key), limit=120)
            if value is not None:
                enriched_client_context[key] = value
        summary = self._mcp_result_summary(capability_response)
        if summary is not None:
            enriched_client_context["mcp_result_summary"] = summary
        enriched_observation_summary = dict(observation_summary)
        for key in ("mcp_server_id", "tool_name", "status", "mcp_result_summary"):
            value = enriched_client_context.get(key)
            if isinstance(value, str) and value.strip():
                enriched_observation_summary[key] = value.strip()
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _capability_result_followup_hint_summary(
        self,
        *,
        capability_id: str,
        observation_summary: dict[str, Any] | None,
        result_payload: dict[str, Any] | None,
    ) -> str | None:
        hook_name = self._capability_followup_hint_hook_name(capability_id)
        if hook_name == "vision_capture":
            visual_summary_text = None
            if isinstance(observation_summary, dict):
                visual_summary_text = observation_summary.get("visual_summary_text")
            if isinstance(visual_summary_text, str) and visual_summary_text.strip():
                return f"視覚観測では {visual_summary_text.strip()}"
            image_count = self._capability_result_payload_image_count(result_payload or {})
            if image_count is not None and image_count <= 0:
                return "視覚観測は空で、追加の手掛かりを得られなかった。"
            return None
        if hook_name == "camera_ptz":
            if not isinstance(observation_summary, dict):
                return None
            status = observation_summary.get("status")
            operation = observation_summary.get("operation")
            amount = observation_summary.get("amount")
            if isinstance(status, str) and isinstance(operation, str) and isinstance(amount, str):
                return f"カメラ制御結果: status={status.strip()} operation={operation.strip()} amount={amount.strip()}"
            return None
        if hook_name == "external_status":
            status_text = None
            service = None
            if isinstance(observation_summary, dict):
                status_text = observation_summary.get("status_text")
                service = observation_summary.get("service")
            if isinstance(status_text, str) and status_text.strip():
                if isinstance(service, str) and service.strip():
                    return f"{service.strip()} の状態要約: {status_text.strip()}"
                return status_text.strip()
            return None
        if hook_name == "schedule_status":
            schedule_summary = None
            slot_count = None
            if isinstance(observation_summary, dict):
                schedule_summary = observation_summary.get("schedule_summary")
                schedule_slots = observation_summary.get("schedule_slots")
                if isinstance(schedule_slots, list):
                    slot_count = len(schedule_slots)
            if isinstance(schedule_summary, str) and schedule_summary.strip():
                return f"予定要約: {schedule_summary.strip()}"
            if isinstance(slot_count, int) and slot_count > 0:
                return f"近い予定が {slot_count} 件ある。"
            return None
        if hook_name == "device_status":
            device_state_summary = None
            if isinstance(observation_summary, dict):
                device_state_summary = observation_summary.get("device_state_summary")
            if isinstance(device_state_summary, str) and device_state_summary.strip():
                return f"端末状態: {device_state_summary.strip()}"
            return None
        if hook_name == "body_status":
            body_state_summary = None
            if isinstance(observation_summary, dict):
                body_state_summary = observation_summary.get("body_state_summary")
            if isinstance(body_state_summary, str) and body_state_summary.strip():
                return f"身体状態: {body_state_summary.strip()}"
            return None
        if hook_name == "environment_status":
            environment_summary = None
            if isinstance(observation_summary, dict):
                environment_summary = observation_summary.get("environment_summary")
            if isinstance(environment_summary, str) and environment_summary.strip():
                return f"環境状態: {environment_summary.strip()}"
            return None
        if hook_name == "location_status":
            location_summary = None
            if isinstance(observation_summary, dict):
                location_summary = observation_summary.get("location_summary")
            if isinstance(location_summary, str) and location_summary.strip():
                return f"場所状態: {location_summary.strip()}"
            return None
        if hook_name == "social_status":
            social_context_summary = None
            if isinstance(observation_summary, dict):
                social_context_summary = observation_summary.get("social_context_summary")
            if isinstance(social_context_summary, str) and social_context_summary.strip():
                return f"対人文脈: {social_context_summary.strip()}"
            return None
        if hook_name == "mcp_call_tool":
            if not isinstance(observation_summary, dict):
                return None
            tool_name = observation_summary.get("tool_name")
            summary = observation_summary.get("mcp_result_summary")
            if isinstance(tool_name, str) and isinstance(summary, str) and summary.strip():
                return f"MCP tool {tool_name.strip()} の結果: {summary.strip()}"
            return None
        return None

    def _interpret_capability_result_capture(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        input_text: str,
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        images = self._normalize_visual_observation_images(
            capability_response.get("images", []),
            allow_missing=True,
        )
        return self._interpret_visual_observation(
            state=state,
            started_at=started_at,
            trigger_kind="capability_result",
            client_context=client_context,
            observation_summary=observation_summary,
            input_text=input_text,
            images=images,
        )

    def _build_capability_result_input_text(
        self,
        *,
        client_context: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> str:
        parts = ["capability result を受信。"]
        capability_id = self._capability_result_capability_id(capability_response)
        source_label = self._client_context_text(client_context.get("source_label"), limit=80)
        source_kind = self._client_context_text(client_context.get("source_kind"), limit=32)
        vision_source_id = self._client_context_text(client_context.get("vision_source_id"), limit=96)
        image_count = self._capability_result_payload_image_count(capability_response)
        parts.append(f"{capability_id} の非同期結果を受け取った。")
        if source_label is not None:
            parts.append(f"観測 source は {source_label}。")
        elif vision_source_id is not None:
            parts.append(f"観測 source id は {vision_source_id}。")
        if source_kind is not None:
            parts.append(f"source kind は {source_kind}。")
        error = capability_response.get("error")
        if isinstance(error, str) and error.strip():
            parts.append(f"結果は error だった。 error={error.strip()}")
        status_text = self._capability_result_status_text(capability_response)
        if status_text is not None:
            parts.append(f"結果要約は {status_text}")
        elif capability_id == "camera.ptz":
            status = self._client_context_text(capability_response.get("status"), limit=32)
            operation = self._client_context_text(capability_response.get("operation"), limit=32)
            amount = self._client_context_text(capability_response.get("amount"), limit=32)
            if status is not None and operation is not None and amount is not None:
                parts.append(f"カメラ制御結果は status={status} operation={operation} amount={amount}。")
            else:
                parts.append("カメラ制御結果を受け取った。")
            if vision_source_id is not None:
                parts.append(f"必要なら同じ vision_source_id={vision_source_id} を vision.capture で見て確認したい。")
        elif capability_id == "schedule.status":
            schedule_summary = self._client_context_text(capability_response.get("schedule_summary"), limit=160)
            if schedule_summary is not None:
                parts.append(f"予定要約は {schedule_summary}")
            else:
                parts.append("予定確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "device.status":
            device_state_summary = self._client_context_text(capability_response.get("device_state_summary"), limit=160)
            if device_state_summary is not None:
                parts.append(f"端末状態要約は {device_state_summary}")
            else:
                parts.append("端末状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "body.status":
            body_state_summary = self._client_context_text(capability_response.get("body_state_summary"), limit=160)
            if body_state_summary is not None:
                parts.append(f"身体状態要約は {body_state_summary}")
            else:
                parts.append("身体状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "environment.status":
            environment_summary = self._client_context_text(capability_response.get("environment_summary"), limit=160)
            if environment_summary is not None:
                parts.append(f"環境状態要約は {environment_summary}")
            else:
                parts.append("環境状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "location.status":
            location_summary = self._client_context_text(capability_response.get("location_summary"), limit=160)
            if location_summary is not None:
                parts.append(f"場所状態要約は {location_summary}")
            else:
                parts.append("場所状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "social.status":
            social_context_summary = self._client_context_text(
                capability_response.get("social_context_summary"),
                limit=160,
            )
            if social_context_summary is not None:
                parts.append(f"対人文脈要約は {social_context_summary}")
            else:
                parts.append("対人文脈確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "mcp.call_tool":
            mcp_server_id = self._client_context_text(capability_response.get("mcp_server_id"), limit=80)
            tool_name = self._client_context_text(capability_response.get("tool_name"), limit=120)
            mcp_status = self._client_context_text(capability_response.get("status"), limit=32)
            summary = self._client_context_text(client_context.get("mcp_result_summary"), limit=300)
            if mcp_server_id is not None and tool_name is not None:
                parts.append(f"MCP tool は {mcp_server_id}/{tool_name}。")
            if mcp_status is not None:
                parts.append(f"MCP 実行 status は {mcp_status}。")
            if summary is not None:
                parts.append(f"MCP 結果要約は {summary}")
            else:
                parts.append("MCP tool の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "vision.capture" and image_count is not None and image_count <= 0:
            parts.append("観測結果は空だった。")
        else:
            parts.append("受け取った結果を踏まえて返答や次の行動を決めたい。")
        return " ".join(parts)

    def _mcp_result_summary(self, capability_response: dict[str, Any]) -> str | None:
        client_context = capability_response.get("client_context")
        if isinstance(client_context, dict):
            value = self._client_context_text(client_context.get("mcp_result_summary"), limit=300)
            if value is not None:
                return value
        error = capability_response.get("error")
        if isinstance(error, str) and error.strip():
            return f"error={error.strip()}"
        content = capability_response.get("content")
        if not isinstance(content, list):
            return None
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if text_parts:
            return " ".join(text_parts)
        if content:
            return f"{len(content)} 件の MCP content を受け取った。"
        return None

    def _capability_result_active_step_summary(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        error = result_payload.get("error")
        if isinstance(error, str) and error.strip():
            return f"{capability_id} の error 結果を受け、次の1手を判断中。"
        return f"{capability_id} の結果を受け、次の1手を判断中。"

    def _capability_result_followup_terminal_reason(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> str:
        has_error = result_payload.get("error") not in {None, ""}
        reason_code = self._capability_result_followup_reason_code(
            decision=decision,
            result_payload=result_payload,
        )
        if reason_code in {"followup_speech", "followup_noop"}:
            return self._capability_terminal_transition_reason_summary(
                reason_code=reason_code,
                result_error=has_error,
            )
        return self._capability_result_terminal_reason(
            capability_id=capability_id,
            result_payload=result_payload,
        )

    def _capability_result_followup_reason_code(
        self,
        *,
        decision: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> str:
        decision_kind = str(decision.get("kind") or "").strip()
        if decision_kind == "speech":
            return "followup_speech"
        if decision_kind == "noop":
            return "followup_noop"
        if result_payload.get("error") not in {None, ""}:
            return "result_error"
        return "result_received"

    def _capability_result_followup_detail_summary(
        self,
        *,
        capability_id: str,
        decision: dict[str, Any],
        observation_summary: dict[str, Any] | None = None,
        result_payload: dict[str, Any] | None = None,
    ) -> str | None:
        decision_reason = str(decision.get("reason_summary") or "").strip()
        if decision_reason:
            return decision_reason
        hook_summary = self._capability_result_followup_hint_summary(
            capability_id=capability_id,
            observation_summary=observation_summary,
            result_payload=result_payload,
        )
        if hook_summary is not None:
            return hook_summary
        if isinstance(result_payload, dict):
            error = result_payload.get("error")
            if isinstance(error, str) and error.strip():
                return error.strip()
        return None

    def _apply_capability_runtime_state_followup(
        self,
        *,
        capability_id: str,
        current_time: str,
        observation_summary: dict[str, Any] | None,
        result_payload: dict[str, Any],
        ongoing_action_transition_summary: dict[str, Any] | None,
        failure_reason: str | None = None,
    ) -> None:
        if not isinstance(ongoing_action_transition_summary, dict):
            return
        final_state = str(ongoing_action_transition_summary.get("final_state") or "").strip()
        if final_state == "waiting_result":
            return
        hook_summary = self._capability_result_followup_hint_summary(
            capability_id=capability_id,
            observation_summary=observation_summary,
            result_payload=result_payload,
        )
        summary_text = hook_summary or str(ongoing_action_transition_summary.get("reason_summary") or "").strip() or failure_reason or capability_id
        if ongoing_action_transition_summary.get("result_error") is True or final_state == "interrupted":
            self._mark_capability_runtime_failure(
                capability_id=capability_id,
                current_time=current_time,
                failure_summary=summary_text,
            )
            return
        if final_state in {"completed", "on_hold"}:
            self._mark_capability_runtime_success(
                capability_id=capability_id,
                current_time=current_time,
                result_summary=summary_text,
            )

    def _capability_result_followup_terminal_step_summary(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> str:
        decision_kind = str(decision.get("kind") or "").strip()
        has_error = result_payload.get("error") not in {None, ""}
        if decision_kind == "speech":
            if has_error:
                return f"{capability_id} の error を受けて speech した。"
            return f"{capability_id} の結果を受けて speech した。"
        if decision_kind == "noop":
            if has_error:
                return f"{capability_id} の error を受けて継続を中断した。"
            return f"{capability_id} の結果を受けて継続を完了した。"
        return self._capability_result_terminal_step_summary(
            capability_id=capability_id,
            result_payload=result_payload,
        )
