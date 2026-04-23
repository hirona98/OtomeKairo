# event stream

## event/control stream 面

### `GET /api/events/stream`

- 認証: 必要
- 役割: capability 実行要求と観測通知を含む server-driven event / control を WebSocket で配信する

handshake:

- HTTP `GET` で開始する
- `Authorization: Bearer <console_access_token>` を付ける
- `Upgrade: websocket`
- `Sec-WebSocket-Version: 13`
- `Sec-WebSocket-Key: ...`

接続後、client は最初に `hello` を送る。

client -> server:

```json
{
  "type": "hello",
  "client_id": "console-...",
  "caps": ["vision.capture"]
}
```

- `client_id` は対象 client の安定識別子である
- `caps` はその client が現在受けられる capability 一覧である
- capability 識別子は `vision.capture` のような canonical 名を使う
- 同じ `client_id` で再接続した場合、server は古い stream session を置き換える

この設計では、client から受ける message は `hello` だけとする。

server -> client の代表例:

```json
{
  "event_id": 0,
  "type": "vision.capture_request",
  "data": {
    "request_id": "vision_capture_request:...",
    "capability_id": "vision.capture",
    "source": "desktop",
    "mode": "still",
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

少なくとも次の event type を持つ。

- `vision.capture_request`
- `desktop_watch`

`vision.capture_request` は capability 実行要求である。
`desktop_watch` は観測結果に基づく通知であり、capability 定義そのものではない。
capability 実行要求と結果の対応は [05_実行連携.md](05_実行連携.md) を正とする。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_websocket_upgrade` | `Upgrade: websocket` が不正 |
| `400` | `missing_websocket_key` | `Sec-WebSocket-Key` が無い |
| `400` | `invalid_websocket_version` | `Sec-WebSocket-Version` が `13` ではない |
