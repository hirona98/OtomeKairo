# 音声入力 API

## この文書の境界

この文書は、音声入力 WebSocket、Web 入力 session、話者管理 HTTP API、音声 input device API の path、method、認証、request / response、message、error code を正本にする。
入力リース、VAD、STT、話者識別、話者登録、配送の意味規則は [../audio/音声入力と話者識別.md](../audio/音声入力と話者識別.md) を正とする。
アバター音声設定 bundle は [状態と設定.md](状態と設定.md)、表示用 event は [event_stream.md](event_stream.md)、runtime inspection は [列挙とinspection.md](列挙とinspection.md) を正とする。

## 音声入力 WebSocket

### `GET /api/audio/stream`

- 認証: `Authorization: Bearer <console_access_token>`
- 利用主体: microphone connector
- 役割: `local_microphone` の connector control と PCM binary frame の双方向 stream

### `GET /api/audio/console-stream`

- 認証: `Authorization: Bearer <console_access_token>`
- 利用主体: CocoroConsole
- 役割: `console_microphone` の control と PCM binary frame の双方向 stream

### `GET /ui/api/audio/stream`

- 認証: server が保持する `console_access_token`
- 利用主体: 同一 origin の Web UI
- 役割: Web microphone control と PCM binary frame の双方向 stream
- `Origin` と `Host` が一致しない接続を拒否する
- token をブラウザへ返さない

共通 handshake:

- HTTP `GET` で開始する
- `Upgrade: websocket`
- `Sec-WebSocket-Version: 13`
- `Sec-WebSocket-Key` を付ける
- WebSocket fragmentation を使用しない

接続直後は JSON text message だけを受理する。
server が `audio_started` を返した後だけ binary PCM message を受理する。

### `audio_start`

client -> server:

```json
{
  "type": "audio_start",
  "protocol_version": "2",
  "client_id": "microphone-connector-main",
  "input_source": "local_microphone",
  "input_session_id": null,
  "format": {
    "sample_rate": 16000,
    "channels": 1,
    "sample_format": "pcm_s16le",
    "frame_duration_ms": 20,
    "bytes_per_frame": 640
  },
  "device": {
    "host_api": "ALSA",
    "name": "USB Audio Device"
  },
  "capture_settings": {
    "source_sample_rate": 48000
  }
}
```

`input_source` は local connector endpoint では `local_microphone`、CocoroConsole endpoint では `console_microphone`、Web endpoint では `web_microphone` と一致させる。
local connector と CocoroConsole は `input_session_id=null` を送る。
Web UI は `client_id` に対話 input と event stream で使用する session-scoped client ID、`input_session_id` に開始済み Web 入力 session の ID を指定する。
CocoroConsole の `device` は `device_id / name` を持つ。
Web UI は `capture_settings` に次を追加する。

```json
{
  "echo_cancellation": true,
  "noise_suppression": true,
  "auto_gain_control": false,
  "device_id_present": true
}
```

device ID 実値は送らない。
`echo_cancellation / noise_suppression / auto_gain_control` は
browser が実値を公開しない場合に `null` とする。
`device_id_present` は必ず boolean とする。

server -> client:

```json
{
  "type": "audio_started",
  "protocol_version": "2",
  "lease_generation": 12,
  "input_source": "local_microphone",
  "mode": "normal",
  "paused_reason": null,
  "heartbeat_interval_seconds": 5,
  "lease_timeout_seconds": 15
}
```

`audio_started` 前の binary message は protocol error とする。
`paused_reason` は開始直後から送信可能な場合に `null`、開始時点で pause 中の場合に pause reason を持つ。
client は `paused_reason=null` の `audio_started` または `audio_resumed` を受信した後だけ PCM を送る。
登録済み話者がいない場合は pause にせず、`audio_runtime_state.conversation_input_blocked_reason` を `speaker_enrollment_required` とする。server は PCM を受理して VAD と入力音量を配信するが、発話を通常の会話処理へ渡さない。
実効入力元と異なる source の `audio_start` は `audio_source_not_selected` とする。
2 件目の Web 入力 session は `audio_input_busy` とする。
通常入力と Web 入力 session の切替では旧 client へ `audio_paused` を送って generation を失効させてから、新しい source へ `audio_started` を送る。

## Web 入力 session API

### `POST /ui/api/audio/input-sessions`

- 認証: server が保持する `console_access_token`
- 利用主体: 同一 origin の Web UI
- 役割: 現在のタブだけで有効な入力元と応答先を開始する
- DBへ保存しない

request:

```json
{
  "owner_client_id": "web-ui:..."
}
```

