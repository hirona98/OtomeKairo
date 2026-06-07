from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from typing import Any, Callable
from urllib.parse import urlparse


WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class StreamError(RuntimeError):
    pass


class EventStreamClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        tls_verify: bool,
        socket_timeout_seconds: float,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise StreamError("base_url must use http or https.")
        if not parsed.hostname:
            raise StreamError("base_url must include host.")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.base_path = parsed.path.rstrip("/")
        self.access_token = access_token
        self.socket_timeout_seconds = socket_timeout_seconds
        self.ssl_context = ssl.create_default_context()
        if not tls_verify:
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
        self._socket: socket.socket | ssl.SSLSocket | None = None

    def run(self, *, hello_payload: dict[str, Any], on_event: Callable[[dict[str, Any]], None]) -> None:
        self._connect()
        try:
            self._send_json(hello_payload)
            while True:
                opcode, payload = self._read_frame()
                if opcode == 0x8:
                    raise StreamError("event stream closed by server.")
                if opcode == 0x9:
                    self._send_frame(opcode=0xA, payload=payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode != 0x1:
                    raise StreamError(f"unsupported websocket opcode: {opcode}")
                event = json.loads(payload.decode("utf-8"))
                if not isinstance(event, dict):
                    raise StreamError("event stream payload must be a JSON object.")
                on_event(event)
        finally:
            self.close()

    def close(self) -> None:
        websocket = self._socket
        if websocket is None:
            return
        try:
            self._send_frame(opcode=0x8, payload=b"")
        except OSError:
            pass
        try:
            websocket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        websocket.close()
        self._socket = None

    def _connect(self) -> None:
        raw_socket = socket.create_connection((self.host, self.port), timeout=self.socket_timeout_seconds)
        if self.scheme == "https":
            websocket: socket.socket | ssl.SSLSocket = self.ssl_context.wrap_socket(raw_socket, server_hostname=self.host)
        else:
            websocket = raw_socket
        websocket.settimeout(1.0)
        self._socket = websocket

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = f"{self.base_path}/api/events/stream" if self.base_path else "/api/events/stream"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Authorization: Bearer {self.access_token}\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        websocket.sendall(request.encode("ascii"))
        status_code, headers = self._read_http_response()
        if status_code != 101:
            raise StreamError(f"event stream handshake failed with HTTP {status_code}.")
        expected_accept = base64.b64encode(
            hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode("utf-8")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise StreamError("event stream handshake returned an invalid Sec-WebSocket-Accept.")

    def _read_http_response(self) -> tuple[int, dict[str, str]]:
        websocket = self._require_socket()
        chunks = bytearray()
        while b"\r\n\r\n" not in chunks:
            try:
                chunk = websocket.recv(4096)
            except socket.timeout as exc:
                raise StreamError("event stream handshake timed out.") from exc
            if not chunk:
                raise StreamError("event stream handshake closed before headers completed.")
            chunks.extend(chunk)
        header_text = bytes(chunks).split(b"\r\n\r\n", 1)[0].decode("ascii")
        lines = header_text.split("\r\n")
        status_line = lines[0]
        try:
            status_code = int(status_line.split()[1])
        except (IndexError, ValueError) as exc:
            raise StreamError(f"invalid event stream status line: {status_line}") from exc
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        return status_code, headers

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(opcode=0x1, payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _send_frame(self, *, opcode: int, payload: bytes) -> None:
        websocket = self._require_socket()
        mask = os.urandom(4)
        masked_payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        header = bytearray([0x80 | (opcode & 0x0F)])
        payload_length = len(payload)
        if payload_length < 126:
            header.append(0x80 | payload_length)
        elif payload_length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", payload_length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", payload_length))
        header.extend(mask)
        websocket.sendall(bytes(header) + masked_payload)

    def _read_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(2)
        first_byte = header[0]
        second_byte = header[1]
        fin = (first_byte & 0x80) != 0
        opcode = first_byte & 0x0F
        masked = (second_byte & 0x80) != 0
        payload_length = second_byte & 0x7F
        if not fin:
            raise StreamError("fragmented websocket frames are not supported.")
        if masked:
            raise StreamError("server websocket frame must not be masked.")
        if payload_length == 126:
            payload_length = struct.unpack("!H", self._read_exact(2))[0]
        elif payload_length == 127:
            payload_length = struct.unpack("!Q", self._read_exact(8))[0]
        return opcode, self._read_exact(payload_length)

    def _read_exact(self, size: int) -> bytes:
        websocket = self._require_socket()
        chunks = bytearray()
        while len(chunks) < size:
            try:
                chunk = websocket.recv(size - len(chunks))
            except socket.timeout:
                continue
            if not chunk:
                raise StreamError("event stream socket closed unexpectedly.")
            chunks.extend(chunk)
        return bytes(chunks)

    def _require_socket(self) -> socket.socket | ssl.SSLSocket:
        if self._socket is None:
            raise StreamError("event stream is not connected.")
        return self._socket
