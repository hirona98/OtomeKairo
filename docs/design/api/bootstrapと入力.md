# bootstrapと入力

## bootstrap 面

### `GET /api/bootstrap/probe`

- 認証: 不要
- 役割: bootstrap 面へ到達できるかを確認する

response:

```json
{
  "ok": true,
  "data": {
    "bootstrap_available": true,
    "https_required": true,
    "bootstrap_state": "unregistered"
  }
}
```

`bootstrap_state` は次のいずれかを返す。

| 値 | 意味 |
|----|------|
| `unregistered` | `console_access_token` が未発行であり、`register-first-console` が token を発行する |
| `registered` | `console_access_token` は発行済みであり、通常 API は認証を要求する |

`probe` は token 実値を返さない。

### `GET /api/bootstrap/server-identity`

- 認証: 不要
- 役割: 接続先の安定識別情報を読む

response:

```json
{
  "ok": true,
  "data": {
    "server_id": "server:...",
    "server_display_name": "OtomeKairo",
    "api_version": "0.1.0",
    "bootstrap_state": "unregistered",
    "console_access_token_issued": false
  }
}
```

`server-identity` は接続先識別と bootstrap 状態だけを返す。
`console_access_token_issued` は発行有無を示す boolean であり、token 実値は返さない。

### `POST /api/bootstrap/register-first-console`

- 認証: 不要
- 役割: 未発行状態の server に初回 console token を発行する
- request body: `{}` とする
- 実行条件: `console_access_token` が未発行であること

response:

```json
{
  "ok": true,
  "data": {
    "console_access_token": "tok_..."
  }
}
```

未発行状態では新しい token を発行し、発行済み状態では既存 token を返さない。
発行済み状態では `409 first_console_already_registered` を返す。
この endpoint は既存 token の確認、再表示、復旧には使わない。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `409` | `first_console_already_registered` | 初回 console token は発行済み |

### `POST /api/bootstrap/reissue-console-access-token`

- 認証: 必要
- 役割: 認証済みの console が現在の token を新しい token へ置き換える
- request body: `{}` とする

response:

```json
{
  "ok": true,
  "data": {
    "console_access_token": "tok_..."
  }
}
```

再発行に成功したら旧 token は失効する。
新 token の実値はこの response だけで返し、ログ、inspection、状態 API には残さない。

## 対話面

### `POST /api/conversation`

- 認証: 必要
- 役割: 会話入力を受け、会話 1 サイクルを実行する

request:

```json
{
  "text": "こんにちは",
  "images": ["data:image/png;base64,..."],
  "client_context": {
    "source": "CocoroConsole",
    "client_id": "console-...",
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  }
}
```

- `text` は必須の文字列
- `images` は任意の画像 Data URI 配列とする。値がないときは省略する
- `images` は最大 1 件とする
- `client_context` は object とする。値がないときは省略する
- 標準の `client_context` には `source / client_id / active_app / window_title / locale` を含める
- `client_context` の任意 field として `social_context_summary / environment_summary / location_summary / external_service_summary / body_state_summary / device_state_summary / schedule_summary` を定義する。いずれも raw payload ではなく短い要約だけを渡す
- server は raw `images` を永続化せず、必要な場合だけ詳細な視覚説明へ変換して shared pipeline と視覚記録へ渡す
- 会話の `images` は `conversation_attachment` として扱い、`vision.capture` の capability result とは結び付けない
- 会話の `images` だけから `world_state.visual_context` を更新しない
- server は上記 summary をそのまま永続化せず、必要な場合だけ `world_state` source pack の補助文脈へ使う
- server は会話入力を `current_input.sender=user`、`source_kind=user_message`、`response_target=user`、`text=<ユーザー原文>` として shared pipeline に渡す
- server は非空のユーザー原文に対する `decision.kind=noop` を契約違反として repair する。明示的な発話不要表現がある場合だけ `noop` を許可する

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "speech",
    "speech": {
      "text": "やわらかく穏やかに受け取ったよ。こんにちは"
    },
    "capability_request": null,
    "autonomous_run": null
  }
}
```

`result_kind` は外向きに返す主結果の種別であり、内部 `decision.kind` と常に一致しない。
`result_kind` は次のいずれかを返す。

- `speech`
- `capability_request`
- `noop`
- `internal_failure`

内部で `decision.kind=autonomous_run` が選ばれた場合、server は response の `result_kind` として `autonomous_run` を返さない。
会話入力で即時承諾発話を返す場合は `result_kind=speech` とし、`speech` と `autonomous_run` 要約を返す。
承諾発話がなく capability request を開始した場合は `result_kind=capability_request` とする。
承諾発話も capability request もない場合は `result_kind=noop` とする。

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "speech",
    "speech": {
      "text": "うん、1分後に声をかけるね。"
    },
    "capability_request": null,
    "autonomous_run": {
      "run_id": "autonomous_run:...",
      "memory_set_id": "memory_set:...",
      "status": "waiting_timer",
      "objective_summary": "1分後に声をかける。",
      "origin_kind": "user_message",
      "current_step_summary": "指定時刻まで待機する。",
      "history_summary": "action=none transition=wait_until",
      "next_run_at": "2026-06-08T01:11:45+09:00",
      "waiting_request_id": null,
      "pause_reason": null,
      "created_at": "2026-06-08T01:10:45+09:00",
      "updated_at": "2026-06-08T01:10:45+09:00",
      "completed_at": null
    }
  }
}
```

