from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from queue import Queue
import threading
from unittest.mock import patch

import pytest

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.capability import CapabilityDispatchError


START = "2026-11-02T12:00:00+09:00"
CAPTURED = "2026-11-02T12:00:01.1234567+09:00"
RECEIVED = "2026-11-02T12:00:02+09:00"
IMAGE = "data:image/png;base64,YWJj"


class CaptureSocket:
    def __init__(self):
        self.requests = Queue()

    def send_json(self, payload):
        self.requests.put(payload)

    def close(self):
        pass


@pytest.fixture
def transport(tmp_path):
    service = OtomeKairoService(tmp_path)
    token = service.acquire_console_access_token()["console_access_token"]
    state = service.store.read_state()
    state["camera_sources"] = {"vision_source:camera": {
        "vision_source_id": "vision_source:camera", "enabled": True,
    }}
    service.store.write_state(state)
    sources = {}
    for kind in ("desktop", "camera"):
        socket = CaptureSocket()
        session = service._event_stream_registry.add_connection(socket)
        service._event_stream_registry.register_hello(
            session, client_id=kind, client_kind="capability_connector",
            capabilities={"vision.capture": "1"}, rejected_bindings=[],
            vision_sources=[{"vision_source_id": f"vision_source:{kind}",
                             "capability_id": "vision.capture", "kind": kind, "label": kind}],
        )
        sources[kind] = socket
    service._now_iso = lambda: RECEIVED
    server = OtomeKairoHttpServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield service, token, server, sources
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def dispatch(service, kind, *, wait=True):
    return service._dispatch_capability_request(
        memory_set_id=service.store.read_state()["selected_memory_set_id"],
        capability_id="vision.capture",
        input_payload={"vision_source_id": f"vision_source:{kind}", "mode": "still"},
        current_time=START, goal_summary="取得経路の検証", wait_for_response=wait,
        component="Test", track_ongoing_action=False,
    )


def post_result(server, token, event, kind, result):
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request("POST", "/api/capability/result", json.dumps({
            "request_id": event["data"]["request_id"], "capability_id": "vision.capture",
            "client_id": kind, "result": {
                **result, "client_context": {"vision_source_id": f"vision_source:{kind}",
                                             "source_kind": kind, "source_label": kind},
            },
        }), {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@pytest.mark.parametrize("kind", ["desktop", "camera"])
@pytest.mark.parametrize("result", [
    {"images": [IMAGE], "captured_at": CAPTURED},
    {"images": [], "captured_at": None, "error": "capture skipped (idle)"},
    {"images": [], "captured_at": None, "error": "capture failed"},
])
def test_capture_result_http_response_releases_waiting_request(transport, kind, result):
    service, token, server, sources = transport
    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(dispatch, service, kind)
        event = sources[kind].requests.get(timeout=5)
        status, body = post_result(server, token, event, kind, result)
        assert status == 200, body
        accepted = waiting.result(timeout=5)
    assert accepted["images"] == result["images"]
    assert accepted["captured_at"] == ("2026-11-02T12:00:01.123456+09:00" if result["images"] else None)
    assert not service._capability_runtime_state_entry("vision.capture")["busy_request_id"]


@pytest.mark.parametrize("invalid", [
    {"images": []},
    {"images": [IMAGE], "captured_at": None},
    {"images": [IMAGE], "captured_at": "2026-11-02T11:59:59+09:00"},
    {"images": [IMAGE], "captured_at": "2026-11-02T12:00:03+09:00"},
    {"images": [], "captured_at": CAPTURED},
])
def test_invalid_capture_time_returns_400_and_request_accepts_corrected_result(transport, invalid):
    service, token, server, sources = transport
    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(dispatch, service, "desktop")
        event = sources["desktop"].requests.get(timeout=5)
        try:
            status, body = post_result(server, token, event, "desktop", invalid)
            assert status == 400, body
            assert body["error"]["code"] == "invalid_capture_time"
            assert not waiting.done()
        finally:
            post_result(server, token, event, "desktop", {"images": [IMAGE], "captured_at": CAPTURED})
        assert waiting.result(timeout=5)["images"] == [IMAGE]


@pytest.mark.parametrize("failure", ["dispatch", "timeout", "expired"])
def test_desktop_failure_does_not_block_camera_and_cooldown_expires(transport, failure):
    service, token, server, sources = transport
    with patch.object(service, "_now_iso", return_value=START):
        if failure == "dispatch":
            with patch.object(service._event_stream_registry, "send_to_client", return_value=False):
                with pytest.raises(CapabilityDispatchError):
                    dispatch(service, "desktop", wait=False)
        elif failure == "timeout":
            with patch.object(threading.Event, "wait", return_value=False):
                with pytest.raises(CapabilityDispatchError):
                    dispatch(service, "desktop")
        else:
            record = dispatch(service, "desktop", wait=False)["request_record"]
            record["expires_at"] = START
            service._prune_pending_capability_requests(current_time=START)
        inspection = service.get_capability_inspection(token)
    capability = next(c for c in inspection["capabilities"] if c["capability_id"] == "vision.capture")
    by_source = {s["kind"]: s for s in capability["vision_sources"]}
    assert capability["available"] is True
    assert by_source["desktop"]["available"] is False
    assert by_source["desktop"]["unavailable_reason"] == ("dispatch_failed" if failure == "dispatch" else "request_timeout")
    assert by_source["camera"]["available"] is True
    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(dispatch, service, "camera")
        event = sources["camera"].requests.get(timeout=5)
        status, body = post_result(server, token, event, "camera", {"images": [IMAGE], "captured_at": CAPTURED})
        assert status == 200, body
        assert waiting.result(timeout=5)["images"] == [IMAGE]
    assert service._vision_source_runtime_unavailable_reason(
        vision_source_id="vision_source:desktop", current_time="2026-11-02T12:00:31+09:00",
    ) is None