`owner_client_id` は接続中の event stream client と一致させる。
入力元は保存済み `microphone_settings.input_source` から確定し、requestでは指定しない。

response:

```json
{
  "ok": true,
  "data": {
    "input_session_id": "web_audio_input:...",
    "owner_client_id": "web-ui:...",
    "input_source": "local_microphone"
  }
}
```

### `DELETE /ui/api/audio/input-sessions/{input_session_id}`

- 役割: Web 入力 session を終了して通常入力へ戻す
- request body は不要とする

owner の event stream 切断、Web audio stream 切断、heartbeat timeout でも同じ終了処理を実行する。
音声設定変更でもsessionを終了する。
選択中アバターの `stt.enabled` が false になったときもsessionを終了する。

## STT 運用トグル API

### `GET /api/audio/stt-enabled`

- 認証: 必要
- 利用主体: CocoroConsole、Stream Deck、その他運用 client
- 役割: 選択中アバターの `stt.enabled` を返す

### `PUT /api/audio/stt-enabled`

- 認証: 必要
- 利用主体: CocoroConsole、Stream Deck、その他運用 client
- 役割: 選択中アバターの `stt.enabled` だけを更新する
- request body は `enabled` だけを持つ
- 他の avatar field、microphone_settings、avatars 一覧は変更しない
- 値が変わらない保存でも成功 response を返す
- 値が変わった保存では音声 runtime の設定 reload を行い、`audio_runtime_state` を配信する
- write を認証済み設定編集操作として audit に残す
- response body に秘密値を含めない

request:

```json
{
  "enabled": true
}
```

response:

```json
{
  "ok": true,
  "data": {
    "enabled": true,
    "selected_avatar_id": "avatar:default"
  }
}
```

`GET /ui/api/audio/stt-enabled` と `PUT /ui/api/audio/stt-enabled` は同じ処理を呼び出す。
意味規則は [../audio/音声入力と話者識別.md](../audio/音声入力と話者識別.md) を正とする。
アバター音声 bundle 全体の編集は [状態と設定.md](状態と設定.md) を正とする。

## TTS 運用トグル API

### `GET /api/audio/tts-enabled`

- 認証: 必要
- 利用主体: CocoroConsole、Stream Deck、その他運用 client
- 役割: 選択中アバターの `tts.enabled` を返す

### `PUT /api/audio/tts-enabled`

- 認証: 必要
- 利用主体: CocoroConsole、Stream Deck、その他運用 client
- 役割: 選択中アバターの `tts.enabled` だけを更新する
- request body は `enabled` だけを持つ
- 他の avatar field、microphone_settings、avatars 一覧は変更しない
- 値が変わらない保存でも成功 response を返す
- 値が変わった保存では音声入力 runtime の lease を破棄せず、`audio_runtime_state` を force 配信する
- write を認証済み設定編集操作として audit に残す
- response body に秘密値を含めない

request:

```json
{
  "enabled": true
}
```

response:

```json
{
  "ok": true,
  "data": {
    "enabled": true,
    "selected_avatar_id": "avatar:default"
  }
}
```

`GET /ui/api/audio/tts-enabled` と `PUT /ui/api/audio/tts-enabled` は同じ処理を呼び出す。
`tts.enabled=false` では発話本文は成功し、`audio_delivery.status=disabled` とする。
アバター音声 bundle 全体の編集は [状態と設定.md](状態と設定.md) を正とする。

## 実効入力状態 API

### `GET /api/audio/input-state`

- 認証: 必要
- 利用主体: microphone connector、CocoroConsole
- 役割: DB上の保存入力元とWeb入力sessionを解決した現在の実効入力元を返す

response:

```json
{
  "ok": true,
  "data": {
    "configured_source": "local_microphone",
    "effective_source": "local_microphone",
    "stt_enabled": true,
    "tts_enabled": true,
    "selected_avatar_id": "avatar:default",
    "local_input_device": {
      "host_api": "ALSA",
      "name": "USB Audio Device"
    },
    "console": {
      "client_id": "console-main",
      "input_device": null
    }
  }
}
```

`console.input_device` は Windows input endpoint が未選択の場合に `null` とする。
`stt_enabled` は選択中アバターの保存済み `stt.enabled` である。
`tts_enabled` は選択中アバターの保存済み `tts.enabled` である。
このresponseの `effective_source` は process-local な実効値を含み、入力元設定の正本として保存しない。

## 音声stream data/control

### PCM binary message

- opcode: `0x2`
- payload size: 640 bytes
- payload: 320 samples の signed PCM16LE mono
- WebSocket FIN: `1`

payload size 不一致、奇数 byte、fragmented message、text による音声送信は protocol error とし、接続を close する。
失効 generation の接続から届いた PCM は処理せず `audio_lease_revoked` を返して close する。

