from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

import pytest

CONNECTOR = Path(__file__).resolve().parents[1] / "connectors" / "pico_environment"
sys.path.insert(0, str(CONNECTOR / "src"))

from otomekairo_pico_environment_connector import app, protocol
from otomekairo_pico_environment_connector.config import ConfigError, load_config
from otomekairo.observations import EnvironmentRuntime, MediaBundle, ObservationSnapshot
from otomekairo.observations.context import ObservationContext
from otomekairo.store.file_store import FileStore

spec = importlib.util.spec_from_file_location("pico_sensors", CONNECTOR / "firmware" / "sensors.py")
sensors = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"protocol": protocol}):
    spec.loader.exec_module(sensors)


@pytest.fixture
def config(tmp_path):
    return replace(load_config(CONNECTOR / "config.example.json"),
                   config_db_path=tmp_path / "config.db", serial_port="/dev/serial/by-id/test-pico")


def readings():
    values = {"temperature": 24.5, "humidity": 45.0, "illuminance": 0, "co2": 600}
    return [{"channel_id": channel, "status": "succeeded", "error_code": None,
             "payload": {"kind": "measurement", "value": values[channel], "unit": unit}}
            for channel, (_, unit) in protocol.CHANNELS.items()]


def reply(samples=None, *, request_id="request"):
    return {"protocol": protocol.PROTOCOL, "request_id": request_id,
            "samples": readings() if samples is None else samples}


def test_zero_light_and_partial_sensor_failure_are_preserved():
    payload = reply()
    payload["samples"][3] = {"channel_id": "co2", "status": "failed", "payload": None,
                             "error_code": "acquisition_failed"}
    result = app.decode_reply(json.dumps(payload).encode(), "request")
    assert result[2]["payload"]["value"] == 0
    assert result[3]["payload"] is None


@pytest.mark.parametrize("change", [
    lambda p: p.update(request_id="old-request"),
    lambda p: p.update(protocol="other-protocol"),
    lambda p: p["samples"].pop(),
    lambda p: p["samples"].__setitem__(1, deepcopy(p["samples"][0])),
    lambda p: p["samples"][0]["payload"].update(unit="K"),
    lambda p: p["samples"][0].update(error_code="acquisition_failed"),
    lambda p: p["samples"][0].update(status="failed"),
    lambda p: p["samples"][0].update(channel_id=[]),
])
def test_usb_protocol_rejects_replayed_or_inconsistent_measurements(change):
    payload = reply()
    change(payload)
    with pytest.raises(app.ProtocolError):
        app.decode_reply(json.dumps(payload).encode(), "request")


@pytest.mark.parametrize("value", [True, None, "24.5", float("nan"), float("inf")])
def test_usb_protocol_rejects_invalid_numeric_values(value):
    payload = reply()
    payload["samples"][0]["payload"]["value"] = value
    with pytest.raises(app.ProtocolError):
        app.decode_reply(json.dumps(payload).encode(), "request")


@pytest.mark.parametrize("ending", [b"\n", b""])
def test_serial_request_identity_and_complete_line_are_required(config, ending):
    class Port:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def reset_input_buffer(self):
            pass

        def write(self, raw):
            self.request = json.loads(raw)
            assert self.request["command"] == "measure"
            return len(raw)

        def read_until(self, expected, size):
            assert expected == b"\n" and size == 8193
            return json.dumps(reply(request_id=self.request["request_id"])).encode() + ending

    factory = lambda *args, **kwargs: Port()
    if ending:
        assert len(app.read_device(config, serial_factory=factory)) == 4
    else:
        with pytest.raises(app.ProtocolError):
            app.read_device(config, serial_factory=factory)


def test_missing_token_db_is_not_created_or_replaced_with_an_empty_token(config):
    with pytest.raises(ConfigError):
        config.read_access_token()
    assert not config.config_db_path.exists()


