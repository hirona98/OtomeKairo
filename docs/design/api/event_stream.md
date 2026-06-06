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
    },
    {
      "id": "camera.ptz",
      "version": "1"
    }
  ],
  "vision_sources": [
    {
      "vision_source_id": "vision_source:main_display",
      "capability_id": "vision.capture",
      "kind": "desktop",
      "label": "メイン画面",
      "aliases": ["画面", "デスクトップ", "メインモニタ"],
      "default_for": ["visual", "desktop"],
      "required_permissions": ["observe_desktop"]
    },
    {
      "vision_source_id": "vision_source:room_camera",
      "capability_id": "vision.capture",
      "kind": "camera",
      "label": "部屋のカメラ",
      "aliases": ["カメラ", "部屋のカメラ"],
      "default_for": ["visual", "camera"],
      "required_permissions": ["observe_vision", "observe_camera"],
      "source_owner": "self",
      "supported_controls": {
        "camera.ptz": {
          "operations": ["move_up", "move_down", "move_left", "move_right"],
          "amounts": ["small", "medium"]
        }
      }
    }
  ]
}
```

- `client_id` は対象 client の安定識別子である
- `caps` はその client が現在受けられる capability binding 候補の一覧である
- `vision_sources` はその client が `vision.capture` で観測できる視覚 source の一覧である
- capability 識別子は `vision.capture` のような canonical 名を使う
- `version` は server が持つ `CapabilityManifest` の版と照合する
- client は capability manifest を送らない
- 未知の capability id または非対応 version は実行不可として扱う
- `vision.capture` が accepted された client は、`vision_sources` を必須かつ 1 件以上にする
- `vision.capture` が accepted されない client では、`vision_sources` は省略または空配列にする
- `vision_sources[].vision_source_id` は server 内で一意に扱う
- `vision_sources[].vision_source_id` は `vision_source:` で始める
- `vision_sources[].capability_id` は `vision.capture` と一致させる
- `vision_sources[].kind` は `desktop / camera / virtual` のいずれかにする
- `vision_sources[].required_permissions` は source 固有の権限照合に使う
- `vision_sources[].source_owner` は省略可能であり、`kind=camera` の採用済み source は `self`、`kind=desktop / virtual` は `user_environment` として扱う
- `vision_sources[].supported_controls` は source-targeted action capability の対応操作を表す
- `camera.ptz` を advertised する client は `supported_controls.camera.ptz.operations` と `supported_controls.camera.ptz.amounts` を持つ
- `supported_controls` には credential、内部 URL、機器 API 名、角度を入れない
- `hello.caps` と availability の意味境界は [../capability/capability_manifest.md](../capability/capability_manifest.md) を正とする
- 同じ `client_id` で再接続した場合、server は古い stream session を置き換える

この設計では、client から受ける message は `hello` だけとする。

event type の分類軸は次に固定する。

- `*_request` は server から client への capability 実行要求である
- `assistant_message` は server が生成した assistant 発話を client に表示させる通知である
- `assistant_message.data.source_kind` は発話生成の起点を示し、event type を増やして起点ごとの発話通知を分けない
- capability result follow-up の発話通知は `assistant_message` に `source_kind=capability_result`、`request_id`、`capability_id` を入れる
- `wake / background_wake` の発話通知は `assistant_message` に `source_kind=wake / background_wake`、`trigger_kind` を入れる

`capability_result` は event type として使わない。capability result そのものは client が `/api/capability/result` へ HTTP POST する payload であり、event stream の発話通知ではない。
`spontaneous_speech` は event type として使わない。自発発話も `assistant_message` に統一し、起点は `source_kind` で表す。

server -> client の代表例:

```json
{
  "event_id": 0,
  "type": "vision.capture_request",
  "data": {
    "request_id": "vision_capture_request:...",
    "capability_id": "vision.capture",
    "vision_source_id": "vision_source:main_display",
    "source_kind": "desktop",
    "source_label": "メイン画面",
    "mode": "still",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 1,
  "type": "camera.ptz_request",
  "data": {
    "request_id": "camera_ptz_request:...",
    "capability_id": "camera.ptz",
    "vision_source_id": "vision_source:room_camera",
    "source_kind": "camera",
    "source_label": "部屋のカメラ",
    "operation": "move_up",
    "amount": "small",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 2,
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
  "event_id": 3,
  "type": "device.status_request",
  "data": {
    "request_id": "device_status_request:...",
    "capability_id": "device.status",
    "scope": "connectivity",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 4,
  "type": "body.status_request",
  "data": {
    "request_id": "body_status_request:...",
    "capability_id": "body.status",
    "scope": "body",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 5,
  "type": "environment.status_request",
  "data": {
    "request_id": "environment_status_request:...",
    "capability_id": "environment.status",
    "scope": "workspace",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 6,
  "type": "location.status_request",
  "data": {
    "request_id": "location_status_request:...",
    "capability_id": "location.status",
    "scope": "current",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 7,
  "type": "social.status_request",
  "data": {
    "request_id": "social_status_request:...",
    "capability_id": "social.status",
    "scope": "current_social_context",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 8,
  "type": "assistant_message",
  "data": {
    "cycle_id": "cycle:...",
    "source_kind": "capability_result",
    "request_id": "vision_capture_request:...",
    "capability_id": "vision.capture",
    "system_text": "[capability_result] vision.capture",
    "message": "Slack の general チャンネルが視覚前景に見えているよ。"
  }
}
```

```json
{
  "event_id": 9,
  "type": "assistant_message",
  "data": {
    "cycle_id": "cycle:...",
    "source_kind": "background_wake",
    "trigger_kind": "background_wake",
    "system_text": "[background_wake]",
    "message": "このあと 22 時の予定が近づいています。今の作業を切り上げる目安にしてください。"
  }
}
```

少なくとも次の event type を持つ。

- `vision.capture_request`: 視覚 source の画像取得を client に要求する
- `camera.ptz_request`: camera source の向きや画角調整を client に要求する
- `external.status_request`: 外部 service の状態取得を client に要求する
- `schedule.status_request`: 予定情報の取得を client に要求する
- `device.status_request`: device 状態の取得を client に要求する
- `body.status_request`: body 状態の取得を client に要求する
- `environment.status_request`: 周辺環境状態の取得を client に要求する
- `location.status_request`: 位置状態の取得を client に要求する
- `social.status_request`: 社会的文脈の状態取得を client に要求する
- `assistant_message`: server が生成した assistant 発話を client に表示させる

`vision.capture_request`、`camera.ptz_request`、`external.status_request`、`schedule.status_request`、`device.status_request`、`body.status_request`、`environment.status_request`、`location.status_request`、`social.status_request` は capability 実行要求である。
`assistant_message` は server が生成した assistant 発話を client へ表示させる通知である。
`assistant_message.data.source_kind` は `capability_result / wake / background_wake` のいずれかであり、capability result follow-up の場合だけ `request_id / capability_id` を持つ。
`wake / background_wake` の `assistant_message` は、同じ cycle の client context または 起床前観測 の `vision_source_id` から解決した client へ送る。
capability 実行要求と結果の対応は [実行連携.md](実行連携.md) を正とする。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_websocket_upgrade` | `Upgrade: websocket` が不正 |
| `400` | `missing_websocket_key` | `Sec-WebSocket-Key` が無い |
| `400` | `invalid_websocket_version` | `Sec-WebSocket-Version` が `13` ではない |