### heartbeat

client -> server:

```json
{
  "type": "audio_heartbeat",
  "lease_generation": 12
}
```

server -> client:

```json
{
  "type": "audio_heartbeat_ack",
  "lease_generation": 12
}
```

client は 5 秒ごとに送る。
server は最後の有効 heartbeat から 15 秒でリースを失効させる。

### pause / resume

server -> client:

```json
{
  "type": "audio_paused",
  "lease_generation": 12,
  "reason": "queue_full"
}
```

```json
{
  "type": "audio_resumed",
  "lease_generation": 12
}
```

`audio_paused` 後は `audio_resumed` または新しい `audio_started` まで binary message を送らない。
`reason` は次のいずれかにする。

- `source_switched`
- `queue_full`
- `stt_disabled`
- `stt_configuration_error`
- `microphone_device_unavailable`
- `audio_runtime_unavailable`
- `settings_reloaded`

### `audio_stop`

client -> server:

```json
{
  "type": "audio_stop",
  "lease_generation": 12
}
```

server -> client:

```json
{
  "type": "audio_stopped",
  "lease_generation": 12
}
```

stop、disconnect、timeout では構築中発話を破棄する。
入力停止後は、実効入力元のproviderが新しい `audio_start` を送る。

### device catalog

microphone connector は接続後または device 一覧変化時に device catalog を送る。

```json
{
  "type": "audio_device_catalog",
  "client_id": "microphone-connector-main",
  "input_devices": [
    {
      "host_api": "ALSA",
      "name": "USB Audio Device",
      "max_input_channels": 1,
      "default_sample_rate": 48000
    }
  ],
  "output_devices": [
    {
      "host_api": "ALSA",
      "name": "USB Audio Device",
      "max_output_channels": 2,
      "default_sample_rate": 48000
    }
  ]
}
```

connector は `audio_device_catalog` を `audio_start` 前、pause 中、音声入力無効中にも送る。
server は device catalog を process-local state とし、DBへ保存しない。
同じ host API と name が複数ある場合は各 entry に `ambiguous=true` を付ける。

### control error

server -> client:

```json
{
  "type": "audio_error",
  "code": "audio_input_busy",
  "message": "Another web microphone owns the audio input lease."
}
```

秘密値、文字起こし、音声、表示名、embedding を `message` に含めない。

## input device API

### `GET /api/audio/input-devices`

- 認証: 必要
- 役割: microphone connector が報告した現在の input device catalog を返す

response:

```json
{
  "ok": true,
  "data": {
    "connector_client_id": "microphone-connector-main",
    "connector_connected": true,
    "devices": [
      {
        "host_api": "ALSA",
        "name": "USB Audio Device",
        "max_input_channels": 1,
        "default_sample_rate": 48000,
        "ambiguous": false
      }
    ]
  }
}
```

`GET /ui/api/audio/input-devices` は同じ処理を呼び出す。
device catalog は現在値であり、再起動をまたぐ正本ではない。

## output device / state API

### `GET /api/audio/output-devices`

- 認証: 必要
- 役割: microphone connector が報告した現在の output device catalog を返す

response は input device API と同じ最上位 shape で、`devices[]` は `host_api / name / max_output_channels / default_sample_rate / ambiguous` を持つ。`GET /ui/api/audio/output-devices` は同じ処理を呼び出す。

### `GET /api/audio/output-state`

- 認証: 必要
- 利用主体: microphone connector、CocoroConsole
- 役割: 保存済みの音声出力先と OtomeKairo ローカル出力デバイスを返す

```json
{
  "ok": true,
  "data": {
    "destination": "otomekairo",
    "local_output_device": {
      "host_api": "ALSA",
      "name": "USB Audio Device"
    }
  }
}
```

`GET /ui/api/audio/output-state` は同じ処理を呼び出す。意味規則は [../audio/音声出力.md](../audio/音声出力.md) を正とする。

## 話者一覧

### `GET /api/audio/speakers`

- 認証: 必要
- 役割: 音声人物と登録状態を一覧する
- embedding を返さない

response:

```json
{
  "ok": true,
  "data": {
    "speakers": [
      {
        "person_ref": "person:voice:550e8400-e29b-41d4-a716-446655440000",
        "conversation_display_name_id": "conversation_display_name:hiro",
        "display_name": "ひろ",
        "registration_status": "registered",
        "model_id": "wespeaker-resnet34-voxceleb-v1",
        "registered_at": "2026-07-28T10:00:00+09:00",
        "updated_at": "2026-07-28T10:00:00+09:00"
      }
    ]
  }
}
```