`result_kind=capability_request` のとき、server は capability request を `events/stream` へ配送済みであり、response には `capability_request` 要約を返す。
`capability_request` 要約には `request_id`、`capability_id`、`status`、`timeout_ms`、`readiness_digest` を含め、`target_client_id`、資格情報、内部 URL、transport 詳細は含めない。

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "capability_request",
    "speech": null,
    "capability_request": {
      "request_id": "vision_capture_request:...",
      "capability_id": "vision.capture",
      "status": "dispatched",
      "timeout_ms": 5000,
      "readiness_digest": {
        "family": "visual_observation",
        "world_state_type": "visual_context",
        "input_keys": ["vision_source_id", "mode"],
        "present_input_keys": ["vision_source_id", "mode"],
        "missing_input_keys": [],
        "input_keys_satisfied": true
      }
    }
  }
}
```

`result_kind=noop`、`result_kind=capability_request`、`result_kind=internal_failure` のとき、`speech` は `null` を返す。
`capability_request` が無い結果では、`capability_request` は `null` を返す。
`autonomous_run` が無い結果では、`autonomous_run` は `null` を返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_text` | `text` が文字列ではない |
| `400` | `invalid_images` | `images` が配列でない、2 件以上、Data URI でない、または要素が不正 |
| `400` | `invalid_client_context` | `client_context` が object ではない |

## 自律面

### `POST /api/wake`

- 認証: 必要
- 役割: API起床要求を受け、wake 1 サイクルを実行する

request:

```json
{
  "client_context": {
    "source": "CocoroConsole",
    "client_id": "console-...",
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  }
}
```

- `client_context` は object とする。値がないときは省略する
- wake でも `client_context` の `source / active_app / window_title / locale` を起床入力の整形に使う
- wake でも `client_context` の `social_context_summary / environment_summary / location_summary / external_service_summary / body_state_summary / device_state_summary / schedule_summary` があれば、`world_state` source pack の補助文脈へ使う

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "noop",
    "speech": null
  }
}
```

`result_kind` は次のいずれかを返す。

- `speech`
- `capability_request`
- `noop`
- `internal_failure`

保留意図は内部結果として扱い、外向きには返さない。
そのため、wake で内部的に保留意図が選ばれた場合も、response の `result_kind` は `noop` とする。
capability 実行を開始した場合は、`POST /api/conversation` と同じ `capability_request` 要約を返す。
内部で `decision.kind=autonomous_run` が選ばれた場合も、server は response の `result_kind` として `autonomous_run` を返さない。
外向き発話がない wake で run だけを開始または待機した場合は `result_kind=noop` とし、`autonomous_run` 要約を返す。

API起床は少なくとも次の挙動を持つ。

- `wake_policy.mode=disabled` なら `noop`
- `mode=interval` で次回時刻にまだ達していなければ `noop`
- `mode=interval` で `wake_policy.observations` がある場合、enabled observation を順番に取得し、成功結果をその回の判断へ進む前景シグナルとして扱い、visual capture は `visual_observation` の構造化出力で `change_state` を受け取り、視覚記録と `world_state` を整理してから wake 判断を 1 回だけ行う
- 起床前観測 が vision source 未接続の一時失敗だけで終わった場合、server は interval を消費せず短い再試行待ちにする
- 起床前観測 の同期 capability request は内部観測として扱い、`ongoing_action` を作らない
- server は wake 入力を `current_input.sender=system`、`source_kind=wake`、`response_target=none` として shared pipeline に渡す
- server 内の定期起床スケジューラは `current_input.sender=system`、`source_kind=background_wake`、`response_target=none` として shared pipeline に渡す
- capability request は dispatch 時点の `current_input` を request record の `source_current_input` に保存し、capability result の `response_target` は `source_current_input.response_target` を引き継ぐ
- `source_current_input.response_target=none` の capability result は内部観測結果として扱い、実効判断を `noop` に正規化し、assistant message を送信しない
- `source_current_input.response_target=user` の capability request は request record に外向き応答先 client を内部保存し、follow-up capability request へ引き継ぐ
- capability result follow-up の assistant message は、capability result を返した client ではなく request record の外向き応答先 client へ送る
- `wake / background_wake` の判断で `camera.ptz` を dispatch した場合も同じ `source_current_input` を保存し、result follow-up から同じ camera source の `vision.capture` を内部観測として発行できる
- visual observation は wake 判断へ渡し、`change_state=first_seen / changed` は wake 判断の `visual_observation` 前景候補として扱い、具体的な抑制根拠がなければ短い `speech` を第一候補として比較する
- visual observation の意味変化は `visual_observation` の `change_state / change_basis / change_reason_summary` を正とする。signature は runtime 追跡用の診断値として扱う
- visual observation は wake 判断へ渡す。LLM は change_state、drive_state、world_state、同一観測の反復有無、直近で触れた内容、進行中コミットメントを合わせて `speech / noop / pending_intent` を選ぶ
- 再評価時刻に達した保留意図があれば再評価し、必要なら `speech`

server 内の定期起床スケジューラも、同じ wake 1 サイクルを内部的に使う。

`result_kind=noop`、`result_kind=capability_request`、`result_kind=internal_failure` のとき、`speech` は `null` を返す。
`capability_request` が無い結果では、`capability_request` は `null` を返す。
`autonomous_run` が無い結果では、`autonomous_run` は `null` を返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_client_context` | `client_context` が object ではない |
