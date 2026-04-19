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
    "bootstrap_state": "ready_for_first_console"
  }
}
```

この設計では `bootstrap_state` は `ready_for_first_console` を返す。

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
    "console_access_token_issued": false
  }
}
```

### `POST /api/bootstrap/register-first-console`

- 認証: 不要
- 役割: 通常 API に入るための `console_access_token` を受け取る
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

未発行状態では新しいトークンを発行し、発行済み状態では現在のトークンを返す。

### `POST /api/bootstrap/reissue-console-access-token`

- 認証: 必要
- 役割: 現在のトークンを新しいトークンへ置き換える
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

## 対話面

### `POST /api/conversation`

- 認証: 必要
- 役割: 会話入力を受け、会話 1 サイクルを実行する

request:

```json
{
  "text": "こんにちは",
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
- `client_context` は object とする。値がないときは省略する
- 標準の `client_context` には `source / client_id / active_app / window_title / locale` を含める

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "reply",
    "reply": {
      "text": "やわらかく穏やかに受け取ったよ。こんにちは"
    }
  }
}
```

`result_kind` は次のいずれかを返す。

- `reply`
- `noop`
- `internal_failure`

`result_kind=noop` または `result_kind=internal_failure` のとき、`reply` は `null` を返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_text` | `text` が文字列ではない |
| `400` | `invalid_client_context` | `client_context` が object ではない |

## 自律面

### `POST /api/wake`

- 認証: 必要
- 役割: 起床要求を受け、wake 1 サイクルを実行する

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

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "noop",
    "reply": null
  }
}
```

`result_kind` は次のいずれかを返す。

- `reply`
- `noop`
- `internal_failure`

保留意図は内部結果として扱い、外向きには返さない。
そのため、wake で内部的に保留意図が選ばれた場合も、response の `result_kind` は `noop` とする。

wake API は少なくとも次の挙動を持つ。

- `wake_policy.mode=disabled` なら `noop`
- `mode=interval` で次回時刻にまだ達していなければ `noop`
- 再評価時刻に達した保留意図があれば再評価し、必要なら `reply`

server 内の background 起床スケジューラも、同じ wake 1 サイクルを内部的に使う。

`result_kind=noop` または `result_kind=internal_failure` のとき、`reply` は `null` を返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_client_context` | `client_context` が object ではない |
