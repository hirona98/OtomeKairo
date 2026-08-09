# 列挙とinspection

## この文書の境界

この文書は、列挙 API と inspection API の path、method、認証、request / response、error code を正本にする。
capability availability の意味規則は [../capability/capability_manifest.md](../capability/capability_manifest.md)、段階トレースの意味規則は [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md) を正とする。
この文書では、各 endpoint の top-level field と wire 上の enum だけを定める。

## 列挙面

### `GET /api/catalog`

- 認証: 必要
- 役割: 人格設定、記憶集合、モデルプリセットの一覧を返す
- capability manifest 一覧と capability availability は返さない
- capability manifest と capability availability の境界は [../capability/capability_manifest.md](../capability/capability_manifest.md) を正とする

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

### `GET /api/docs`

- 認証: 必要
- 役割: CocoroConsole などの UI が表示するために OtomeKairo が選定したプレーンテキスト文書一式を返す
- `docs/` 全体の任意読み取り API ではない
- 初期対象は会話 API と API起床の Console 表示用文書だけとする
- 表示用文書は利用者向け説明であり、API wire 契約の正本は `docs/design/api/` 配下の該当文書とする
- 外部から呼び出す endpoint の基点は `{BASE_URL}` と表記する
- CocoroConsole は接続設定のOtomeKairo URL、ブラウザ UI はアクセス中の origin で `{BASE_URL}` を表示時に置換する
- response に token、API key、credential、内部 URL、絶対パスを含めない

response:

