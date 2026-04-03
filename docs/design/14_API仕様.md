# API仕様

<!-- Block: Role -->
## この文書の役割

この文書は、OtomeKairo が外へ公開する API の厳密な仕様を定める正本である。

ここで固定するのは次である。

- API パス
- HTTP メソッド
- 認証の渡し方
- request / response の JSON 形式
- 成功時と失敗時の返し方

API を実装または変更する場合は、同じ変更内でこの文書も更新する。

<!-- Block: ParentDocs -->
## 上位文書

この文書は、少なくとも次の文書を具体化する。

- `08_API概念.md`
- `11_接続と認証.md`
- `12_デバッグ可能性.md`
- `13_自発判断と自発行動.md`

この文書では、これらの上位文書で定めた責務境界を変えない。

<!-- Block: Scope -->
## 現時点で実装する API 面

この文書で扱うのは、現時点で実装する次の API 面である。

- bootstrap 面
- 観測面
- 状態面
- 設定面
- 列挙面
- inspection 面

内部関数、保存形式、LLM 呼び出しの内部 shape は対象に含めない。

<!-- Block: CommonRule -->
## 共通ルール

### ベース

- ベース URL は `https://<host>:<port>` とする
- request / response は `application/json` とする
- 成功時は常に `{"ok": true, "data": ...}` を返す
- 失敗時は常に `{"ok": false, "error": {"code": "...", "message": "..."}}` を返す

### 認証

- bootstrap 面の `GET /api/bootstrap/probe`
- bootstrap 面の `GET /api/bootstrap/server-identity`
- bootstrap 面の `POST /api/bootstrap/register-first-console`

上の 3 つは未認証で呼べてよい。

それ以外の API は `Authorization: Bearer <console_access_token>` を必須とする。

### 主な共通エラー

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_json` | JSON として解釈できない |
| `400` | `invalid_json_shape` | request body が object ではない |
| `401` | `bootstrap_required` | まだ `console_access_token` が発行されていない |
| `401` | `invalid_token` | 認証トークンが無い、または不正 |
| `404` | `route_not_found` | 未定義の route |
| `409` | `last_resource_delete_forbidden` | 最後の 1 件を削除しようとした |
| `500` | `internal_server_error` | サーバ内部で失敗した |

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
    "source": "CocoroConsole"
  }
}
```

- `text` は必須の文字列
- `client_context` は省略可能な object

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

<!-- Block: Status -->
## 状態面

### `GET /api/status`

- 認証: 必要
- 役割: 運用に必要な設定スナップショットとランタイム要約を返す

response:

```json
{
  "ok": true,
  "data": {
    "settings_snapshot": {
      "selected_persona_id": "persona:default",
      "selected_memory_set_id": "memory_set:default",
      "memory_enabled": true,
      "desktop_watch": {
        "enabled": false,
        "interval_seconds": 300,
        "target_client_id": null
      },
      "wake_policy": {
        "mode": "disabled"
      },
      "selected_model_preset_id": "model_preset:default"
    },
    "runtime_summary": {
      "loaded_persona_ref": {
        "persona_id": "persona:default",
        "display_name": "Default Persona"
      },
      "loaded_memory_set_ref": {
        "memory_set_id": "memory_set:default",
        "display_name": "Default Memory"
      },
      "loaded_model_preset_ref": {
        "model_preset_id": "model_preset:default",
        "display_name": "Default Mock Preset"
      },
      "connection_state": "ready",
      "wake_scheduler_active": false,
      "ongoing_action_exists": false
    }
  }
}
```

<!-- Block: Config -->
## 設定面

### `GET /api/config`

- 認証: 必要
- 役割: 現在設定と現在選択中の設定資源を返す

response:

```json
{
  "ok": true,
  "data": {
    "settings_snapshot": {
      "selected_persona_id": "persona:default",
      "selected_memory_set_id": "memory_set:default",
      "memory_enabled": true,
      "desktop_watch": {
        "enabled": false,
        "interval_seconds": 300,
        "target_client_id": null
      },
      "wake_policy": {
        "mode": "disabled"
      },
      "selected_model_preset_id": "model_preset:default"
    },
    "selected_persona": {},
    "selected_memory_set": {},
    "selected_model_preset": {},
    "selected_model_profile_ids": {
      "reply_generation": "model_profile:mock_reply"
    },
    "selected_model_profiles": {
      "reply_generation": {}
    }
  }
}
```

### `PATCH /api/config/current`

- 認証: 必要
- 役割: 現在設定のうち変更したい項目だけを更新する

request body の最小 shape:

```json
{
  "selected_persona_id": "persona:default",
  "selected_memory_set_id": "memory_set:default",
  "selected_model_preset_id": "model_preset:default",
  "memory_enabled": true,
  "desktop_watch": {
    "enabled": false,
    "interval_seconds": 300,
    "target_client_id": null
  },
  "wake_policy": {
    "mode": "disabled"
  }
}
```

各項目は省略してよい。response は `GET /api/config` と同じ shape を返す。

### `POST /api/config/select-persona`

request:

```json
{
  "persona_id": "persona:default"
}
```

