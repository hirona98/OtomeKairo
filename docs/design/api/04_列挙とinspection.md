# 列挙とinspection

## この文書の境界

この文書は、列挙 API と inspection API の path、method、認証、request / response、error code を正本にする。
capability availability の意味規則は [../17_capability_manifest.md](../17_capability_manifest.md)、段階トレースの意味規則は [../13_デバッグ可能性.md](../13_デバッグ可能性.md) を正とする。
この文書では、各 endpoint の top-level field と wire 上の enum だけを定める。

## 列挙面

### `GET /api/catalog`

- 認証: 必要
- 役割: 人格設定、記憶集合、モデルプリセットの一覧を返す
- capability manifest 一覧と capability availability は返さない
- capability manifest と capability availability の境界は [../17_capability_manifest.md](../17_capability_manifest.md) を正とする

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

### `GET /api/inspection/current-state`

- 認証: 必要
- 役割: 現在の判断主体と runtime 前景を point-in-time snapshot として返す
- この response は「いま何が前景にあり、何が動いているか」を確認する inspection 正本である
- 含まれる timestamp 系フィールドは OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す
- raw payload、credential、token、内部 URL、長い画像/OCR 本文は返さない
- `pending_intent_candidates` と `pending_capability_requests` は process-local runtime state の snapshot であり、永続正本ではない

response:

```json
{
  "ok": true,
  "data": {
    "generated_at": "2026-03-31T09:00:00+09:00",
    "settings_snapshot": {},
    "runtime_summary": {},
    "runtime_detail": {
      "wake_runtime_state": {},
      "wake_policy_observations": [
        {
          "observation_id": "observation:main_desktop",
          "enabled": true,
          "capability_id": "vision.capture",
          "vision_source_id": "vision_source:console:desktop",
          "mode": "still",
          "interval_seconds": 60,
          "last_run_at": "2026-03-31T09:00:00+09:00",
          "last_status": "succeeded",
          "last_summary": "エディタが開いている",
          "last_error": null,
          "last_request_id": "vision_capture_request:...",
          "last_observation_signature": "vision_source_id=... | source_kind=... | source_label=... | visual_summary_text=...",
          "same_observation_count": 1,
          "last_prompted_at": null
        }
      ],
      "memory_postprocess_runtime_state": {},
      "visual_daily_runtime_state": {},
      "pending_capability_requests": []
    },
    "current_state": {
      "foreground_world_states": [],
      "activity_context": null,
      "drive_states": [],
      "ongoing_action": null,
      "pending_intent_candidates": [],
      "mood_state": {},
      "affect_states": [],
      "visual_daily_summary": null
    },
    "capability_inspection": {
      "capabilities": [],
      "rejected_bindings": []
    }
  }
}
```

`current_state.foreground_world_states` は現在有効な `world_state` の前景 snapshot を返す。
`activity_context`、`drive_states`、`ongoing_action`、`mood_state`、`affect_states` は、現在の個を構成する内部状態の確認用 snapshot である。
`runtime_detail` は scheduler、memory postprocess、visual daily worker、capability request 待ちのような process-local runtime state を返す。
`runtime_detail.wake_policy_observations` は現在設定されている `wake_policy.observations` と process-local の直近実行結果を照合した snapshot である。
`runtime_detail.wake_runtime_state.initial_delay_until` は、visual capture を有効化した直後の初回 5 秒待機が残っている間だけ入る。
`runtime_detail.wake_runtime_state.retry_after` は、起床前観測 の一時失敗後に interval を消費せず短く再試行する時刻を表す。
各項目は `enabled / vision_source_id / interval_seconds / last_run_at / last_status / last_summary / last_error` を返す。
visual observation では、process-local 変化判定用に `last_observation_signature / same_observation_count / last_prompted_at` も返す。
`last_*` は process-local runtime state であり、server restart をまたいで保持しない。
`capability_inspection` は `GET /api/inspection/capabilities` と同じ availability 導出結果を current-state snapshot の中で参照しやすく束ねたものである。

`runtime_detail.visual_daily_runtime_state` は、視覚日次整理 worker の process-local 状態を返す。
少なくとも `current_digest_id` を含める。
`current_state.visual_daily_summary` は、直近 digest の集計を返す。
少なくとも `latest_local_date / latest_digest_id / record_count / group_count / retained_count / compressed_count / memory_candidate_count` を含める。
`current_state.visual_daily_summary` は raw image、詳細な `detailed_summary_text`、OCR 全文を含めない。

