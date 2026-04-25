# 列挙とinspection

## 列挙面

### `GET /api/catalog`

- 認証: 必要
- 役割: 人格設定、記憶集合、モデルプリセットの一覧を返す
- 現時点では capability manifest 一覧は返さない。接続中 client の capability availability は `GET /api/events/stream` の `hello.caps` で扱う
- capability manifest は server 側の正本であり、catalog の選択肢とは分ける

response:

```json
{
  "ok": true,
  "data": {
    "personas": [
      {
        "persona_id": "persona:default",
        "display_name": "標準人格設定"
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
    ]
  }
}
```

## inspection 面

### `GET /api/inspection/cycle-summaries?limit=<n>`

- 認証: 必要
- 役割: 最近の `cycle_summary` 一覧を返す
- `limit` は省略時 `20`
- `started_at` / `finished_at` は OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す

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
        "started_at": "2026-03-31T09:00:00+09:00",
        "finished_at": "2026-03-31T09:00:00+09:00",
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
- 含まれる timestamp 系フィールドは OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "cycle_summary": {},
    "input_trace": {},
    "recall_trace": {},
    "decision_trace": {},
    "result_trace": {},
    "memory_trace": {}
  }
}
```

- `recall_trace` には `selected_memory_unit_ids`、`selected_episode_ids`、必要時だけ `selected_event_ids` を含む
- `recall_trace.event_evidence_generation` には `requested_event_count`、`loaded_event_count`、`succeeded_event_count`、`failed_items` を含む
- `recall_trace.event_evidence_generation.failed_items.*` には `event_id`、`kind`、`failure_stage`、`failure_reason` を含む
- `recall_trace.recall_pack_selection` には `candidate_section_counts`、`selected_section_order`、`selected_candidate_refs`、`dropped_candidate_refs`、`conflict_summary_count`、`result_status`、`failure_reason` を含む
- `input_trace` の観測要約には、capability ベースの観測時だけ `capability_id`、`image_count`、`image_interpreted` を含む
- `input_trace` の実行状態要約には `ongoing_action_exists` を含み、必要時だけ参照した `ongoing_action` の要約を含む
- `input_trace.pending_intent_selection` には `candidate_pool_count`、`eligible_candidate_count`、`selected_candidate_ref`、`selected_candidate_id`、`selection_reason`、`result_status`、`failure_reason` を含む
- `decision_trace` には、必要時だけ強く効いた `drive_state` と参照した `ongoing_action` の要約を含む
- `result_trace` には、必要時だけ capability 実行要求の要約と `ongoing_action` の作成 / 継続 / 完了 / 中断の要約を含む
- `result_trace.ongoing_action_transition_summary` には、必要時だけ `action_id`、`transition_sequence`、`final_state`、`goal_summary`、`step_summary`、`episode_series_id`、`last_capability_id`、`reason_summary` を含む
- `memory_trace` には 生成した `episode` の要約、`episode_series_id`、`open_loops`、`memory_units` 更新要約、感情更新要約を含む
- `memory_trace` には、必要時だけ `drive_state` 更新要約を含む
- `memory_trace.drive_state_update` と `reflective_consolidation.drive_state_update` には `result_status`、`active_drive_ids`、`removed_drive_ids`、`drive_summaries` を含む
- 感情更新要約には `episode_affect` 保存件数、`mood_state` 更新要約、必要時だけ `affect_state` 更新要約を含む
- `mood_state` 更新要約には、必要時だけ `baseline_vad / residual_vad / current_vad / confidence` を含む
- 当時の感情本文を追う場合は `mood_state` ではなく `episode_affect.summary_text` を見る
- `memory_trace` には `vector_index_sync.result_status` と `reflective_consolidation.result_status` を含め、同期保存済み部分と後段 job の進行を分けて追う
- `reflective_consolidation.summary_generation` には `requested_scope_count`、`succeeded_scope_count`、`failed_scopes` を含む
- `episode_series_id` と `open_loops` は追跡用の inspection 情報として扱い、通常の状態面や設定面では返さない

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `404` | `cycle_not_found` | 指定した `cycle_id` が存在しない |

### `GET /api/logs/stream`

- 認証: 必要
- 役割: `CocoroConsole` のログビューアー向けに、判断サイクルの短い段階要約ログを WebSocket で流す
- client から送る message は不要
- 接続時には、直近の短いログを replay する
- `ts` は OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す

message shape:

```json
[
  {
    "ts": "2026-04-06T09:00:00+09:00",
    "level": "INFO",
    "logger": "RecallStructured",
    "msg": "cf09b49a3ce1 memory_units=memory_unit:1234abcd episodes=episode:5678efgh"
  }
]
```

`logger` には少なくとも次を流す。

- `Observation`
- `RecallHint`
- `RecallStructured`
- `RecallAssociation`
- `RecallResult`
- `Decision`
- `Result`
- `Memory`

ここで流すのは live 表示向けの派生ログであり、inspection の正本ではない。
完全な prompt、生の LLM 応答全文、長い思考過程は流さない。
