# 列挙とinspection

<!-- Block: Role -->
## この文書の役割

この文書は、列挙面と inspection 面の API 仕様を定める。

ここで扱うのは次である。

- `GET /api/catalog`
- `GET /api/inspection/cycle-summaries`
- `GET /api/inspection/cycles/{cycle_id}`

共通ルールは `00_API仕様ガイド.md` を正とする。

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
        "display_name": "Default OpenRouter Gemini Preset"
      }
    ],
    "model_profiles": [
      {
        "model_profile_id": "model_profile:gemini_reply",
        "display_name": "OpenRouter Gemini Reply"
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