digest 詳細を inspection で見る API は、認証必須の `GET /api/inspection/visual-digests` とする。
query は `limit` と `local_date` だけを受け付ける。
response は `daily_visual_digests` の compact 表示に限り、`group_summaries[].summary_text` は短縮した値だけを返す。

### `GET /api/inspection/capabilities`

- 認証: 必要
- 役割: server が manifest、binding、`capability_state`、権限から導出した現在の capability availability を返す
- この response は capability availability の外向き確認正本である
- この endpoint は現行設計が目指す HTTP API 仕様である。実装状態は `src/` と smoke 結果を正とする
- `capabilities` は server が知っている manifest を基準に並べる
- `rejected_bindings` は、接続 client が `hello.caps` で提示したが server が binding として受理しなかった候補を返す
- token、credential、内部 URL、transport 詳細は返さない
- availability 判定の入力と decision view との関係は [../17_capability_manifest.md](../17_capability_manifest.md) を正とする

response:

```json
{
  "ok": true,
  "data": {
    "generated_at": "2026-03-31T09:00:00+09:00",
    "capabilities": [
      {
        "capability_id": "vision.capture",
        "manifest_version": "1",
        "kind": "observation",
        "available": true,
        "unavailable_reason": null,
        "readiness": {
          "family": "visual_observation",
          "world_state_type": "visual_context",
          "input_keys": ["vision_source_id", "mode"],
          "result_summary_keys": ["visual_summary_text"]
        },
        "binding": {
          "status": "bound",
          "eligible_client_count": 1,
          "bound_client_ids": ["console-..."]
        },
        "permissions": {
          "required": ["observe_vision"],
          "missing": []
        },
        "vision_sources": [
          {
            "vision_source_id": "vision_source:main_display",
            "kind": "desktop",
            "label": "メイン画面",
            "default_for": ["visual", "desktop"],
            "available": true,
            "required_permissions": ["observe_desktop"],
            "unavailable_reason": null
          }
        ],
        "state": {
          "paused": false,
          "busy": false,
          "busy_request_id": null,
          "busy_action_id": null,
          "last_failure_at": null,
          "last_failure_summary": null,
          "last_result_at": null,
          "last_result_summary": null,
          "unavailable_active": false,
          "unavailable_reason": null,
          "unavailable_until": null,
          "parallel_blocked_by_action_id": null
        }
      }
    ],
    "rejected_bindings": [
      {
        "client_id": "console-...",
        "capability_id": "vision.capture",
        "offered_version": "0",
        "rejection_reason": "unsupported_version",
        "seen_at": "2026-03-31T09:00:00+09:00"
      }
    ]
  }
}
```

`unavailable_reason` は `available=true` のとき `null` である。
`available=false` のとき、`unavailable_reason` は次のいずれかである。

| 値 | 意味 |
|----|------|
| `no_binding` | 実行できる接続 client がない |
| `permission_denied` | 必要権限を満たす接続主体がない |
| `paused` | server 側で一時停止している |
| `busy` | 同じ capability が結果待ちである |
| `unavailable` | 動的一時 unavailable の理由が詳細化されていない |
| `dispatch_failed` | 直近の配送失敗により一時的に実行不可である |
| `request_timeout` | 直近の result timeout により一時的に実行不可である |
| `parallel_blocked` | 並列実行制限により実行不可である |

`readiness` は manifest 由来の family 前提条件であり、`family / world_state_type / input_keys / result_summary_keys / result_item_keys` を持つ。
`readiness` は token、credential、内部 URL、transport 詳細を含まない。

`binding.status` は次のいずれかである。

| 値 | 意味 |
|----|------|
| `bound` | 実行候補として受理済みの接続 client がある |
| `no_binding` | capability を実行できる接続 client がない |
| `rejected_only` | 提示された候補はあるが、すべて拒否された |

`rejected_bindings.rejection_reason` は次のいずれかである。

