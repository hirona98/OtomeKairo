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
  "client_kind": "cocoro_console",
  "caps": [
    {
      "id": "vision.capture",
      "version": "1"
    },
    {
      "id": "camera.ptz",
      "version": "1"
    },
    {
      "id": "mcp.call_tool",
      "version": "1"
    }
  ],
  "event_subscriptions": [
    "conversation_input",
    "assistant_message",
    "assistant_audio",
    "audio_runtime_state"
  ],
  "mcp_servers": [
    {
      "mcp_server_id": "mcp:elyth",
      "transport": "stdio",
      "tools": [
        {
          "name": "get_information",
          "description": "ELYTH の現在情報を取得する",
          "inputSchema": { "type": "object" }
        }
      ]
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
- `client_kind` は `browser / cocoro_console / otomekairo_audio / capability_connector` のいずれかであり、表示・音声配送先の種類を明示する
- `caps` はその client が現在受けられる capability binding 候補の一覧である
- `event_subscriptions` はその client が受信して処理する server-driven event の一覧である
- `assistant_message` を表示できる client だけが `event_subscriptions` に `assistant_message` を入れる
- `assistant_audio` の直後の binary WAV を再生できる client だけが `event_subscriptions` に `assistant_audio` を入れる
- ユーザー発話を表示できる client は入力元にかかわらず `conversation_input` を入れる
- 音声 runtime を表示できる client だけが `audio_runtime_state` を入れる
- `mcp_servers` は `mcp.call_tool` を実行できる client が接続中 MCP server の許可済み tool catalog を通知する一覧である
- `vision_sources` はその client が `vision.capture` で観測できる視覚 source の一覧である
- capability 識別子は `vision.capture` のような canonical 名を使う
- `version` は server が持つ `CapabilityManifest` の版と照合する
- client は capability manifest を送らない
- 未知の capability id または非対応 version は実行不可として扱う
- `mcp.call_tool` が accepted された client は、`mcp_servers` を必須かつ 1 件以上にする
- `mcp_servers[].mcp_server_id` は `mcp:` で始め、接続中 server 全体で一意にする
- `mcp_servers[].transport` の初期対応値は `stdio` とする
- `mcp_servers[].tools[]` は MCP `tools/list` のうち、保存済み MCP server 定義の `enabled_tools` に含まれる tool の `name / description / inputSchema` だけを渡す
- `mcp_servers[].tools` は許可済み tool が MCP server に存在しない場合に空配列とする
- server は MCP server の有効状態、割当先 `client_id`、transport、`enabled_tools` が hello と一致する場合だけ catalog を登録する
- `mcp_servers` には API key、token、内部 URL、command、env を入れない
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
- `conversation_input` は確定したテキストまたは音声のユーザー発話を、購読中の全 client に表示させる通知である
- `assistant_message` は server が生成した assistant 発話を client に表示させる通知である
- `assistant_audio` は server が合成した assistant 発話音声の配送 metadata である
- `audio_runtime_state` は音声 runtime の process-local snapshot を表示させる通知である
- server は `event_subscriptions` に `assistant_message` を宣言した client だけへ `assistant_message` を送る
- server は `assistant_message` と `conversation_input` を、それぞれを購読する起動中の全 client へ履歴再送なしで配信する
- server は `audio_output_settings.destination` が示す `client_kind` のうち、`assistant_audio` を購読する起動中の全 client へ同じ合成済み WAV を配信する
- `assistant_message.data.source_kind` は発話生成の起点を示し、event type を増やして起点ごとの発話通知を分けない
- `assistant_message.data.interaction_ref / recipient_person_refs` は論理配送先を示す
- capability result follow-up の発話通知は `assistant_message` に `source_kind=capability_result`、`request_id`、`capability_id` を入れる
- `wake / background_thinking` の発話通知は `assistant_message` に `source_kind=wake / background_thinking`、`trigger_kind` を入れる

`capability_result` は event type として使わない。capability result そのものは client が `/api/capability/result` へ HTTP POST する payload であり、event stream の発話通知ではない。
`spontaneous_speech` は event type として使わない。自発発話も `assistant_message` に統一し、起点は `source_kind` で表す。
音声入力の状態遷移と配送規則は [../audio/音声入力と話者識別.md](../audio/音声入力と話者識別.md) を正とする。

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
  "type": "schedule.status_request",
  "data": {
    "request_id": "schedule_status_request:...",
    "capability_id": "schedule.status",
    "range": "このあと",
    "timeout_ms": 5000
  }
}
```

```json
{
  "event_id": 4,
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
  "event_id": 5,
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
  "event_id": 6,
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
  "event_id": 7,
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
  "event_id": 8,
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
  "event_id": 9,
  "type": "assistant_message",
  "data": {
    "message_id": "chat_message:...",
    "created_at": "2026-03-31T09:00:00+09:00",
    "cycle_id": "cycle:...",
    "source_kind": "capability_result",
    "request_id": "vision_capture_request:...",
    "capability_id": "vision.capture",
    "interaction_ref": "interaction:discord:channel-123",
    "recipient_person_refs": ["person:external-123"],
    "system_text": "[capability_result] vision.capture",
    "message": "Slack の general チャンネルが視覚前景に見えているよ。"
  }
}
```

```json
{
  "event_id": 10,
  "type": "assistant_message",
  "data": {
    "message_id": "chat_message:...",
    "created_at": "2026-03-31T09:00:00+09:00",
    "cycle_id": "cycle:...",
    "source_kind": "wake",
    "trigger_kind": "wake",
    "interaction_ref": "interaction:discord:channel-123",
    "recipient_person_refs": ["person:external-123"],
    "system_text": "[wake]",
    "message": "このあと 22 時の予定が近づいています。今の作業を切り上げる目安にしてください。"
  }
}
```

```json
{
  "event_id": 11,
  "type": "assistant_audio",
  "data": {
    "delivery_id": "tts_delivery:...",
    "destination": "cocoro_console",
    "cycle_id": "cycle:...",
    "source_kind": "wake",
    "interaction_ref": "interaction:discord:channel-123",
    "recipient_person_refs": ["person:external-123"],
    "status": "succeeded",
    "media_type": "audio/wav",
    "byte_count": 48236,
    "error_code": null
  }
}
```

この JSON message の直後に、`byte_count=48236` の1個の binary WebSocket messageを送る。

```json
{
  "event_id": 12,
  "type": "conversation_input",
  "data": {
    "message_id": "chat_message:...",
    "cycle_id": "cycle:...",
    "created_at": "2026-03-31T09:00:00+09:00",
    "utterance_seq": 18,
    "source_kind": "local_microphone",
    "source_client_id": "microphone-connector-main",
    "message": "おとめ、今日の予定を教えて",
    "interaction_ref": "interaction:voice:direct:550e8400-e29b-41d4-a716-446655440000",
    "speaker_ref": "person:voice:550e8400-e29b-41d4-a716-446655440000",
    "participant_refs": [
      "person:voice:550e8400-e29b-41d4-a716-446655440000"
    ],
    "display_name": "ひろ"
  }
}
```

```json
{
  "event_id": 13,
  "type": "audio_runtime_state",
  "data": {
    "available": true,
    "unavailable_reason": null,
    "model_ids": {
      "silero_vad": "silero-vad-v5",
      "wespeaker": "wespeaker-resnet34-voxceleb-v1"
    },
    "configured_source": "local_microphone",
    "effective_source": "local_microphone",
    "stt_enabled": true,
    "tts_enabled": true,
    "selected_avatar_id": "avatar:default",
    "audio_output_destination": "cocoro_console",
    "local_output_device": null,
    "audio_output_client_count": 2,
    "selected_device": {
      "host_api": "ALSA",
      "name": "USB Audio Device"
    },
    "connector": {
      "client_id": "microphone-connector-main",
      "connected": true
    },
    "active_source": "local_microphone",
    "lease_generation": 12,
    "last_heartbeat_at": "2026-03-31T09:00:00+09:00",
    "settings_generation": 4,
    "mode": "normal",
    "paused_reason": null,
    "conversation_input_blocked_reason": null,
    "vad": {
      "speaking": false,
      "probability": 0.03,
      "dbfs": -42.1
    },
    "normal_activation": {
      "state": "waiting",
      "active_until": null
    },
    "queue": {
      "processing": null,
      "waiting": []
    },
    "enrollment": null,
    "last_utterance_result": null
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
- `mcp.call_tool_request`: MCP server の tool 実行を client に要求する
- `conversation_input`: テキストまたは音声入力から確定したユーザー発話を全購読 client に表示させる
- `assistant_message`: server が生成した assistant 発話を client に表示させる
- `assistant_audio`: server が生成した assistant 発話の合成結果を metadata と WAV で配送する
- `audio_runtime_state`: 音声 runtime の完全 snapshot を表示させる

`vision.capture_request`、`camera.ptz_request`、`external.status_request`、`schedule.status_request`、`device.status_request`、`body.status_request`、`environment.status_request`、`location.status_request`、`social.status_request`、`mcp.call_tool_request` は capability 実行要求である。
`assistant_message` は server が生成した assistant 発話を client へ表示させる通知である。
`assistant_message.data.source_kind` は `conversation / capability_result / wake / background_thinking / autonomous_run` のいずれかであり、capability result follow-up の場合だけ `request_id / capability_id` を持つ。
`assistant_message.data.message_id / created_at / message` は全発話通知で必須とする。
`assistant_message.data.interaction_ref / recipient_person_refs` は全発話通知で必須とする。
`assistant_message.data.audio_delivery` と HTTP response の `speech.audio_delivery` は `delivery_id / status / error_code` を持つ。
`audio_delivery.status` は `queued / disabled / failed` のいずれかとする。
`queued` のときだけ `delivery_id` を返し、`disabled` のときは `error_code=null`、`failed` のときは `tts_target_unavailable / tts_queue_full` のいずれかを返す。
`assistant_audio.data` は `delivery_id / destination / cycle_id / source_kind / interaction_ref / recipient_person_refs / status / media_type / byte_count / error_code` を持つ。
音声合成に成功した場合、server は `status=succeeded / media_type=audio/wav / byte_count>0 / error_code=null` の JSON message と、その直後の1個の binary messageを同じ送信lock内で配送する。
binary message は RIFF/WAVE の PCM 16-bit または IEEE float 32-bit とし、長さを `byte_count` と一致させる。
音声合成に失敗した場合、server は `status=failed / media_type=null / byte_count=0` の JSON messageだけを送り、`error_code` を `tts_request_failed / tts_response_invalid / tts_response_too_large` のいずれかにする。
client は `status=succeeded` の `assistant_audio` を受信した場合だけ、直後の binary messageを対応する音声として扱う。
server は音声合成を timeout 60 秒、FIFO 1 worker、queue 上限32件、WAV上限32 MiBで実行し、再試行とengine fallbackを実行しない。
VOICEVOX では `voicevox_config.endpoint_url`（優先）と `secondary_endpoint_url`（サブ、空可）を持つ。
server は両 endpoint の HTTP 応答を 10 秒間隔で監視し、健全な1つを選んで合成する。両方健全なら常に優先を使う。
1 delivery 内で合成失敗した endpoint を即 unhealthy にし、**同一 delivery ではもう一方へ掛け直さない**。次の delivery または次のプローブ結果で接続先が切り替わる。
サブが空のときは優先だけを使い、ヘルス監視による切替を行わない。
TTS入力では発話本文先頭の`[face:Joy] / [face:Angry] / [face:Sorrow] / [face:Fun]`を1個だけ除去し、それ以外の文字、空白、改行を保持する。
server はTTS入力を文字列長で切り詰めない。
音声合成失敗は先行する発話本文の成功を取り消さない。
`conversation_input.data.message_id / cycle_id / created_at / message / interaction_ref / speaker_ref / participant_refs / display_name / source_kind` は必須とする。`utterance_seq` は音声入力時だけ持つ。
`audio_runtime_state.data` は [列挙とinspection.md](列挙とinspection.md) の `runtime_detail.audio_runtime_state` と同じ shape にする。
`audio_runtime_state` は完全 snapshot とし、差分 event にしない。
チャット event は保存済み履歴を再送せず、event 発生時に接続・購読中の client だけへ配送する。画像 Data URI は `conversation_input` に含めず、送信元 UI だけがローカル保持して表示する。
音声出力の意味規則は [../audio/音声出力.md](../audio/音声出力.md) を正とする。
capability 実行要求と結果の対応は [実行連携.md](実行連携.md) を正とする。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_websocket_upgrade` | `Upgrade: websocket` が不正 |
| `400` | `missing_websocket_key` | `Sec-WebSocket-Key` が無い |
| `400` | `invalid_websocket_version` | `Sec-WebSocket-Version` が `13` ではない |
| `400` | `invalid_event_subscriptions` | `hello.event_subscriptions` が不正 |