`registration_status` は `registered / unregistered` のいずれかにする。
`GET /ui/api/audio/speakers` は同じ処理を呼び出す。

## 話者登録

### `POST /api/audio/speaker-enrollments`

- 認証: 必要
- 役割: 新規登録または既存人物の再登録を開始する

新規登録 request:

```json
{
  "owner_client_id": "console-main",
  "conversation_display_name_id": "conversation_display_name:hiro"
}
```

再登録 request:

```json
{
  "owner_client_id": "console-main",
  "person_ref": "person:voice:550e8400-e29b-41d4-a716-446655440000"
}
```

新規登録では未割当の `conversation_display_name_id` を必須とし、`person_ref` を送らない。
再登録では `person_ref` を必須とし、`conversation_display_name_id` を送らない。
`owner_client_id` は接続中 event stream client と一致させる。
現在の音声入力リースが存在すれば、`owner_client_id` と入力元 client は一致しなくてよい。

response:

```json
{
  "ok": true,
  "data": {
    "enrollment_id": "speaker_enrollment:...",
    "owner_client_id": "console-main",
    "person_ref": null,
    "conversation_display_name_id": "conversation_display_name:hiro",
    "display_name": "ひろ",
    "required_samples": 3,
    "completed_samples": 0,
    "expires_at": "2026-07-28T10:02:00+09:00"
  }
}
```

`POST /ui/api/audio/speaker-enrollments` は同じ処理を呼び出し、`owner_client_id` を Web session client ID と一致させる。

### `DELETE /api/audio/speaker-enrollments/{enrollment_id}`

- 認証: 必要
- 役割: owner が登録 session を中止する

request body は不要とする。
成功時は中止後の enrollment summary を返す。
`DELETE /ui/api/audio/speaker-enrollments/{enrollment_id}` は同じ処理を呼び出す。

## 話者の呼ばれ方割当変更

### `PUT /api/audio/speakers/{person_ref}/conversation-display-name`

- 認証: 必要
- 役割: `person_ref` を維持して共有する呼ばれ方定義の割当を変更する

request:

```json
{
  "conversation_display_name_id": "conversation_display_name:hiro"
}
```

存在する未割当の呼ばれ方定義だけを指定する。
過去記録は更新しない。
`PUT /ui/api/audio/speakers/{person_ref}/conversation-display-name` は同じ処理を呼び出す。

## 音声登録解除

### `DELETE /api/audio/speakers/{person_ref}/registration`

- 認証: 必要
- 役割: embedding を削除し、人物 row を未登録状態で維持する

request body は不要とする。
成功時は `registration_status=unregistered` の speaker summary を返す。
`DELETE /ui/api/audio/speakers/{person_ref}/registration` は同じ処理を呼び出す。

## 共通エラー

| HTTP | `error.code` | 意味 |
| --- | --- | --- |
| `400` | `invalid_audio_start` | `audio_start` の shape または値が不正 |
| `400` | `invalid_audio_frame` | PCM frame の長さまたは形式が不正 |
| `400` | `invalid_audio_control` | control message が不正 |
| `400` | `invalid_stt_enabled` | `stt-enabled` request が不正 |
| `400` | `invalid_tts_enabled` | `tts-enabled` request が不正 |
| `400` | `invalid_speaker_enrollment` | 話者登録 request が不正 |
| `400` | `invalid_conversation_display_name_assignment` | 呼ばれ方定義の割当 request が不正 |
| `401` | `invalid_token` | connector 認証が不正 |
| `403` | `invalid_audio_origin` | Web audio stream の Origin と Host が一致しない |
| `404` | `speaker_not_found` | 対象人物が存在しない |
| `404` | `speaker_enrollment_not_found` | 対象登録 session が存在しない |
| `404` | `conversation_display_name_not_found` | 対象の呼ばれ方定義が存在しない |
| `404` | `audio_input_session_not_found` | 対象Web入力sessionが存在しない |
| `409` | `audio_input_busy` | 別の Web input がリースを保持中 |
| `409` | `audio_source_not_selected` | 接続sourceが現在の実効入力元ではない |
| `409` | `audio_lease_revoked` | 接続のリース generation が失効済み |
| `409` | `speaker_enrollment_busy` | 別の登録 session が進行中 |
| `409` | `speaker_enrollment_owner_mismatch` | owner 以外が中止を要求 |
| `409` | `conversation_display_name_already_assigned` | 呼ばれ方定義が別の音声話者へ割当済み |
| `422` | `microphone_device_unavailable` | 選択 device が存在しない |
| `503` | `audio_runtime_unavailable` | model runtime を利用できない |
| `503` | `stt_configuration_error` | STT 設定が成立しない |