```json
{
  "ok": true,
  "data": {
    "document_set_id": "console_docs",
    "title": "OtomeKairo Docs",
    "format": "plain_text",
    "sections": [
      {
        "section_id": "conversation",
        "title": "会話API",
        "body_text": "会話API\n======\n..."
      },
      {
        "section_id": "wake",
        "title": "API起床",
        "body_text": "API起床\n======\n..."
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
          "last_prompted_observation_summary": null,
          "last_prompted_at": null
        }
      ],
      "memory_postprocess_runtime_state": {},
      "visual_daily_runtime_state": {},
      "audio_runtime_state": {
        "available": true,
        "unavailable_reason": null,
        "model_ids": {
          "silero_vad": "silero-vad-v5",
          "wespeaker": "wespeaker-resnet34-voxceleb-v1"
        },
        "configured_source": "local_microphone",
        "effective_source": "local_microphone",
        "stt_enabled": true,
        "selected_avatar_id": "avatar:default",
        "response_client_id": "console-main",
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
        "normal_activation": {
          "state": "waiting",
          "active_until": null
        },
        "vad": {
          "speaking": false,
          "probability": 0.03,
          "dbfs": -42.1
        },
        "queue": {
          "processing": null,
          "waiting": []
        },
        "enrollment": null,
        "last_utterance_result": null
      },
      "pending_capability_requests": [],
      "autonomous_runs": []
    },
    "current_state": {
      "foreground_world_states": [],
      "activity_contexts": [],
      "drive_states": [],
      "ongoing_action": null,
      "autonomous_runs": [],
      "pending_intent_candidates": [],
      "mood_state": {},
      "affect_states": [],
      "entity_registry": [],
      "relation_index": [
        {
          "relation_index_id": "relation_index:...",
          "source_ref": "self",
          "target_ref": "person:tanaka",
          "relation_predicate": "trusts",
          "derived_status": "active",
          "confidence": 0.84,
          "salience": 0.72,
          "last_evidence_at": "2026-03-31T08:50:00+09:00",
          "supporting_memory_unit_count": 2,
          "supporting_memory_link_count": 1,
          "representative_summary": "田中さんを信頼している。"
        }
      ],
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
`activity_contexts` は人物参照ごとの現在活動を配列で返す。
`drive_states`、`autonomous_runs`、`ongoing_action`、`mood_state`、`affect_states` は、現在の個を構成する内部状態の確認用 snapshot である。
`current_state.entity_registry` は、選択中 `memory_set` の固有対象正規化を確認する compact snapshot である。
`current_state.entity_registry` は `entity_ref / entity_type / display_name / aliases / first_seen_at / last_seen_at / confidence / salience / evidence_event_count / supporting_memory_unit_count` を返す。
`current_state.entity_registry` は読み取り専用であり、対象の性格、好み、属性、関係本文を含めない。
`current_state.relation_index` は、選択中 `memory_set` の完成済み関係索引を最大 20 件返す compact snapshot である。
`current_state.relation_index` は `relation_index_id / source_ref / target_ref / relation_predicate / derived_status / confidence / salience / last_evidence_at / supporting_memory_unit_count / supporting_memory_link_count / representative_summary` を返す。
`current_state.relation_index` は読み取り専用であり、支持元の本文や revision 本文を含めない。
`runtime_detail` は scheduler、memory postprocess、visual daily worker、capability request 待ち、due `autonomous_run` のような runtime state を返す。
`runtime_detail.audio_runtime_state` は音声 model、connector、入力リース、VAD、発話キュー、話者登録、直近発話結果の process-local snapshot を返す。
音声 runtime state の意味と必須情報は [../audio/音声入力と話者識別.md](../audio/音声入力と話者識別.md) を正とする。
`last_utterance_result` は直近 1 件だけを持ち、文字起こし、音声、表示名、embedding を含めない。
通常発話の `last_utterance_result` は `top1_similarity` / `top2_similarity` / `threshold_met` を持つ。
`threshold_met` は処理時の `speaker_recognition_threshold` に対する top1 超過だけを表し、最終受理判定とは独立する。
話者識別不成立では `speaker_candidates` に top1 / top2 の `person_ref / similarity` を含める。
過去の発話結果一覧は返さない。
`runtime_detail.autonomous_runs` と `current_state.autonomous_runs` は `run_id / status / objective_summary / current_step_summary / history_summary / next_run_at / waiting_request_id / pause_reason / created_at / updated_at / completed_at` の要約を返す。
`runtime_detail.wake_policy_observations` は現在設定されている `wake_policy.observations` と process-local の直近実行結果を照合した snapshot である。
`runtime_detail.wake_runtime_state.initial_delay_until` は、visual capture を有効化した直後の初回 5 秒待機が残っている間だけ入る。
`runtime_detail.wake_runtime_state.retry_after` は、思考前観測 の一時失敗後に interval を消費せず短く再試行する時刻を表す。
各項目は `enabled / vision_source_id / interval_seconds / last_run_at / last_status / last_summary / last_error` を返す。
visual observation では、比較入力と発話済み観測の追跡用に `last_observation_signature / same_observation_count / last_prompted_observation_summary / last_prompted_at` も返す。
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
inspection の短縮表示は表示専用であり、LLM 入力、検索 index、永続正本には使わない。

### `GET /api/inspection/memory-snapshot`

- 認証: 必要
- 役割: 選択中 `memory_set` の継続理解（`memory_units`）と経験（`episodes`）を、人間向け compact 要約として返す
- この response は読み取り専用の inspection 表示であり、記憶の編集面ではない
- embedding、raw event payload、evidence ID 列、秘密値は返さない
- query:
  - `unit_limit`（省略時 `12`、上限 `30`）
  - `episode_limit`（省略時 `8`、上限 `20`）
- `memory_units` は salience 降順
- `episodes` は open loop と salience を踏まえた recall 向け順

response:

```json
{
  "ok": true,
  "data": {
    "generated_at": "2026-03-31T09:00:00+09:00",
    "memory_set_id": "memory_set:default",
    "memory_units": [
      {
        "memory_unit_id": "memory_unit:...",
        "memory_type": "person_model",
        "summary_text": "田中さんとは落ち着いた距離感で話している。",
        "status": "active",
        "salience": 0.82,
        "confidence": 0.7,
        "scope_type": "person",
        "scope_key": "person:tanaka",
        "formed_at": "2026-03-30T12:00:00+09:00",
        "last_confirmed_at": "2026-03-31T08:00:00+09:00",
        "updated_at": "2026-03-31T08:00:00+09:00"
      }
    ],
    "episodes": [
      {
        "episode_id": "episode:...",
        "episode_type": "interaction",
        "summary_text": "作業の合間に近況を聞かれた。",
        "outcome_text": "次も様子を見ることにした。",
        "salience": 0.6,
        "formed_at": "2026-03-31T08:50:00+09:00",
        "primary_scope_type": "person",
        "primary_scope_key": "person:tanaka"
      }
    ]
  }
}
```

### `GET /api/inspection/capabilities`

- 認証: 必要
- 役割: server が manifest、binding、`capability_state`、権限から導出した現在の capability availability を返す
- この response は capability availability の外向き確認正本である
- この endpoint は HTTP API 仕様である
- `capabilities` は server が知っている manifest を基準に並べる
- `rejected_bindings` は、接続 client が `hello.caps` で提示したが server が binding として受理しなかった候補を返す
- token、credential、内部 URL、transport 詳細は返さない
- availability 判定の入力と decision view との関係は [../capability/capability_manifest.md](../capability/capability_manifest.md) を正とする

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
          "eligible_client_count": 2,
          "bound_client_ids": ["console-...", "tapo-c220-connector-main"]
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
            "source_owner": "user_environment",
            "supported_controls": {},
            "unavailable_reason": null
          },
          {
            "vision_source_id": "vision_source:room_camera",
            "kind": "camera",
            "label": "部屋のカメラ",
            "default_for": ["visual", "camera"],
            "available": true,
            "required_permissions": ["observe_vision", "observe_camera"],
            "source_owner": "self",
            "wake_observation": {
              "observation_id": "observation:room_camera",
              "enabled": true
            },
            "supported_controls": {
              "camera.ptz": {
                "operations": ["move_up", "move_down", "move_left", "move_right"],
                "amounts": ["small", "medium"]
              }
            },
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
      },
      {
        "capability_id": "camera.ptz",
        "manifest_version": "1",
        "kind": "action",
        "available": true,
        "unavailable_reason": null,
        "readiness": {
          "family": "camera_control",
          "world_state_type": "visual_context",
          "input_keys": ["vision_source_id", "operation", "amount"],
          "result_summary_keys": ["status", "operation", "amount"]
        },
        "binding": {
          "status": "bound",
          "eligible_client_count": 1,
          "bound_client_ids": ["tapo-c220-connector-main"]
        },
        "permissions": {
          "required": ["control_camera_ptz"],
          "missing": []
        },
        "vision_sources": [
          {
            "vision_source_id": "vision_source:room_camera",
            "kind": "camera",
            "label": "部屋のカメラ",
            "default_for": ["visual", "camera"],
            "available": true,
            "required_permissions": ["observe_vision", "observe_camera"],
            "source_owner": "self",
            "supported_controls": {
              "camera.ptz": {
                "operations": ["move_up", "move_down", "move_left", "move_right"],
                "amounts": ["small", "medium"]
              }
            },
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
| `camera_source_disabled` | 採用済み camera source が無効である |
| `no_vision_source` | 対象となる視覚 source がない |
| `no_supported_control` | 対象 camera source に対応制御がない |
| `no_mcp_tool` | 対象 MCP tool がない |

`readiness` は manifest 由来の family 前提条件であり、`family / world_state_type / input_keys / result_summary_keys / result_item_keys` を持つ。
`readiness` は token、credential、内部 URL、transport 詳細を含まない。
`vision_sources[].supported_controls` は source が advertised した action capability の対応操作だけを返す。
`supported_controls` は credential、内部 URL、機器 API 名、角度を含まない。
`kind=camera` かつ `source_owner=self` の source は、対応する `camera_source_status` を返す。
対応する camera source が無効の場合、source または capability の `unavailable_reason` に `camera_source_disabled` を出す。

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
- `limit` は省略時 `20`、サーバは `1` 以上 `100` 以下に clamp する
- `started_at` / `finished_at` は OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す
- ブラウザ UI は同一処理を `GET /ui/api/inspection/cycle-summaries` 経由で呼ぶ
- `GET /ui/api/inspection/cycle-summaries` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない

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
        "failed": false,
        "input_summary": "君はどう？",
        "outcome_summary": "私は特に変わったこともなく、こうして落ち着いてお話しできていることが何よりです。",
        "reason_summary": "マスターから自身の近況を改めて問われており、対話の流れとして誠実かつ簡潔に自身の平穏な状態を伝えるのが適切である。"
      }
    ]
  }
}
```