### `POST /api/config/select-memory-set`

request:

```json
{
  "memory_set_id": "memory_set:default"
}
```

### `POST /api/config/update-wake-policy`

request:

```json
{
  "wake_policy": {
    "mode": "interval",
    "interval_minutes": 5
  }
}
```

### `POST /api/config/select-model-preset`

request:

```json
{
  "model_preset_id": "model_preset:default"
}
```

上の 4 つの response は、すべて `GET /api/config` と同じ shape を返す。

### `GET /api/config/editor-state`

- 認証: 必要
- 役割: `CocoroConsole` の編集 UI がまとめて扱いやすい bundle を返す

response:

```json
{
  "ok": true,
  "data": {
    "current": {
      "selected_persona_id": "persona:default",
      "selected_memory_set_id": "memory_set:default",
      "selected_model_preset_id": "model_preset:default",
      "memory_enabled": true,
      "desktop_watch": {
        "enabled": false,
        "interval_seconds": 300,
        "target_client_id": null
      },
      "wake_policy": {
        "mode": "disabled"
      }
    },
    "personas": [
      {
        "persona_id": "persona:default",
        "display_name": "Default Persona"
      }
    ],
    "memory_sets": [
      {
        "memory_set_id": "memory_set:default",
        "display_name": "Default Memory"
      }
    ],
    "model_presets": [
      {
        "model_preset_id": "model_preset:default",
        "display_name": "Default Mock Preset"
      }
    ],
    "model_profiles": [
      {
        "model_profile_id": "model_profile:mock_reply",
        "display_name": "Mock Reply"
      }
    ]
  }
}
```

### `PUT /api/config/editor-state`

- 認証: 必要
- 役割: 現在設定と設定資源群を bundle 単位で全体置換する

request body の最小 shape:

```json
{
  "current": {
    "selected_persona_id": "persona:default",
    "selected_memory_set_id": "memory_set:default",
    "selected_model_preset_id": "model_preset:default",
    "memory_enabled": true,
    "desktop_watch": {
      "enabled": false,
      "interval_seconds": 300,
      "target_client_id": null
    },
    "wake_policy": {
      "mode": "disabled"
    }
  },
  "personas": [],
  "memory_sets": [],
  "model_presets": [],
  "model_profiles": []
}
```

response は `GET /api/config/editor-state` と同じ shape を返す。

### `GET /api/config/personas/{persona_id}`

- 認証: 必要
- 役割: 指定した `persona` の詳細を返す

### `PUT /api/config/personas/{persona_id}`

- 認証: 必要
- 役割: 指定した `persona` を全体置換で作成または更新する

request body の最小 shape:

```json
{
  "persona_id": "persona:default",
  "display_name": "Default Persona",
  "persona_text": "やわらかく寄り添いながら会話する。",
  "second_person_label": "あなた",
  "addon_text": "",
  "core_persona": {
    "self_image": "long-term companion"
  },
  "expression_style": {
    "tone": "gentle"
  }
}
```

### `DELETE /api/config/personas/{persona_id}`

- 認証: 必要
- 役割: 指定した `persona` を削除する

### `GET /api/config/memory-sets/{memory_set_id}`

- 認証: 必要
- 役割: 指定した `memory_set` の詳細を返す

### `PUT /api/config/memory-sets/{memory_set_id}`

- 認証: 必要
- 役割: 指定した `memory_set` を全体置換で作成または更新する

request body の最小 shape:

```json
{
  "memory_set_id": "memory_set:default",
  "display_name": "Default Memory",
  "description": "Empty starter memory set for the MVP slice."
}
```

### `DELETE /api/config/memory-sets/{memory_set_id}`

- 認証: 必要
- 役割: 指定した `memory_set` を削除する

### `PUT /api/config/model-presets/{model_preset_id}`

- 認証: 必要
- 役割: 指定したモデルプリセット定義を全体置換する

request body の最小 shape:

```json
{
  "model_preset_id": "model_preset:default",
  "display_name": "Default Mock Preset",
  "roles": {
    "reply_generation": {
      "model_profile_id": "model_profile:mock_reply"
    },
    "decision_generation": {
      "model_profile_id": "model_profile:mock_decision"
    },
    "recall_hint_generation": {
      "model_profile_id": "model_profile:mock_recall"
    },
    "memory_interpretation": {
      "model_profile_id": "model_profile:mock_memory"
    },
    "embedding": {
      "model_profile_id": "model_profile:mock_embedding"
    }
  }
}
```

response:

```json
{
  "ok": true,
  "data": {
    "model_preset": {}
  }
}
```

### `GET /api/config/model-presets/{model_preset_id}`

- 認証: 必要
- 役割: 指定した `model_preset` の詳細を返す

### `DELETE /api/config/model-presets/{model_preset_id}`

- 認証: 必要
- 役割: 指定した `model_preset` を削除する

### `PUT /api/config/model-profiles/{model_profile_id}`

- 認証: 必要
- 役割: 指定したモデルプロファイル定義を全体置換する

request body の最小 shape:

