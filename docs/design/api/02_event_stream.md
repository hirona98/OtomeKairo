# event stream

<!-- Block: Role -->
## この文書の役割

この文書は、server-driven event/control stream の API 仕様を定める。

ここで扱うのは次である。

- `GET /api/events/stream`
- `POST /api/v2/vision/capture-response`

共通ルールは `00_API仕様ガイド.md` を正とする。

<!-- Block: EventControl -->
## event/control stream 面

### `GET /api/events/stream`

- 認証: 必要
- 役割: `desktop_watch` を含む server-driven event / command を WebSocket で配信する

handshake:

- HTTP `GET` で開始する
- `Authorization: Bearer <console_access_token>` を付ける
- `Upgrade: websocket`
- `Sec-WebSocket-Version: 13`
- `Sec-WebSocket-Key: ...`

接続後、client は最初に `hello` を送ってよい。

client -> server:

```json
{
  "type": "hello",
  "client_id": "console-...",
  "caps": ["vision.desktop", "vision.camera"]
}
```

- `client_id` は対象 client の安定識別子である
- `caps` はその client が現在受けられる command capability 一覧である
- 同じ `client_id` で再接続した場合、server は古い stream session を置き換えてよい

MVP では、client から受ける message は `hello` だけでよい。

server -> client の代表例:

```json
{
  "event_id": 0,
  "type": "vision.capture_request",
  "data": {
    "request_id": "vision_capture_request:...",
    "source": "desktop",
    "mode": "still",
    "purpose": "desktop_watch",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 1,
  "type": "desktop_watch",
  "data": {
    "system_text": "[desktop_watch] Slack",
    "message": "少し区切れそうなら、前の続きに戻っても大丈夫だよ。",
    "images": ["data:image/png;base64,..."]
  }
}
```

MVP では、少なくとも次の event type があればよい。

- `vision.capture_request`
- `desktop_watch`

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_websocket_upgrade` | `Upgrade: websocket` が不正 |
| `400` | `missing_websocket_key` | `Sec-WebSocket-Key` が無い |
| `400` | `invalid_websocket_version` | `Sec-WebSocket-Version` が `13` ではない |

### `POST /api/v2/vision/capture-response`

- 認証: 必要
- 役割: `vision.capture_request` の結果を返す

request:

```json
{
  "request_id": "vision_capture_request:...",
  "client_id": "console-...",
  "images": ["data:image/png;base64,..."],
  "client_context": {
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  },
  "error": null
}
```

- `images` は 0 件以上の Data URI 配列でよい
- `client_context` は省略可能な object
- `error` は省略可能な string または `null`
- `client_context.active_app / window_title / locale` は `desktop_watch` 観測の判断補助に使ってよい

response:

```json
{
  "ok": true,
  "data": {}
}
```

MVP では、遅延した capture response が来た場合は無視して成功で返してよい。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_request_id` | `request_id` が不正 |
| `400` | `invalid_client_id` | `client_id` が不正 |
| `400` | `invalid_images` | `images` が配列でない、または要素が不正 |
| `400` | `invalid_client_context` | `client_context` が object ではない |
| `400` | `invalid_capture_error` | `error` が string または `null` ではない |
| `409` | `capture_client_id_mismatch` | `request_id` に紐づく `target_client_id` と `client_id` が一致しない |
