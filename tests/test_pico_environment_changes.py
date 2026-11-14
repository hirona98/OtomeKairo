from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sys

import pytest

CONNECTOR = Path(__file__).resolve().parents[1] / "connectors" / "pico_environment"
sys.path.insert(0, str(CONNECTOR / "src"))

from otomekairo_pico_environment_connector import app, notifications
from otomekairo_pico_environment_connector.changes import ChangeTracker
from otomekairo_pico_environment_connector.config import ConfigError, load_config
from otomekairo.observations import EnvironmentRuntime, MediaBundle, ObservationSnapshot
from otomekairo.observations.context import ObservationContext
from otomekairo.service.input.wake_reference import ServiceInputWakeReferenceMixin
from otomekairo.store.file_store import FileStore


@pytest.fixture
def config():
    return load_config(CONNECTOR / "config.example.json")


def sample(value=24.0, *, channel="temperature"):
    unit = {"temperature": "degC", "humidity": "%", "illuminance": "lux", "co2": "ppm"}[channel]
    return {"channel_id": channel, "status": "succeeded", "error_code": None,
            "payload": {"kind": "measurement", "value": value, "unit": unit}}


def annotate(tracker, value, now):
    result = sample(value)
    tracker.annotate([result], now=now)
    return result["payload"]["change"]


def test_stable_then_rapid_change_uses_only_previous_sample(config):
    tracker = ChangeTracker(config.change_detection)
    assert annotate(tracker, 24.0, 0)["pattern"] == "initial"
    for now in range(10, 601, 10):
        assert annotate(tracker, 24.0, now)["pattern"] == "steady"
    change = annotate(tracker, 24.6, 610)
    assert change["pattern"] == "rapid_change"
    assert change["previous_value"] == 24.0
    assert change["delta"] == pytest.approx(0.6)
    assert change["elapsed_seconds"] == 10
    assert len(tracker.previous) == 1


@pytest.mark.parametrize("value,expected", [(24.0, "steady"), (24.05, "steady"), (24.1, "steady"), (24.3, "changing"),
                                           (24.5, "rapid_change"), (23.5, "rapid_change")])
def test_numeric_pattern_thresholds_and_direction(config, value, expected):
    tracker = ChangeTracker(config.change_detection)
    annotate(tracker, 24.0, 0)
    assert annotate(tracker, value, 10)["pattern"] == expected


def test_measurement_failure_and_long_gap_restart_comparison(config):
    tracker = ChangeTracker(config.change_detection)
    annotate(tracker, 24.0, 0)
    assert annotate(tracker, 30.0, 31)["pattern"] == "initial"
    tracker.annotate([{"channel_id": "temperature", "status": "failed", "payload": None,
                       "error_code": "acquisition_failed"}], now=40)
    assert annotate(tracker, 10.0, 50)["pattern"] == "initial"
    tracker.reset()
    assert annotate(tracker, 30.0, 60)["pattern"] == "initial"


def test_change_metadata_survives_sensor_runtime_and_reaches_llm(config, tmp_path):
    tracker = ChangeTracker(config.change_detection)
    annotate(tracker, 24.0, 0)
    reading = sample(24.6)
    tracker.annotate([reading], now=10)
    now = datetime.now().astimezone().isoformat()
    definition = {"image_max_age_seconds": 300, "places": [], "sensor_sources": [{
        "sensor_source_id": config.sensor_source_id, "display_name": "Pico", "producer_client_id": config.client_id,
        "enabled": True, "place_ref": None, "source_owner": "self", "max_age_seconds": 120,
        "channels": [{"channel_id": "temperature", "display_name": "室温", "kind": "measurement",
                      "value_type": "number", "unit": "degC"}]}]}
    runtime = EnvironmentRuntime(definition, resolve_ids=FileStore(tmp_path).resolve_observation_ids)
    runtime.submit({"client_id": config.client_id, "sensor_source_id": config.sensor_source_id,
                    "samples": [{**reading, "sample_id": "4c9db777-14a2-4376-b019-4b14f3055d14", "occurred_at": now}]},
                   received_at=now)
    observations, reports = runtime.snapshot(now)
    context = ObservationContext(ObservationSnapshot("snapshot:test", now, "memory_set:test",
                                                     observations, (), reports, MediaBundle()))
    assert context.to_prompt_payload()["surroundings"]["observations"][0]["payload"]["change"] == reading["payload"]["change"]


class ManualExecutor:
    def __init__(self, **kwargs):
        self.jobs = []

    def submit(self, send, event):
        future = Future()
        self.jobs.append((send, event, future))
        return future

    def finish(self, index):
        send, event, future = self.jobs[index]
        send(event)
        future.set_result(None)

    def shutdown(self, **kwargs):
        for index, (_, _, future) in enumerate(self.jobs):
            if not future.done():
                self.finish(index)


def report(pattern="rapid_change", delta=0.6):
    reading = sample()
    reading.update(sample_id="4c9db777-14a2-4376-b019-4b14f3055d14", occurred_at="2026-11-14T10:00:00+09:00")
    reading["payload"]["change"] = {"pattern": pattern, "previous_value": 24.0 - delta,
                                     "delta": delta, "elapsed_seconds": 10}
    return {"client_id": "pico-environment-main", "sensor_source_id": "sensor_source:pico_environment", "samples": [reading]}


