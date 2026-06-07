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

    def add_connection(self, websocket: ServerWebSocket, permissions: list[str] | None = None) -> str:
        # セッション
        session_id = f"event_stream_session:{uuid.uuid4().hex}"
        with self._lock:
            self._sessions[session_id] = {
                "session_id": session_id,
                "websocket": websocket,
                "client_id": None,
                "capabilities": {},
                "permissions": sorted(set(permissions or [])),
                "rejected_bindings": [],
                "event_subscriptions": [],
                "vision_sources": [],
            }

        # 結果
        return session_id

    def session_permissions(self, session_id: str) -> list[str]:
        # 接続主体に付与された capability 実行権限
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            permissions = session.get("permissions", [])
            if not isinstance(permissions, list):
                return []
            return [
                permission
                for permission in permissions
                if isinstance(permission, str) and permission
            ]

    def register_hello(
        self,
        session_id: str,
        *,
        client_id: str,
        capabilities: dict[str, str],
        rejected_bindings: list[dict[str, Any]],
        event_subscriptions: list[str] | None = None,
        vision_sources: list[dict[str, Any]] | None = None,
    ) -> None:
        # スナップショット
        replaced_sessions: list[dict[str, Any]] = []
        normalized_event_subscriptions = sorted(
            {
                event_type.strip()
                for event_type in event_subscriptions or []
                if isinstance(event_type, str) and event_type.strip()
            }
        )
        normalized_vision_sources = [dict(source) for source in vision_sources or []]
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

            # vision_source_id は接続中 client 全体で一意にする。
            existing_source_ids: set[str] = set()
            for existing_session in self._sessions.values():
                if existing_session.get("session_id") == session_id:
                    continue
                for source in existing_session.get("vision_sources", []):
                    if not isinstance(source, dict):
                        continue
                    source_id = source.get("vision_source_id")
                    if isinstance(source_id, str) and source_id.strip():
                        existing_source_ids.add(source_id.strip())
            duplicate_source_ids = sorted(
                {
                    source["vision_source_id"]
                    for source in normalized_vision_sources
                    if source.get("vision_source_id") in existing_source_ids
                }
            )
            if duplicate_source_ids:
                raise ValueError(f"duplicate_vision_source_id: {', '.join(duplicate_source_ids)}")

            # 更新
            session["client_id"] = client_id
            session["capabilities"] = dict(capabilities)
            session["rejected_bindings"] = list(rejected_bindings)
            session["event_subscriptions"] = normalized_event_subscriptions
            session["vision_sources"] = normalized_vision_sources

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
                capabilities = session.get("capabilities", {})
                if capability in capabilities:
                    return True

        # 空
        return False

    def client_accepts_event(self, client_id: str, event_type: str) -> bool:
        # 走査
        normalized_client_id = client_id.strip()
        normalized_event_type = event_type.strip()
        if not normalized_client_id or not normalized_event_type:
            return False
        with self._lock:
            for session in self._sessions.values():
                if session.get("client_id") != normalized_client_id:
                    continue
                event_subscriptions = session.get("event_subscriptions", [])
                if normalized_event_type in event_subscriptions:
                    return True

        # 空
        return False

    def find_single_client_with_event_subscription(self, event_type: str) -> str | None:
        # event を受け取れる接続中 client 群
        normalized_event_type = event_type.strip()
        if not normalized_event_type:
            return None
        with self._lock:
            client_ids = sorted(
                {
                    client_id.strip()
                    for session in self._sessions.values()
                    if isinstance((client_id := session.get("client_id")), str)
                    and client_id.strip()
                    and normalized_event_type in session.get("event_subscriptions", [])
                }
            )

        # 1 台だけのときだけ採用する
        if len(client_ids) != 1:
            return None
        return client_ids[0]

    def find_single_client_with_capability(self, capability: str) -> str | None:
        # capability を持つ接続中 client 群
        with self._lock:
            client_ids = sorted(
                {
                    client_id.strip()
                    for session in self._sessions.values()
                    if isinstance((client_id := session.get("client_id")), str)
                    and client_id.strip()
                    and capability in session.get("capabilities", {})
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

    def list_capability_bindings(self) -> dict[str, Any]:
        # inspection 用に接続中 client の binding 状態を要約する。
        accepted: dict[str, set[str]] = {}
        rejected: list[dict[str, Any]] = []
        vision_sources: list[dict[str, Any]] = []
        with self._lock:
            for session in self._sessions.values():
                client_id = session.get("client_id")
                if not isinstance(client_id, str) or not client_id.strip():
                    continue
                capabilities = session.get("capabilities", {})
                if not isinstance(capabilities, dict):
                    capabilities = {}
                for capability_id in capabilities:
                    accepted.setdefault(capability_id, set()).add(client_id)
                for rejected_binding in session.get("rejected_bindings", []):
                    if isinstance(rejected_binding, dict):
                        rejected.append(dict(rejected_binding))
                for source in session.get("vision_sources", []):
                    if not isinstance(source, dict):
                        continue
                    capability_id = source.get("capability_id")
                    if not isinstance(capability_id, str) or capability_id not in capabilities:
                        continue
                    vision_sources.append(
                        {
                            **dict(source),
                            "client_id": client_id.strip(),
                            "available": True,
                            "unavailable_reason": None,
                        }
                    )

        return {
            "accepted": {
                capability_id: sorted(client_ids)
                for capability_id, client_ids in accepted.items()
            },
            "rejected": rejected,
            "vision_sources": sorted(
                vision_sources,
                key=lambda item: str(item.get("vision_source_id") or ""),
            ),
        }

    def get_vision_source(self, vision_source_id: str) -> dict[str, Any] | None:
        # dispatch 用に vision_source_id から接続中 client と source metadata を引く。
        normalized_source_id = vision_source_id.strip()
        if not normalized_source_id:
            return None
        matches: list[dict[str, Any]] = []
        with self._lock:
            for session in self._sessions.values():
                client_id = session.get("client_id")
                if not isinstance(client_id, str) or not client_id.strip():
                    continue
                capabilities = session.get("capabilities", {})
                if not isinstance(capabilities, dict) or "vision.capture" not in capabilities:
                    continue
                for source in session.get("vision_sources", []):
                    if not isinstance(source, dict):
                        continue
                    if source.get("vision_source_id") != normalized_source_id:
                        continue
                    matches.append(
                        {
                            **dict(source),
                            "client_id": client_id.strip(),
                            "available": True,
                            "unavailable_reason": None,
                        }
                    )
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"Vision source is ambiguous: {normalized_source_id}")
        return matches[0]

    def find_single_vision_source(
        self,
        *,
        kind: str | None = None,
        default_for: str | None = None,
    ) -> dict[str, Any] | None:
        # 保存済み設定の古い vision_source_id を、接続中 source の安定した属性から再解決する。
        normalized_kind = kind.strip() if isinstance(kind, str) else ""
        normalized_default_for = default_for.strip() if isinstance(default_for, str) else ""
        if not normalized_kind and not normalized_default_for:
            return None

        matches: list[dict[str, Any]] = []
        with self._lock:
            for session in self._sessions.values():
                client_id = session.get("client_id")
                if not isinstance(client_id, str) or not client_id.strip():
                    continue
                capabilities = session.get("capabilities", {})
                if not isinstance(capabilities, dict) or "vision.capture" not in capabilities:
                    continue
                for source in session.get("vision_sources", []):
                    if not isinstance(source, dict):
                        continue
                    if normalized_kind and source.get("kind") != normalized_kind:
                        continue
                    default_for_values = source.get("default_for", [])
                    if normalized_default_for and (
                        not isinstance(default_for_values, list) or normalized_default_for not in default_for_values
                    ):
                        continue
                    matches.append(
                        {
                            **dict(source),
                            "client_id": client_id.strip(),
                            "available": True,
                            "unavailable_reason": None,
                        }
                    )
        if len(matches) != 1:
            return None
        return matches[0]

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