def test_config_reads_token_without_containing_it_in_repr(config):
    with sqlite3.connect(config.config_db_path) as connection:
        connection.execute("CREATE TABLE server_identity (id INTEGER, console_access_token TEXT)")
        connection.execute("INSERT INTO server_identity VALUES (1, 'test-token')")
    assert config.read_access_token() == "test-token"
    assert "test-token" not in repr(config)


@pytest.mark.parametrize("field,value", [("poll_interval_seconds", 0), ("serial_timeout_seconds", True),
                                        ("serial_timeout_seconds", float("nan")), ("serial_port", "auto")])
def test_config_rejects_implicit_device_selection_and_invalid_intervals(tmp_path, field, value):
    raw = json.loads((CONNECTOR / "config.example.json").read_text())
    raw["connector"][field] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError):
        load_config(path)


def test_sensor_report_reaches_llm_surroundings_and_usb_failure_invalidates_it(config, tmp_path):
    source = {"sensor_source_id": config.sensor_source_id, "display_name": "Pico環境センサー",
              "producer_client_id": config.client_id, "enabled": True, "place_ref": None,
              "source_owner": "self", "max_age_seconds": 120,
              "channels": [{"channel_id": channel, "display_name": label, "kind": "measurement",
                            "value_type": "number", "unit": unit}
                           for channel, (label, unit) in protocol.CHANNELS.items()]}
    store = FileStore(tmp_path / "store")
    runtime = EnvironmentRuntime({"image_max_age_seconds": 300, "places": [], "sensor_sources": [source]},
                                 resolve_ids=store.resolve_observation_ids)

    class Api:
        def enabled_source(self):
            return True

        def request(self, method, path, payload):
            assert method == "POST" and path == "/api/observations/sensors"
            return runtime.submit(payload, received_at=datetime.now().astimezone().isoformat())

    result = app.poll_once(config, Api(), reader=lambda _: readings())
    assert len({item["sample_id"] for item in result["samples"]}) == 4
    now = datetime.now().astimezone().isoformat()
    observations, reports = runtime.snapshot(now)
    context = ObservationContext(ObservationSnapshot("snapshot:test", now, "memory_set:test",
                                                      observations, (), reports, MediaBundle()))
    prompted = context.to_prompt_payload()["surroundings"]["observations"]
    assert {item["channel_id"]: item["payload"]["value"] for item in prompted} == {
        "temperature": 24.5, "humidity": 45.0, "illuminance": 0, "co2": 600}
    assert runtime.snapshot((datetime.fromisoformat(now) + timedelta(seconds=121)).isoformat())[0] == ()

    def disconnected(_):
        raise OSError("USB disconnected")

    app.poll_once(config, Api(), reader=disconnected)
    observations, reports = runtime.snapshot(datetime.now().astimezone().isoformat())
    assert observations == ()
    assert len(reports) == 4
    assert all(report.status == "failed" and report.error_code == "source_unavailable" for report in reports)


def test_disabled_source_does_not_read_usb(config):
    class Api:
        def enabled_source(self):
            return False

    assert app.poll_once(config, Api(), reader=lambda _: pytest.fail("USB was opened")) is None


def test_sensor_crc_known_vector_and_corruption():
    assert sensors.sensirion_words(b"\xbe\xef\x92") == [0xBEEF]
    with pytest.raises(ValueError):
        sensors.sensirion_words(b"\xbe\xef\x93")


def test_firmware_crc_failure_affects_only_the_device_channels():
    class SensorSet(sensors.Sensors):
        def sht41(self):
            return sensors.sensirion_words(b"\xbe\xef\x93")

        def veml7700(self):
            return (0,)

        def scd4x(self):
            return (600,)

    result = SensorSet(None).measure()
    assert [item["status"] for item in result] == ["failed", "failed", "succeeded", "succeeded"]
    assert result[0]["error_code"] == result[1]["error_code"] == "invalid_device_value"
