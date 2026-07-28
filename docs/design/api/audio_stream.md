# 音声入力 API

## この文書の境界

この文書は、音声入力 WebSocket、話者管理 HTTP API、音声 input device API の path、method、認証、request / response、message、error code を正本にする。
入力リース、VAD、STT、話者識別、話者登録、配送の意味規則は [../audio/音声入力と話者識別.md](../audio/音声入力と話者識別.md) を正とする。
アバター音声設定 bundle は [状態と設定.md](状態と設定.md)、表示用 event は [event_stream.md](event_stream.md)、runtime inspection は [列挙とinspection.md](列挙とinspection.md) を正とする。

## 音声入力 WebSocket

### `GET /api/audio/stream`

- 認証: `Authorization: Bearer <console_access_token>`
- 利用主体: microphone connector
- 役割: connector control と PCM binary frame の双方向 stream

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
  "protocol_version": "1",
  "client_id": "microphone-connector-main",
  "input_source": "physical_microphone",
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

Web UI は `client_id` に対話 input と event stream で使用する session-scoped client ID を指定する。
`input_source` は connector endpoint では `physical_microphone`、Web endpoint では `web_microphone` と一致させる。
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

server -> client:

```json
{
  "type": "audio_started",
  "protocol_version": "1",
  "lease_generation": 12,
  "input_source": "physical_microphone",
  "mode": "normal",
  "heartbeat_interval_seconds": 5,
  "lease_timeout_seconds": 15
}
```

`audio_started` 前の binary message は protocol error とする。
2 件目の Web input は `audio_input_busy` とする。
Web input が physical input を横取りする場合は、physical client へ `audio_paused` を送ってから Web client へ `audio_started` を送る。

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

- `preempted_by_web`
- `queue_full`
- `stt_disabled`
- `stt_configuration_error`
- `speaker_enrollment_required`
- `microphone_device_unavailable`
- `response_client_unavailable`
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
Web input の stop 後、physical connector は新しい `audio_start` を送る。

### device catalog

microphone connector は接続後または device 一覧変化時に device catalog を送る。

```json
{
  "type": "audio_device_catalog",
  "client_id": "microphone-connector-main",
  "devices": [
    {
      "host_api": "ALSA",
      "name": "USB Audio Device",
      "max_input_channels": 1,
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
  "display_name": "ひろ"
}
```

再登録 request:

```json
{
  "owner_client_id": "console-main",
  "person_ref": "person:voice:550e8400-e29b-41d4-a716-446655440000"
}
```

新規登録では `display_name` を必須とし、`person_ref` を送らない。
再登録では `person_ref` を必須とし、`display_name` を送らない。
`owner_client_id` は接続中 event stream client と一致させる。

response:

```json
{
  "ok": true,
  "data": {
    "enrollment_id": "speaker_enrollment:...",
    "owner_client_id": "console-main",
    "person_ref": null,
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

## 話者表示名変更

### `PUT /api/audio/speakers/{person_ref}/display-name`

- 認証: 必要
- 役割: `person_ref` を維持して表示名を変更する

request:

```json
{
  "display_name": "ひろ"
}
```

表示名は前後空白を除いた非空文字列にする。
過去記録を更新しない。
`PUT /ui/api/audio/speakers/{person_ref}/display-name` は同じ処理を呼び出す。

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
| `400` | `invalid_speaker_enrollment` | 話者登録 request が不正 |
| `400` | `invalid_speaker_display_name` | 表示名が不正 |
| `401` | `invalid_token` | connector 認証が不正 |
| `403` | `invalid_audio_origin` | Web audio stream の Origin と Host が一致しない |
| `404` | `speaker_not_found` | 対象人物が存在しない |
| `404` | `speaker_enrollment_not_found` | 対象登録 session が存在しない |
| `409` | `audio_input_busy` | 別の Web input がリースを保持中 |
| `409` | `audio_lease_revoked` | 接続のリース generation が失効済み |
| `409` | `speaker_enrollment_busy` | 別の登録 session が進行中 |
| `409` | `speaker_enrollment_owner_mismatch` | owner 以外が中止を要求 |
| `422` | `speaker_enrollment_required` | active な話者 embedding が存在しない |
| `422` | `microphone_device_unavailable` | 選択 device が存在しない |
| `422` | `response_client_unavailable` | 物理音声の応答先が不在 |
| `503` | `audio_runtime_unavailable` | model runtime を利用できない |
| `503` | `stt_configuration_error` | STT 設定が成立しない |
