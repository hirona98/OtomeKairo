from __future__ import annotations

from copy import deepcopy
from typing import Any


class CapabilityChoiceError(ValueError):
    pass


def build_capability_choice_view(
    capability_decision_view: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    target_index = 0
    for source in capability_decision_view or []:
        if not isinstance(source, dict):
            continue
        capability_id = source.get("id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            continue
        choice: dict[str, Any] = {
            "capability_id": capability_id.strip(),
            "kind": source.get("kind"),
            "available": source.get("available") is True,
            "what_it_does": source.get("what_it_does"),
            "when_to_use": source.get("when_to_use", []),
            "do_not_use_when": source.get("do_not_use_when", []),
            "risk_level": source.get("risk_level"),
            "unavailable_reason": source.get("unavailable_reason"),
        }
        readiness = source.get("readiness")
        if isinstance(readiness, dict):
            choice["readiness"] = {
                key: readiness[key]
                for key in ("family", "world_state_type")
                if key in readiness
            }
        for key in ("fresh_world_state_policy",):
            if key in source:
                choice[key] = source[key]

        targets: list[dict[str, Any]] = []
        if capability_id == "mcp.call_tool":
            for server in source.get("mcp_servers", []):
                if not isinstance(server, dict):
                    continue
                for tool in server.get("tools", []):
                    if not isinstance(tool, dict):
                        continue
                    target_index += 1
                    targets.append(
                        {
                            "target_ref": f"t{target_index}",
                            "target_kind": "mcp_tool",
                            "available": server.get("available") is True,
                            "mcp_server_id": server.get("mcp_server_id"),
                            "tool_name": tool.get("name"),
                            "description": tool.get("description"),
                            "unavailable_reason": server.get("unavailable_reason"),
                        }
                    )
        elif capability_id in {"vision.capture", "camera.ptz"}:
            fresh_by_source = {
                item.get("vision_source_id"): item
                for item in source.get("fresh_world_state_by_vision_source", [])
                if isinstance(item, dict) and isinstance(item.get("vision_source_id"), str)
            }
            for vision_source in source.get("vision_sources", []):
                if not isinstance(vision_source, dict):
                    continue
                target_index += 1
                target = {
                    "target_ref": f"t{target_index}",
                    "target_kind": "vision_source",
                    "available": vision_source.get("available") is True,
                    "vision_source_id": vision_source.get("vision_source_id"),
                    "source_kind": vision_source.get("kind"),
                    "source_owner": vision_source.get("source_owner"),
                    "label": vision_source.get("label"),
                    "unavailable_reason": vision_source.get("unavailable_reason"),
                }
                for key in ("supported_operations", "supported_amounts"):
                    if key in vision_source:
                        target[key] = vision_source[key]
                fresh = fresh_by_source.get(vision_source.get("vision_source_id"))
                if isinstance(fresh, dict):
                    target["fresh_world_state"] = {
                        key: fresh[key]
                        for key in ("age_label", "summary_text")
                        if key in fresh
                    }
                targets.append(target)
        choice["target_required"] = bool(targets)
        if targets:
            choice["targets"] = targets
        choices.append(choice)
    return choices


def resolve_capability_choice(
    *,
    capability_decision_view: list[dict[str, Any]] | None,
    choice_payload: Any,
) -> dict[str, Any]:
    if not isinstance(choice_payload, dict) or set(choice_payload) != {"capability_id", "target_ref"}:
        raise CapabilityChoiceError("capability choice は capability_id / target_ref を持つ object です。")
    capability_id = choice_payload.get("capability_id")
    target_ref = choice_payload.get("target_ref")
    if not isinstance(capability_id, str) or not capability_id.strip():
        raise CapabilityChoiceError("capability choice.capability_id が不正です。")
    if target_ref is not None and (not isinstance(target_ref, str) or not target_ref.strip()):
        raise CapabilityChoiceError("capability choice.target_ref が不正です。")

    choices = build_capability_choice_view(capability_decision_view)
    choice = next(
        (item for item in choices if item.get("capability_id") == capability_id.strip()),
        None,
    )
    if choice is None:
        raise CapabilityChoiceError(f"未知の capability_id={capability_id.strip()} です。")
    if choice.get("available") is not True:
        raise CapabilityChoiceError(
            f"capability_id={capability_id.strip()} は現在利用できません。"
        )
    targets = choice.get("targets") if isinstance(choice.get("targets"), list) else []
    target = None
    if targets:
        if not isinstance(target_ref, str):
            raise CapabilityChoiceError(
                f"capability_id={capability_id.strip()} には target_ref が必要です。"
            )
        target = next(
            (item for item in targets if item.get("target_ref") == target_ref.strip()),
            None,
        )
        if target is None:
            raise CapabilityChoiceError(f"未知の target_ref={target_ref.strip()} です。")
        if target.get("available") is not True:
            raise CapabilityChoiceError(f"target_ref={target_ref.strip()} は現在利用できません。")
    elif target_ref is not None:
        raise CapabilityChoiceError(
            f"capability_id={capability_id.strip()} は target_ref を取りません。"
        )

    capability_id = str(choice["capability_id"])
    source_entry = next(
        (
            item
            for item in capability_decision_view or []
            if isinstance(item, dict) and item.get("id") == capability_id
        ),
        None,
    )
    if not isinstance(source_entry, dict):
        raise CapabilityChoiceError(f"capability_id={capability_id} の decision view がありません。")
    fixed_input: dict[str, Any] = {}
    selected_tool_schema: dict[str, Any] | None = None
    if isinstance(target, dict) and target.get("target_kind") == "vision_source":
        fixed_input["vision_source_id"] = target.get("vision_source_id")
    if isinstance(target, dict) and target.get("target_kind") == "mcp_tool":
        fixed_input["mcp_server_id"] = target.get("mcp_server_id")
        fixed_input["tool_name"] = target.get("tool_name")
        selected_tool_schema = _mcp_tool_schema(
            source_entry,
            mcp_server_id=str(target.get("mcp_server_id") or ""),
            tool_name=str(target.get("tool_name") or ""),
        )
    return {
        "capability_id": capability_id,
        "capability": deepcopy(source_entry),
        "choice": deepcopy(choice),
        "target": deepcopy(target),
        "fixed_input": fixed_input,
        "selected_tool_schema": deepcopy(selected_tool_schema),
    }


def capability_input_materialization_view(
    *,
    resolved_choice: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    fixed_input = dict(resolved_choice.get("fixed_input") or {})
    input_schema = _residual_input_schema(
        manifest.get("input_schema"),
        fixed_keys=set(fixed_input),
        selected_tool_schema=resolved_choice.get("selected_tool_schema"),
    )
    capability = resolved_choice["capability"]
    payload: dict[str, Any] = {
        "capability_id": resolved_choice["capability_id"],
        "what_it_does": capability.get("what_it_does"),
        "fixed_input": fixed_input,
        "input_schema": input_schema,
    }
    target = resolved_choice.get("target")
    if isinstance(target, dict):
        payload["selected_target"] = {
            key: value
            for key, value in target.items()
            if key not in {"target_ref", "available", "unavailable_reason"}
        }
    return payload


def _residual_input_schema(
    value: Any,
    *,
    fixed_keys: set[str],
    selected_tool_schema: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapabilityChoiceError("selected capability の input_schema がありません。")
    source = deepcopy(value)
    properties = source.get("properties")
    if isinstance(properties, dict):
        source["properties"] = {
            key: schema
            for key, schema in properties.items()
            if key not in fixed_keys
        }
        if isinstance(selected_tool_schema, dict) and "arguments" in source["properties"]:
            source["properties"]["arguments"] = deepcopy(selected_tool_schema)
    required = source.get("required")
    if isinstance(required, list):
        source["required"] = [key for key in required if key not in fixed_keys]
    return source


def _mcp_tool_schema(
    capability_entry: dict[str, Any],
    *,
    mcp_server_id: str,
    tool_name: str,
) -> dict[str, Any]:
    for server in capability_entry.get("mcp_servers", []):
        if not isinstance(server, dict) or server.get("mcp_server_id") != mcp_server_id:
            continue
        for tool in server.get("tools", []):
            if isinstance(tool, dict) and tool.get("name") == tool_name:
                schema = tool.get("input_schema")
                if not isinstance(schema, dict):
                    raise CapabilityChoiceError(
                        f"MCP tool input_schema がありません: server={mcp_server_id} tool={tool_name}"
                    )
                return deepcopy(schema)
    raise CapabilityChoiceError(
        f"MCP tool schema がありません: server={mcp_server_id} tool={tool_name}"
    )
