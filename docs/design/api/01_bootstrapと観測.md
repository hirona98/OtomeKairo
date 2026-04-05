# bootstrapと観測

<!-- Block: Role -->
## この文書の役割

この文書は、bootstrap 面と通常の観測面の厳密な API 仕様を定める。

ここで扱うのは次である。

- bootstrap
- 会話観測
- wake 観測

共通ルールは `00_API仕様ガイド.md` を正とする。

<!-- Block: Bootstrap -->
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

MVP では `bootstrap_state` は常に `ready_for_first_console` を返してよい。

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
- request body: `{}` でよい

response:

```json
{
  "ok": true,
  "data": {
    "console_access_token": "tok_..."
  }
}
```

未発行状態では新しいトークンを発行し、発行済み状態では現在のトークンを返してよい。

### `POST /api/bootstrap/reissue-console-access-token`

- 認証: 必要
- 役割: 現在のトークンを新しいトークンへ置き換える
- request body: `{}` でよい

response:

```json
{
  "ok": true,
  "data": {
    "console_access_token": "tok_..."
  }
}
```

<!-- Block: Observation -->
## 観測面

### `POST /api/observations/conversation`

- 認証: 必要
- 役割: 会話観測を受け、会話 1 サイクルを実行する

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
- `client_context` は省略可能な object
- MVP では `client_context` に `source / client_id / active_app / window_title / locale` を含めてよい

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "reply",
    "reply": {
      "text": "gentleに受け取ったよ。こんにちは"
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

### `POST /api/observations/wake`

- 認証: 必要
- 役割: 起床観測を受け、wake 1 サイクルを実行する

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

- `client_context` は省略可能な object
- MVP では wake でも `client_context` の `source / active_app / window_title / locale` を観測正規化に使ってよい

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

`future_act` は内部結果として扱い、外向きには返さない。
そのため、wake で内部的に `future_act` が選ばれた場合も、response の `result_kind` は `noop` でよい。

MVP の wake API は少なくとも次の挙動を持ってよい。

- `wake_policy.mode=disabled` なら `noop`
- `mode=interval` で次回時刻にまだ達していなければ `noop`
- due な `future_act` 候補があれば再評価し、必要なら `reply`

server 内の background 起床スケジューラも、同じ wake 1 サイクルを内部的に使ってよい。

`result_kind=noop` または `result_kind=internal_failure` のとき、`reply` は `null` を返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_client_context` | `client_context` が object ではない |