`input_summary` は入力やきっかけの短い本文、`outcome_summary` は発話本文・能力要求・失敗理由などの短い結果本文、`reason_summary` はなぜその結果にしたかの短い判断理由である。
一覧から「何を受けて何をし、なぜそうしたか」を読むための俯瞰用 field であり、長い機械 ID や raw payload は含めない。
`outcome_summary` と `reason_summary` は分けて返す。発話がある場合も `reason_summary` を落とさない。
意味と含有方針は [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md) の `cycle_summary` を正とする。

### `GET /api/inspection/cycles/{cycle_id}`

- 認証: 必要
- 役割: 指定した `cycle_id` の段階トレースを返す
- 含まれる timestamp 系フィールドは OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す
- ブラウザ UI の判断画面は同一 wire を `GET /ui/api/inspection/cycles/{cycle_id}` 経由で読む
- `GET /ui/api/inspection/cycles/{cycle_id}` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない

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
各 trace object の意味と標準的な含有内容は [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md) を正とする。
機能ごとの追加 field は、それぞれの設計文書を正とする。
`world_state_trace` には、sanitized context summary に加えて `source_pack_state_type_hooks`、`normalized_candidate_policies`、`replaced_state_count` を含める。
`world_state_trace.source_pack_state_type_hooks.schedule` には、必要な場合に `pending_intent_slot_key` に加えて `real_schedule_slot_count / schedule_slot_keys` を含める。
`world_state_trace.normalized_candidate_policies` では、`schedule:self` と `schedule:<slot_key>` の両方を比較でき、real schedule slot 由来の candidate では `ttl_capped_by = schedule_slot.expires_at` を含める。
trigger をまたいだ比較用に、`result_trace.trigger_compact_summary` に共通 outer shape の compact summary を含める。
capability dispatch が起きた cycle では、`result_trace.capability_dispatch_summary` に capability family 共通で比較しやすい compact summary を含める。
`trigger_kind=capability_result` の cycle では、`result_trace.capability_result_followup_summary` に capability family 共通で比較しやすい compact summary を含める。
initiative 系 trigger の `entry_summary.candidate_families` field 契約は [../runtime/自律initiative_loop.md](../runtime/自律initiative_loop.md) を正とする。
exact answer 系の cycle では、`recall_trace` に `answer_contract`、`evidence_pack`、`fact_resolution_trace` を含める。
`fact_resolution_trace` は wire 上で少なくとも `query`、`selected_recall_sections`、`boundary_event_candidates`、`cycle_event_candidates`、`statement_event_candidates`、`adopted_evidence_items`、`consistency_checks` を持つ。