def test_wake_is_bounded_deduplicated_and_keeps_triggering_value(config, monkeypatch):
    monkeypatch.setattr(notifications, "ThreadPoolExecutor", ManualExecutor)
    sent = []
    notifier = notifications.TemperatureNotifier(config.temperature_wake, sent.append)
    original = report()
    notifier.observe(original, now=0)
    assert len(notifier.executor.jobs) == 1
    original["samples"][0]["payload"]["value"] = 999
    notifier.observe(report(), now=10)  # Same rapid-rise episode.
    assert notifier.pending is None
    notifier.observe(report("steady", 0), now=20)
    notifier.observe(report(delta=-0.6), now=30)  # New episode while wake is executing.
    assert len(notifier.executor.jobs) == 1
    assert notifier.pending["sample"]["payload"]["change"]["delta"] == -0.6
    notifier.executor.finish(0)
    notifier.observe(report("steady", 0), now=119)
    assert len(notifier.executor.jobs) == 1
    notifier.observe(report("steady", 0), now=120)
    assert len(notifier.executor.jobs) == 2
    notifier.close()
    assert len(sent) == 2
    assert sent[0]["sample"]["payload"]["value"] == 24.0


def test_disabled_source_discards_pending_wake(config, monkeypatch):
    monkeypatch.setattr(notifications, "ThreadPoolExecutor", ManualExecutor)
    notifier = notifications.TemperatureNotifier(config.temperature_wake, lambda _: None)
    notifier.observe(report(), now=0)
    notifier.observe(report("steady", 0), now=10)
    notifier.observe(report(delta=-0.6), now=20)
    assert notifier.pending is not None
    notifier.observe(None, now=30)
    assert notifier.pending is None
    notifier.close()


def test_wake_can_be_disabled_without_disabling_measurements(config, monkeypatch):
    monkeypatch.setattr(notifications, "ThreadPoolExecutor", ManualExecutor)
    notifier = notifications.TemperatureNotifier(replace(config.temperature_wake, enabled=False),
                                                lambda _: pytest.fail("wake was sent"))
    notifier.observe(report(), now=0)
    assert notifier.pending is None and not notifier.executor.jobs
    notifier.close()


@pytest.mark.parametrize("pattern", ["initial", "steady", "changing"])
def test_other_patterns_and_other_channels_do_not_start_wake(config, monkeypatch, pattern):
    monkeypatch.setattr(notifications, "ThreadPoolExecutor", ManualExecutor)
    notifier = notifications.TemperatureNotifier(config.temperature_wake, lambda _: pytest.fail("wake was sent"))
    payload = report(pattern, 0)
    humidity = sample(60, channel="humidity")
    humidity["payload"]["change"] = {"pattern": "rapid_change", "previous_value": 40, "delta": 20, "elapsed_seconds": 10}
    payload["samples"].append(humidity)
    notifier.observe(payload, now=0)
    assert notifier.pending is None and not notifier.executor.jobs
    notifier.close()


def test_temperature_wake_uses_existing_reference_route_and_accepts_noop(config):
    event = {"client_id": config.client_id, "sensor_source_id": config.sensor_source_id, "sample": report()["samples"][0]}
    seen = []

    class Api:
        def request(self, method, path, payload, **kwargs):
            assert method == "POST" and path == "/api/wake"
            assert "message" not in payload and "interaction_context" not in payload
            reference = payload["reference"]
            seen.append(Path(reference["uri"]))
            resolution = ServiceInputWakeReferenceMixin()._resolve_wake_reference(
                reference_payload=reference, resolved_at="2026-11-14T10:00:01+09:00")
            assert resolution.content_kind == "text"
            assert json.loads(resolution.text) == event
            assert kwargs["timeout_seconds"] == config.temperature_wake.request_timeout_seconds
            return {"result_kind": "noop"}

    app.notify_temperature(config, Api(), event)
    assert len(seen) == 1 and not seen[0].exists()


def test_failed_wake_is_logged_once_and_reference_is_removed(config, caplog):
    paths = []

    class Api:
        def request(self, method, path, payload, **kwargs):
            paths.append(Path(payload["reference"]["uri"]))
            raise app.ApiError("private error body", retryable=True)

    app.notify_temperature(config, Api(), report())
    assert len(paths) == 1 and not paths[0].exists()
    assert "Temperature wake failed" in caplog.text and "private error body" not in caplog.text


@pytest.mark.parametrize("mutate", [
    lambda raw: raw["change_detection"]["channels"]["temperature"].update(steady_delta=0.5),
    lambda raw: raw["change_detection"]["channels"].pop("humidity"),
    lambda raw: raw["temperature_wake"].update(enabled="true"),
    lambda raw: raw["temperature_wake"].update(min_interval_seconds=0),
])
def test_change_and_wake_config_is_explicit_and_validated(tmp_path, mutate):
    raw = json.loads((CONNECTOR / "config.example.json").read_text())
    mutate(raw)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError):
        load_config(path)
