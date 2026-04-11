from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import uuid
from typing import Any


# 定数
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# エラー
class WebSocketProtocolError(Exception):
    pass


# ハンドシェイク
def build_websocket_accept(key: str) -> str:
    # 要約
    digest = hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode("utf-8")).digest()

    # 結果
    return base64.b64encode(digest).decode("ascii")


# WebSocket処理
class ServerWebSocket:
    def __init__(self, connection: socket.socket) -> None:
        # 項目
        self._connection = connection
        self._send_lock = threading.Lock()
        self._closed = False

    def receive_json(self) -> dict[str, Any] | None:
        # テキスト
        text = self.receive_text()
        if text is None:
            return None

        # デコード
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise WebSocketProtocolError("WebSocket message must be a JSON object.")

        # 結果
        return payload

    def receive_text(self) -> str | None:
        # ループ
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x8:
                self.close()
                return None
            if opcode == 0x9:
                self._send_frame(opcode=0xA, payload=payload)
                continue
            if opcode == 0xA:
                continue
            if opcode != 0x1:
                raise WebSocketProtocolError(f"Unsupported opcode: {opcode}")
            return payload.decode("utf-8")

    def send_json(self, payload: dict[str, Any]) -> None:
        # エンコード
        self.send_text(json.dumps(payload, ensure_ascii=False))

    def send_text(self, text: str) -> None:
        # エンコード
        self._send_frame(opcode=0x1, payload=text.encode("utf-8"))

    def close(self) -> None:
        # 冪等化
        with self._send_lock:
            if self._closed:
                return
            self._closed = True

            # フレーム
            try:
                self._send_frame_unlocked(opcode=0x8, payload=b"")
            except OSError:
                pass

            # ソケット
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._connection.close()

    def _read_frame(self) -> tuple[int, bytes]:
        # ヘッダー
        header = self._read_exact(2)
        first_byte = header[0]
        second_byte = header[1]
        fin = (first_byte & 0x80) != 0
        opcode = first_byte & 0x0F
        masked = (second_byte & 0x80) != 0
        payload_length = second_byte & 0x7F

        # 検証
        if not fin:
            raise WebSocketProtocolError("Fragmented frames are not supported.")
        if not masked:
            raise WebSocketProtocolError("Client frames must be masked.")

        # 拡張長
        if payload_length == 126:
            payload_length = struct.unpack("!H", self._read_exact(2))[0]
        elif payload_length == 127:
            payload_length = struct.unpack("!Q", self._read_exact(8))[0]

        # payload読み取り
        mask = self._read_exact(4)
        payload = self._read_exact(payload_length)
        unmasked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, unmasked

    def _send_frame(self, *, opcode: int, payload: bytes) -> None:
        # ロック下送信
        with self._send_lock:
            if self._closed:
                return
            self._send_frame_unlocked(opcode=opcode, payload=payload)

    def _send_frame_unlocked(self, *, opcode: int, payload: bytes) -> None:
        # ヘッダー
        header = bytearray([0x80 | (opcode & 0x0F)])
        payload_length = len(payload)
        if payload_length < 126:
            header.append(payload_length)
        elif payload_length < 65536:
            header.append(126)
            header.extend(struct.pack("!H", payload_length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", payload_length))

        # 送信
        self._connection.sendall(bytes(header) + payload)

    def _read_exact(self, size: int) -> bytes:
        # ループ
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self._connection.recv(size - len(chunks))
            if not chunk:
                raise WebSocketProtocolError("WebSocket connection closed.")
            chunks.extend(chunk)
        return bytes(chunks)


# レジストリ
class EventStreamRegistry:
    def __init__(self) -> None:
        # 項目
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}

    def add_connection(self, websocket: ServerWebSocket) -> str:
        # セッション
        session_id = f"event_stream_session:{uuid.uuid4().hex}"
        with self._lock:
            self._sessions[session_id] = {
                "session_id": session_id,
                "websocket": websocket,
                "client_id": None,
                "caps": set(),
            }

        # 結果
        return session_id

    def register_hello(self, session_id: str, *, client_id: str, caps: list[str]) -> None:
        # スナップショット
        replaced_sessions: list[dict[str, Any]] = []
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)

            # 置換既存
            for existing_session_id, existing_session in list(self._sessions.items()):
                if existing_session_id == session_id:
                    continue
                if existing_session.get("client_id") != client_id:
                    continue
                replaced_sessions.append(existing_session)
                self._sessions.pop(existing_session_id, None)

            # 更新
            session["client_id"] = client_id
            session["caps"] = set(caps)

        # 置換済み接続のクローズ
        for replaced_session in replaced_sessions:
            try:
                replaced_session["websocket"].close()
            except OSError:
                continue

    def remove_connection(self, session_id: str) -> None:
        # 削除
        with self._lock:
            self._sessions.pop(session_id, None)

    def has_capability(self, client_id: str, capability: str) -> bool:
        # 走査
        with self._lock:
            for session in self._sessions.values():
                if session.get("client_id") != client_id:
                    continue
                caps = session.get("caps", set())
                if capability in caps:
                    return True

        # 空
        return False

    def find_single_client_with_capability(self, capability: str) -> str | None:
        # capability を持つ接続中 client 群
        with self._lock:
            client_ids = sorted(
                {
                    client_id.strip()
                    for session in self._sessions.values()
                    if isinstance((client_id := session.get("client_id")), str)
                    and client_id.strip()
                    and capability in session.get("caps", set())
                }
            )

        # 1 台だけのときだけ採用する
        if len(client_ids) != 1:
            return None
        return client_ids[0]

    def is_client_connected(self, client_id: str) -> bool:
        # 走査
        with self._lock:
            for session in self._sessions.values():
                if session.get("client_id") == client_id:
                    return True

        # 空
        return False

    def send_to_client(self, client_id: str, payload: dict[str, Any]) -> bool:
        # スナップショット
        with self._lock:
            target_session = None
            for session in self._sessions.values():
                if session.get("client_id") != client_id:
                    continue
                target_session = session

        # 空
        if target_session is None:
            return False

        # 送信
        websocket = target_session["websocket"]
        try:
            websocket.send_json(payload)
        except OSError:
            # 後始末
            self.remove_connection(target_session["session_id"])
            return False

        # 結果
        return True

    def close_all(self) -> None:
        # スナップショット
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions = {}

        # クローズ
        for session in sessions:
            try:
                session["websocket"].close()
            except OSError:
                continue