| trace | 詳細正本 |
|-------|----------|
| `input_trace` | [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)、[../runtime/自律initiative_loop.md](../runtime/自律initiative_loop.md)、[../llm/保留意図候補のLLM選別.md](../llm/保留意図候補のLLM選別.md) |
| `world_state_trace` | [../runtime/world_state.md](../runtime/world_state.md) |
| `activity_trace` | [../runtime/activity_state.md](../runtime/activity_state.md) |
| `recall_trace` | [../memory/想起と判断.md](../memory/想起と判断.md)、[../memory/event_evidenceのLLM圧縮.md](../memory/event_evidenceのLLM圧縮.md)、[../memory/RecallPackのLLM選別.md](../memory/RecallPackのLLM選別.md) |
| `decision_trace` | [../runtime/判断と行動.md](../runtime/判断と行動.md)、[../runtime/自律initiative_loop.md](../runtime/自律initiative_loop.md) |
| `result_trace` | [../runtime/判断と行動.md](../runtime/判断と行動.md)、[../capability/capability_manifest.md](../capability/capability_manifest.md) |
| `memory_trace` | [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)、[../memory/記憶更新と再整理.md](../memory/記憶更新と再整理.md)、[../memory/内省要約のLLM生成.md](../memory/内省要約のLLM生成.md) |

### `GET /api/inspection/cycles/{cycle_id}/cognitive-context`

- 認証: 必要
- 役割: 指定した `cycle_id` の前景化と派生 cognitive view を判断詳細表示向けに返す
- この endpoint は `cycle_trace.decision_trace` から inspection 用の派生 view だけを取り出す
- 返却内容は正本状態ではない
- ブラウザ UI は同一 wire を `GET /ui/api/inspection/cycles/{cycle_id}/cognitive-context` 経由で読む
- `GET /ui/api/inspection/cycles/{cycle_id}/cognitive-context` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "cycle_summary": {},
    "foreground_selection": {},
    "workspace_context_summary": {},
    "self_state_context": {},
    "relationship_context": {},
    "prediction_error_context": {},
    "default_mode_context": {}
  }
}
```

`foreground_selection` は判断時点で主役、補助、抑制へ分けた workspace candidate 参照である。
`workspace_context_summary` は候補盤面の要約であり、workspace 全量ではない。
`self_state_context`、`relationship_context`、`prediction_error_context`、`default_mode_context` は判断時点の派生 view であり、記憶、世界状態、感情状態の正本ではない。
存在しない派生 view は空 object として返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `404` | `cycle_not_found` | 指定した `cycle_id` が存在しない |

### `GET /api/logs/stream`

- 認証: 必要
- 役割: `CocoroConsole` のログビューアーと Web UI ログ画面向けに、`debug_log` の出力を WebSocket で流す
- client から送る message は不要
- 接続時には、直近の短いログを replay する
- `ts` は OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す
- 通常会話では、ユーザー入力と実際にユーザーへ表示する assistant 発話の短い抜粋を流す
- 会話本文の抜粋は最初の改行までを流し、それ以降の行を流さない
- `logs/stream` は `debug_log` の購読先として扱い、標準出力とログファイルに出る `LEVEL / Component / message` と同じ内容を `level / logger / msg` として流す
- `logs/stream` の `level / logger / msg` にはターミナル表示用の ANSI 色を含めない
- ブラウザ UI は同一 wire を `GET /ui/api/logs/stream` 経由で購読する
- `GET /ui/api/logs/stream` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない
- `GET /ui/api/logs/stream` は `Origin` と `Host` が一致する同一 origin の接続だけを受理する

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
