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
  "caps": [
    {
      "id": "vision.capture",
      "version": "1"
    }
  ]
}
```

- `client_id` は対象 client の安定識別子である
- `caps` はその client が現在受けられる capability binding 候補の一覧である
- capability 識別子は `vision.capture` のような canonical 名を使う
- `version` は server が持つ `CapabilityManifest` の版と照合する
- client は capability manifest を送らない
- 未知の capability id または非対応 version は実行不可として扱う
- `hello.caps` と availability の意味境界は [../17_capability_manifest.md](../17_capability_manifest.md) を正とする
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
  "type": "external.status_request",
  "data": {
    "request_id": "external_status_request:...",
    "capability_id": "external.status",
    "service": "calendar",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 2,
  "type": "desktop_watch",
  "data": {
    "system_text": "[desktop_watch] Slack",
    "message": "少し区切れそうなら、前の続きに戻っても大丈夫だよ。",
    "images": ["data:image/png;base64,..."]
  }
}
```

```json
{
  "event_id": 3,
  "type": "capability_result",
  "data": {
    "request_id": "vision_capture_request:...",
    "capability_id": "vision.capture",
    "system_text": "[capability_result] vision.capture",
    "message": "画面では Slack の general チャンネルが前景に見えているよ。"
  }
}
```

少なくとも次の event type を持つ。

- `vision.capture_request`
- `external.status_request`
- `desktop_watch`
- `capability_result`

`vision.capture_request` と `external.status_request` は capability 実行要求である。
`desktop_watch` は観測結果に基づく通知であり、capability 定義そのものではない。
`capability_result` は accepted capability result を shared pipeline に戻した後、その follow-up が `reply` になったときの通知である。
capability 実行要求と結果の対応は [05_実行連携.md](05_実行連携.md) を正とする。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_websocket_upgrade` | `Upgrade: websocket` が不正 |
| `400` | `missing_websocket_key` | `Sec-WebSocket-Key` が無い |
| `400` | `invalid_websocket_version` | `Sec-WebSocket-Version` が `13` ではない |
