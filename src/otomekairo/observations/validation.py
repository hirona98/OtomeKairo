from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.observations.models import MAX_SENSOR_OBSERVATIONS, ObservationError


def exact_fields(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ObservationError(code, "Object fields do not match the contract.")
    return value


def nonempty(value: Any, code: str, prefix: str = "") -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ObservationError(code, "A non-empty, trimmed string is required.")
    if prefix and (not value.startswith(prefix) or value == prefix):
        raise ObservationError(code, "Reference prefix is invalid.")
    return value


def positive_integer(value: Any, code: str) -> None:
    if type(value) is not int or value <= 0:
        raise ObservationError(code, "A positive integer is required.")


def validate_environment_config(value: Any, *, camera_sources: dict[str, Any]) -> dict[str, Any]:
    code = "invalid_environment_config"
    config = exact_fields(value, {"image_max_age_seconds", "places", "sensor_sources"}, code)
    positive_integer(config["image_max_age_seconds"], code)
    if not isinstance(config["places"], list) or not isinstance(config["sensor_sources"], list):
        raise ObservationError(code, "Places and sensor sources must be arrays.")
    places: set[str] = set()
    for raw in config["places"]:
        place = exact_fields(raw, {"place_ref", "display_name"}, code)
        ref = nonempty(place["place_ref"], code, "place:")
        nonempty(place["display_name"], code)
        if ref in places:
            raise ObservationError(code, "Place references must be unique.")
        places.add(ref)
    sources: set[str] = set()
    enabled_channels = 0
    for raw in config["sensor_sources"]:
        source = exact_fields(raw, {"sensor_source_id", "display_name", "producer_client_id", "enabled",
                                    "place_ref", "source_owner", "max_age_seconds", "channels"}, code)
        ref = nonempty(source["sensor_source_id"], code, "sensor_source:")
        if ref in sources:
            raise ObservationError(code, "Sensor source IDs must be unique.")
        sources.add(ref)
        nonempty(source["display_name"], code)
        nonempty(source["producer_client_id"], code)
        if type(source["enabled"]) is not bool or not isinstance(source["source_owner"], str) or source["source_owner"] not in {"self", "user_environment"}:
            raise ObservationError(code, "Enabled or source owner is invalid.")
        if source["place_ref"] is not None:
            if nonempty(source["place_ref"], code, "place:") not in places:
                raise ObservationError(code, "Sensor place does not exist.")
        positive_integer(source["max_age_seconds"], code)
        if not isinstance(source["channels"], list) or not source["channels"]:
            raise ObservationError(code, "Sensor channels must be a non-empty array.")
        channels: set[str] = set()
        for raw_channel in source["channels"]:
            channel = exact_fields(raw_channel, {"channel_id", "display_name", "kind", "value_type", "unit"}, code)
            channel_id = nonempty(channel["channel_id"], code)
            nonempty(channel["display_name"], code)
            if channel_id in channels:
                raise ObservationError(code, "Channel IDs must be unique within a source.")
            channels.add(channel_id)
            if channel["kind"] == "measurement":
                if channel["value_type"] == "number":
                    nonempty(channel["unit"], code)
                elif channel["value_type"] != "boolean" or channel["unit"] is not None:
                    raise ObservationError(code, "Measurement type and unit do not match.")
            elif channel["kind"] != "event" or channel["value_type"] is not None or channel["unit"] is not None:
                raise ObservationError(code, "Event channel contract is invalid.")
        if source["enabled"]:
            enabled_channels += len(channels)
    if enabled_channels > MAX_SENSOR_OBSERVATIONS:
        raise ObservationError(code, "Enabled sensor channels exceed the snapshot limit.")
    for camera in camera_sources.values():
        if camera["place_ref"] is not None and camera["place_ref"] not in places:
            raise ObservationError("environment_place_in_use", "A camera references a removed place.", 409)
    return deepcopy(config)