| 値 | 意味 |
|----|------|
| `unknown_capability` | server が知らない capability id である |
| `unsupported_version` | server が対応しない manifest version である |
| `permission_denied` | 接続主体が必要権限を満たさない |

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
        "result_kind": "speech",
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
    "world_state_trace": {},
    "activity_trace": {},
    "recall_trace": {},
    "decision_trace": {},
    "result_trace": {},
    "memory_trace": {}
  }
}
```

top-level の trace object は、存在しない段階でも空 object として返す。
各 trace object の意味と標準的な含有内容は [../13_デバッグ可能性.md](../13_デバッグ可能性.md) を正とする。
機能ごとの追加 field は、それぞれの設計文書を正とする。
`world_state_trace` には、sanitized context summary に加えて `source_pack_state_type_hooks`、`normalized_candidate_policies`、`replaced_state_count` を含める。
`world_state_trace.source_pack_state_type_hooks.schedule` には、必要な場合に `pending_intent_slot_key` に加えて `real_schedule_slot_count / schedule_slot_keys` を含める。
`world_state_trace.normalized_candidate_policies` では、`schedule:self` と `schedule:<slot_key>` の両方を比較でき、real schedule slot 由来の candidate では `ttl_capped_by = schedule_slot.expires_at` を含める。
trigger をまたいだ比較用に、`result_trace.trigger_compact_summary` に共通 outer shape の compact summary を含める。
capability dispatch が起きた cycle では、`result_trace.capability_dispatch_summary` に capability family 共通で比較しやすい compact summary を含める。
`trigger_kind=capability_result` の cycle では、`result_trace.capability_result_followup_summary` に capability family 共通で比較しやすい compact summary を含める。
initiative 系 trigger の `entry_summary.candidate_families` には、`reason_summary / blocking_reason_summary` を含める。capability 提案がある場合は `preferred_result_kind / preferred_result_reason_summary` を含める。
exact answer 系の cycle では、`recall_trace` に `answer_contract`、`evidence_pack`、`fact_resolution_trace` を含める。
`fact_resolution_trace` は wire 上で少なくとも `query`、`selected_recall_sections`、`boundary_event_candidates`、`cycle_event_candidates`、`statement_event_candidates`、`adopted_evidence_items`、`consistency_checks` を持つ。

| trace | 詳細正本 |
|-------|----------|
| `input_trace` | [../13_デバッグ可能性.md](../13_デバッグ可能性.md)、[../21_自律initiative_loop.md](../21_自律initiative_loop.md)、[../15_保留意図候補のLLM選別.md](../15_保留意図候補のLLM選別.md) |
| `world_state_trace` | [../22_world_state.md](../22_world_state.md) |
| `activity_trace` | [../28_activity_state.md](../28_activity_state.md) |
| `recall_trace` | [../memory/03_想起と判断.md](../memory/03_想起と判断.md)、[../memory/08_event_evidenceのLLM圧縮.md](../memory/08_event_evidenceのLLM圧縮.md)、[../memory/09_RecallPackのLLM選別.md](../memory/09_RecallPackのLLM選別.md) |
| `decision_trace` | [../05_判断と行動.md](../05_判断と行動.md)、[../21_自律initiative_loop.md](../21_自律initiative_loop.md) |
| `result_trace` | [../05_判断と行動.md](../05_判断と行動.md)、[../17_capability_manifest.md](../17_capability_manifest.md) |
| `memory_trace` | [../13_デバッグ可能性.md](../13_デバッグ可能性.md)、[../memory/04_記憶更新と再整理.md](../memory/04_記憶更新と再整理.md)、[../memory/07_内省要約のLLM生成.md](../memory/07_内省要約のLLM生成.md) |

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `404` | `cycle_not_found` | 指定した `cycle_id` が存在しない |

### `GET /api/logs/stream`

- 認証: 必要
- 役割: `CocoroConsole` のログビューアー向けに、`debug_log` の出力を WebSocket で流す
- client から送る message は不要
- 接続時には、直近の短いログを replay する
- `ts` は OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す
- 通常会話では、ユーザー入力と実際にユーザーへ表示する assistant 発話の短い抜粋を流す
- 会話本文の抜粋は最初の改行までを流し、それ以降の行を流さない
- `logs/stream` は `debug_log` の購読先として扱い、標準出力とログファイルに出る `LEVEL / Component / message` と同じ内容を `level / logger / msg` として流す
- `logs/stream` の `level / logger / msg` にはターミナル表示用の ANSI 色を含めない

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

`logger` は `debug_log` の component と一致させる。
ここで流すのはデバッグ表示向けログであり、inspection の正本ではない。
完全な prompt、生の LLM 応答全文、長い思考過程は流さない。
通常サーバ実行では、同じデバッグログを `OTOMEKAIRO_DATA_DIR/server.log` にも保存する。
ファイルログは容量上限付きでローテーションする。
