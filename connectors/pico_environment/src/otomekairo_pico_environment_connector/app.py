from __future__ import annotations

from datetime import datetime
import json
import logging
import math
from pathlib import Path
import ssl
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request
import uuid

from .config import Config, ConfigError
from .changes import ChangeTracker
from .notifications import TemperatureNotifier
from .protocol import CHANNELS, PROTOCOL


class ProtocolError(ValueError):
    pass


class ApiError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class Api:
    def __init__(self, config: Config):
        self.config = config
        self.token = config.read_access_token()
        self.context = ssl.create_default_context()
        if not config.tls_verify:
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE

    def request(self, method: str, path: str, payload=None, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self.config.base_url + path, data=body, method=method,
                                         headers={"Authorization": "Bearer " + self.token,
                                                  "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, context=self.context,
                                        timeout=self.config.request_timeout_seconds if timeout_seconds is None else timeout_seconds) as response:
                envelope = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ApiError(f"{method} {path}: HTTP {exc.code}", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ApiError("server connection failed", retryable=True) from exc
        except ValueError as exc:
            raise ApiError("server returned invalid JSON", retryable=False) from exc
        if not isinstance(envelope, dict) or envelope.get("ok") is not True or not isinstance(envelope.get("data"), dict):
            raise ApiError("server returned invalid API envelope", retryable=False)
        return envelope["data"]

    def enabled_source(self) -> bool:
        definition = self.request("GET", "/api/config/environment/editor-state")
        sources = definition.get("sensor_sources")
        if not isinstance(sources, list):
            raise ConfigError("environment settings have no sensor_sources list")
        source = next((item for item in sources if item["sensor_source_id"] == self.config.sensor_source_id), None)
        if source is None:
            raise ConfigError("sensor_source_id is not registered")
        expected = {channel: ("measurement", "number", unit) for channel, (_, unit) in CHANNELS.items()}
        actual = {item["channel_id"]: (item["kind"], item["value_type"], item["unit"]) for item in source["channels"]}
        if source["producer_client_id"] != self.config.client_id or actual != expected:
            raise ConfigError("registered producer or sensor channels do not match the Pico protocol")
        return source["enabled"]


def decode_reply(raw: bytes, request_id: str) -> list[dict[str, Any]]:
    try:
        reply = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Pico returned invalid JSON") from exc
    if (not isinstance(reply, dict) or set(reply) != {"protocol", "request_id", "samples"}
            or reply["protocol"] != PROTOCOL or reply["request_id"] != request_id):
        raise ProtocolError("Pico response protocol or request_id mismatch")
    samples = reply["samples"]
    if not isinstance(samples, list) or len(samples) != len(CHANNELS):
        raise ProtocolError("Pico must report all four channels")
    seen = set()
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"channel_id", "status", "payload", "error_code"}:
            raise ProtocolError("invalid Pico sample fields")
        channel = sample["channel_id"]
        if not isinstance(channel, str) or channel not in CHANNELS or channel in seen:
            raise ProtocolError("unknown or duplicate Pico channel")
        seen.add(channel)
        if sample["status"] == "succeeded":
            payload = sample["payload"]
            if (not isinstance(payload, dict) or set(payload) != {"kind", "value", "unit"}
                    or payload["kind"] != "measurement" or payload["unit"] != CHANNELS[channel][1]
                    or type(payload["value"]) not in (int, float)
                    or not math.isfinite(payload["value"]) or sample["error_code"] is not None):
                raise ProtocolError("invalid Pico measurement")
        elif sample["status"] == "failed":
            if sample["payload"] is not None or sample["error_code"] not in (
                    "source_unavailable", "acquisition_failed", "invalid_device_value"):
                raise ProtocolError("invalid Pico failure report")
        else:
            raise ProtocolError("invalid Pico sample status")
    return samples


def read_device(config: Config, *, serial_factory=None) -> list[dict[str, Any]]:
    if serial_factory is None:
        from serial import Serial
        serial_factory = Serial
    request_id = str(uuid.uuid4())
    request = json.dumps({"command": "measure", "request_id": request_id}).encode("ascii") + b"\n"
    with serial_factory(config.serial_port, baudrate=115200, timeout=config.serial_timeout_seconds,
                        write_timeout=config.serial_timeout_seconds, exclusive=True) as port:
        port.reset_input_buffer()
        if port.write(request) != len(request):
            raise ProtocolError("incomplete Pico request")
        raw = port.read_until(b"\n", size=8193)
    if not raw.endswith(b"\n") or len(raw) > 8192:
        raise ProtocolError("incomplete or oversized Pico response")
    return decode_reply(raw, request_id)


def failed_samples(code: str) -> list[dict[str, Any]]:
    return [{"channel_id": channel, "status": "failed", "payload": None, "error_code": code} for channel in CHANNELS]


def poll_once(config: Config, api: Api, tracker: ChangeTracker, *, reader=read_device) -> dict[str, Any] | None:
    if not api.enabled_source():
        tracker.reset()
        return None
    try:
        samples = reader(config)
    except OSError as exc:
        logging.warning("Pico unavailable (%s); reporting all channels failed", type(exc).__name__)
        samples = failed_samples("source_unavailable")
    except ProtocolError:
        logging.warning("Pico protocol error; reporting all channels failed")
        samples = failed_samples("acquisition_failed")
    tracker.annotate(samples, now=time.monotonic())
    observed_at = datetime.now().astimezone().isoformat(timespec="microseconds")
    payload = {"client_id": config.client_id, "sensor_source_id": config.sensor_source_id,
               "samples": [{**sample, "sample_id": str(uuid.uuid4()), "occurred_at": observed_at} for sample in samples]}
    result = api.request("POST", "/api/observations/sensors", payload)
    if len(result.get("results", [])) != len(samples):
        raise ApiError("server returned incomplete sensor acceptance", retryable=False)
    logging.info("Pico sensor report accepted (%d channels)", len(samples))
    return payload


def notify_temperature(config: Config, api: Api, event: dict[str, Any]) -> None:
    try:
        # Keep the triggering measurement unchanged while later polls continue.
        with tempfile.TemporaryDirectory(prefix="otomekairo-pico-change-") as directory:
            path = Path(directory) / "temperature.json"
            path.write_text(json.dumps(event, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            result = api.request("POST", "/api/wake", {
                "client_context": {"source": "pico_environment", "source_kind": "sensor_change",
                                   "client_id": config.client_id},
                "reference": {"uri": str(path), "label": "Picoの温度変化の測定報告",
                              "reason_summary": "temperature: rapid_change", "content_hint": "text"},
            }, timeout_seconds=config.temperature_wake.request_timeout_seconds)
            if result.get("result_kind") == "internal_failure":
                logging.error("Temperature wake returned internal_failure")
            else:
                logging.info("Temperature wake completed (result_kind=%s)", result.get("result_kind"))
    except (ApiError, OSError) as exc:
        # A timed-out wake may already be executing. Do not issue the same wake twice.
        logging.error("Temperature wake failed (%s); request is not automatically repeated", type(exc).__name__)


def run(config: Config, *, once: bool = False) -> int:
    api = Api(config)
    tracker = ChangeTracker(config.change_detection)
    notifier = TemperatureNotifier(config.temperature_wake, lambda event: notify_temperature(config, api, event))
    try:
        while True:
            started = time.monotonic()
            try:
                payload = poll_once(config, api, tracker)
            except ApiError as exc:
                if once or not exc.retryable:
                    raise
                logging.warning("%s; retrying the same server on the next poll", exc)
            else:
                if once:
                    print(json.dumps(payload if payload is not None else {"status": "disabled"}, ensure_ascii=False, allow_nan=False))
                    return 0 if payload is None or all(sample["status"] == "succeeded" for sample in payload["samples"]) else 2
                notifier.observe(payload, now=time.monotonic())
            time.sleep(max(0, config.poll_interval_seconds - (time.monotonic() - started)))
    finally:
        notifier.close()