```json
{
  "model_profile_id": "model_profile:mock_reply",
  "kind": "generation",
  "provider": "mock",
  "model_name": "mock-reply"
}
```

response:

```json
{
  "ok": true,
  "data": {
    "model_profile": {}
  }
}
```

### `GET /api/config/model-profiles/{model_profile_id}`

- 認証: 必要
- 役割: 指定した `model_profile` の詳細を返す

### `DELETE /api/config/model-profiles/{model_profile_id}`

- 認証: 必要
- 役割: 指定した `model_profile` を削除する

設定面の主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_wake_policy` | `wake_policy` が object ではない |
| `400` | `invalid_wake_policy_mode` | `wake_policy.mode` が不正 |
| `400` | `invalid_interval_minutes` | `interval_minutes` が 1 未満または整数でない |
| `400` | `invalid_memory_enabled` | `memory_enabled` が boolean ではない |
| `400` | `invalid_desktop_watch` | `desktop_watch` が object ではない |
| `400` | `invalid_desktop_watch_enabled` | `desktop_watch.enabled` が boolean ではない |
| `400` | `invalid_desktop_watch_interval_seconds` | `desktop_watch.interval_seconds` が 1 未満または整数でない |
| `400` | `invalid_desktop_watch_target_client_id` | `desktop_watch.target_client_id` が不正 |
| `400` | `persona_id_mismatch` | path と body の `persona_id` が一致しない |
| `400` | `memory_set_id_mismatch` | path と body の `memory_set_id` が一致しない |
| `400` | `model_preset_id_mismatch` | path と body の `model_preset_id` が一致しない |
| `400` | `model_profile_id_mismatch` | path と body の `model_profile_id` が一致しない |
| `400` | `invalid_editor_state_current` | `editor-state.current` が object ではない |
| `400` | `missing_personas` | `editor-state.personas` が空 |
| `400` | `missing_memory_sets` | `editor-state.memory_sets` が空 |
| `400` | `missing_model_presets` | `editor-state.model_presets` が空 |
| `400` | `missing_model_profiles` | `editor-state.model_profiles` が空 |
| `400` | `missing_model_role` | 必須 role が不足している |
| `400` | `model_profile_kind_mismatch` | role と profile kind が一致しない |
| `404` | `persona_not_found` | 指定した人格が存在しない |
| `404` | `memory_set_not_found` | 指定した記憶が存在しない |
| `404` | `model_preset_not_found` | 指定したプリセットが存在しない |
| `404` | `model_profile_not_found` | 指定したプロファイルが存在しない |
| `409` | `selected_persona_delete_forbidden` | 選択中の `persona` を削除しようとした |
| `409` | `selected_memory_set_delete_forbidden` | 選択中の `memory_set` を削除しようとした |
| `409` | `selected_model_preset_delete_forbidden` | 選択中の `model_preset` を削除しようとした |
| `409` | `model_profile_in_use` | `model_profile` が `model_preset` から参照されている |

<!-- Block: Catalog -->
## 列挙面

### `GET /api/catalog`

- 認証: 必要
- 役割: 選択可能な人格、記憶、モデル設定資源の一覧を返す

response:

```json
{
  "ok": true,
  "data": {
    "personas": [
      {
        "persona_id": "persona:default",
        "display_name": "Default Persona"
      }
    ],
    "memory_sets": [
      {
        "memory_set_id": "memory_set:default",
        "display_name": "Default Memory"
      }
    ],
    "model_presets": [
      {
        "model_preset_id": "model_preset:default",
        "display_name": "Default Mock Preset"
      }
    ],
    "model_profiles": [
      {
        "model_profile_id": "model_profile:mock_reply",
        "display_name": "Mock Reply"
      }
    ]
  }
}
```

<!-- Block: Inspection -->
## inspection 面

### `GET /api/inspection/cycle-summaries?limit=<n>`

- 認証: 必要
- 役割: 最近の `cycle_summary` 一覧を返す
- `limit` は省略時 `20`

response:

```json
{
  "ok": true,
  "data": {
    "cycle_summaries": [
      {
        "cycle_id": "cycle:...",
        "server_id": "server:...",
        "trigger_kind": "user_message",
        "started_at": "2026-03-31T00:00:00+00:00",
        "finished_at": "2026-03-31T00:00:00+00:00",
        "selected_persona_id": "persona:default",
        "selected_memory_set_id": "memory_set:default",
        "selected_model_preset_id": "model_preset:default",
        "result_kind": "reply",
        "failed": false
      }
    ]
  }
}
```

### `GET /api/inspection/cycles/{cycle_id}`

- 認証: 必要
- 役割: 指定した `cycle_id` の段階トレースを返す

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "cycle_summary": {},
    "observation_trace": {},
    "recall_trace": {},
    "decision_trace": {},
    "result_trace": {}
  }
}
```

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `404` | `cycle_not_found` | 指定した `cycle_id` が存在しない |

<!-- Block: CurrentBoundary -->
## 現時点の境界

現時点で正本として定めるのは、上記の path、method、認証、JSON 形式である。
内部フローや保存先の exact な shape は、この文書の対象に含めない。
