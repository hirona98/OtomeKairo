# JSONデータ仕様

<!-- Block: Purpose -->
## このドキュメントの役割

- このドキュメントは、SQLite の JSON 列と Web API の JSON 本文を、オブジェクト単位で固定する正本である
- 目的は、`payload_json`、`payload_ref_json`、Web API の入出力本文の中身を、実装前に曖昧にしないことにある
- テーブル名と保存境界は `docs/34_SQLite論理スキーマ.md` を見る
- エンドポイントの意味、HTTP method、`SSE` の接続方法は `docs/35_WebAPI仕様.md` を見る
- `memory_jobs` の責務と payload の意味は `docs/33_記憶ジョブ仕様.md` を見る
- JSON のキー、型、必須項目で迷ったら、このドキュメントを正本として扱う

<!-- Block: Scope -->
## このドキュメントで固定する範囲

- 固定するのは、初期実装で使う JSON オブジェクトのキー、型、必須項目、固定語彙である
- 固定するのは、`pending_inputs.payload_json`、`settings_overrides.requested_value_json`、`ui_outbound_events.payload_json`、`memory_jobs.payload_ref_json`、`memory_job_payloads.payload_json`、主要な Web API 本文である
- 固定しないのは、Python のクラス名、Pydantic モデル名、OpenAPI の自動生成細部である
- 固定しないのは、将来追加する未使用フィールドや後段の拡張イベント種別である

<!-- Block: Common Rules -->
## 共通ルール

<!-- Block: Json Shape -->
### JSON の基本形

- JSON のキーは、すべて `snake_case` に統一する
- ここで定義する JSON のルートは、すべてオブジェクトに固定する
- 必須項目は常に出現させる
- 任意項目は、値がないときに `null` を入れず、省略する
- 未定義キーは受け付けない
- 時刻は、原則として UTC unix milliseconds の `integer` に固定する
- ID は、`ui_event_id` と `last_commit_id` を除き、不透明な `string` に固定する
- 配列は順序を持つものとして扱い、書き込み側で順序を安定化させる

<!-- Block: Fixed Vocab -->
### 固定語彙の扱い

- 種別や状態は、列側の固定語彙と同じ `string` をそのまま使う
- 真偽値は、JSON では `true / false` を使う
- 数値の比較に使うカウンタや添字は、`integer` に固定する
- 自由文は、空文字列を有効値として使わず、内容がない場合は項目自体を省略する

<!-- Block: Shared Objects -->
## 共通オブジェクト

<!-- Block: Payload Ref -->
### `payload_ref_json`

- `payload_ref_json` は、少なくとも `payload_kind`、`payload_id`、`payload_version` を持つ
- `payload_ref_json` は、`input_journal.payload_ref_json` と `memory_jobs.payload_ref_json` で共通の形を使う

```json
{
  "payload_kind": "memory_job_payload",
  "payload_id": "mjp_...",
  "payload_version": 1
}
```

- `payload_kind` は、参照先の分類を示す `string` である
- `payload_id` は、参照先レコードの主キーと一致する `string` である
- `payload_version` は、参照先 JSON の版を示す `integer` である
- `memory_jobs.payload_ref_json.payload_kind` は、初期段階では `memory_job_payload` に固定する

<!-- Block: Error Body -->
### エラー応答 JSON

```json
{
  "error_code": "invalid_request",
  "message": "channel must be browser_chat",
  "request_id": "req_..."
}
```

- `error_code` は、機械判定用の固定語彙 `string` である
- `message` は、表示可能な短い説明文である
- `request_id` は、HTTP リクエスト単位で Web サーバが生成する追跡 ID である

<!-- Block: Control Plane Group -->
## 制御面テーブルの JSON

<!-- Block: Pending Inputs -->
### `pending_inputs.payload_json`

- `pending_inputs.payload_json` は、少なくとも `input_kind` を持つ
- 初期段階の `browser_chat` では、`chat_message` と `cancel` の 2 種だけを受け付ける

<!-- Block: Pending Chat Message -->
#### `chat_message`

```json
{
  "input_kind": "chat_message",
  "text": "おはよう",
  "client_message_id": "cli_msg_001"
}
```

- 必須項目は `input_kind`、`text` である
- `input_kind` は `chat_message` に固定する
- `text` は、空文字列や空白のみを許可しない
- `client_message_id` は任意で、同一クライアントからの再送判定に使う

<!-- Block: Pending Cancel -->
#### `cancel`

```json
{
  "input_kind": "cancel",
  "target_message_id": "msg_..."
}
```

- 必須項目は `input_kind` である
- `input_kind` は `cancel` に固定する
- `target_message_id` は任意で、省略時は現在の `browser_chat` 応答全体を対象にしてよい

<!-- Block: Settings Requested Value -->
### `settings_overrides.requested_value_json`

- `settings_overrides.requested_value_json` は、要求値そのものではなく、型付きの正規化オブジェクトで保持する
- `POST /api/settings/overrides` の `requested_value` は、Web サーバでこの形へ正規化してから保存する

```json
{
  "value_type": "string",
  "value": "openrouter/.../model"
}
```

- 必須項目は `value_type`、`value` である
- `value_type` は、少なくとも `string`、`integer`、`number`、`boolean`、`object`、`array` を区別する
- `value` は、`value_type` と整合する JSON 値をそのまま持つ
- 初期段階での主要ユースケースは `string` だが、型変換の推測は行わない

<!-- Block: UI Outbound -->
### `ui_outbound_events.payload_json`

- `ui_outbound_events.payload_json` は、`event_type` ごとに固定したオブジェクト形を使う
- `GET /api/chat/stream` の `data:` には、この JSON をそのまま 1 行で流す

<!-- Block: UI Token -->
#### `event_type = token`

```json
{
  "message_id": "msg_...",
  "text": "お",
  "chunk_index": 0,
  "is_final_chunk": false
}
```

- 必須項目は `message_id`、`text`、`chunk_index` である
- `chunk_index` は、0 始まりの連番 `integer` とする
- `is_final_chunk` は任意で、最後の断片だけ `true` を付けてよい

<!-- Block: UI Message -->
#### `event_type = message`

```json
{
  "message_id": "msg_...",
  "role": "assistant",
  "text": "おはようございます。",
  "created_at": 1760000000000,
  "source_cycle_id": "cycle_...",
  "related_input_id": "inp_..."
}
```

- 必須項目は `message_id`、`role`、`text`、`created_at` である
- `role` は、少なくとも `assistant`、`system_notice` を区別する
- `source_cycle_id`、`related_input_id` は任意である

<!-- Block: UI Status -->
#### `event_type = status`

```json
{
  "status_code": "thinking",
  "label": "応答を組み立てています",
  "cycle_id": "cycle_..."
}
```

- 必須項目は `status_code`、`label` である
- `status_code` は、少なくとも `idle`、`thinking`、`speaking`、`waiting_external` を区別する
- `cycle_id` は任意で、特定サイクルに紐づく更新だけに付ける

<!-- Block: UI Notice -->
#### `event_type = notice`

```json
{
  "notice_code": "self_initiated_action",
  "text": "周囲の確認を開始します"
}
```

- 必須項目は `notice_code`、`text` である
- `notice_code` は、UI 側で分類できる固定語彙 `string` にする

<!-- Block: UI Error -->
#### `event_type = error`

```json
{
  "error_code": "runtime_unavailable",
  "message": "人格ランタイムに接続できません",
  "retriable": true
}
```

- 必須項目は `error_code`、`message` である
- `retriable` は任意で、再試行可能なときだけ付ける

<!-- Block: Memory Job Group -->
## 記憶ジョブの JSON

<!-- Block: Memory Job Payloads -->
### `memory_job_payloads.payload_json`

- `memory_job_payloads.payload_json` は、すべて共通ヘッダを持つ
- `payload_json.job_kind` は、対応する `memory_job_payloads.job_kind` と一致しなければならない
- `source_event_ids` は、順序を持つ非空配列とする

<!-- Block: Job Common Header -->
#### 共通ヘッダ

```json
{
  "job_kind": "write_memory",
  "cycle_id": "cycle_...",
  "source_event_ids": ["evt_..."],
  "created_at": 1760000000000,
  "idempotency_key": "write_memory:cycle_...:evt_..."
}
```

- 必須項目は `job_kind`、`cycle_id`、`source_event_ids`、`created_at`、`idempotency_key` である
- `source_event_ids` は空配列を許可しない

<!-- Block: Write Memory -->
#### `job_kind = write_memory`

```json
{
  "job_kind": "write_memory",
  "cycle_id": "cycle_...",
  "source_event_ids": ["evt_001", "evt_002"],
  "created_at": 1760000000000,
  "idempotency_key": "write_memory:cycle_...:evt_001:evt_002",
  "primary_event_id": "evt_001",
  "reflection_seed_ref": {
    "ref_kind": "event",
    "ref_id": "evt_001"
  },
  "event_snapshot_refs": [
    {
      "event_id": "evt_001",
      "event_updated_at": 1760000000000
    }
  ]
}
```

- 追加の必須項目は `primary_event_id`、`reflection_seed_ref`、`event_snapshot_refs` である
- `primary_event_id` は、`source_event_ids` のいずれかと一致しなければならない
- `reflection_seed_ref` は、少なくとも `ref_kind`、`ref_id` を持つ
- `event_snapshot_refs` の各要素は、少なくとも `event_id`、`event_updated_at` を持つ
- `event_snapshot_refs` は空配列を許可しない

<!-- Block: Refresh Preview -->
#### `job_kind = refresh_preview`

```json
{
  "job_kind": "refresh_preview",
  "cycle_id": "cycle_...",
  "source_event_ids": ["evt_001"],
  "created_at": 1760000000000,
  "idempotency_key": "refresh_preview:cycle_...:evt_001",
  "target_event_id": "evt_001",
  "target_event_updated_at": 1760000000000,
  "preview_reason": "event_updated"
}
```

- 追加の必須項目は `target_event_id`、`target_event_updated_at`、`preview_reason` である
- `preview_reason` は、少なくとも `event_created`、`event_updated`、`preview_missing` を区別する

<!-- Block: Quarantine Memory -->
#### `job_kind = quarantine_memory`

```json
{
  "job_kind": "quarantine_memory",
  "cycle_id": "cycle_...",
  "source_event_ids": ["evt_001"],
  "created_at": 1760000000000,
  "idempotency_key": "quarantine_memory:cycle_...:evt_001",
  "reason_code": "misretrieval_confirmed",
  "reason_note": "明示的に誤想起と確認された",
  "targets": [
    {
      "entity_type": "memory_state",
      "entity_id": "ms_...",
      "current_searchable": true
    }
  ]
}
```

- 追加の必須項目は `reason_code`、`reason_note`、`targets` である
- `reason_code` は、少なくとも `misretrieval_confirmed`、`stale_linkage`、`manual_quarantine` を区別する
- `targets` は空配列を許可しない
- `targets` の各要素は、少なくとも `entity_type`、`entity_id`、`current_searchable` を持つ
- `entity_type` は、少なくとも `event`、`memory_state`、`event_affect` を区別する

<!-- Block: Web Api Group -->
## Web API の JSON

<!-- Block: Settings Override Request -->
### `POST /api/settings/overrides` の入力 JSON

```json
{
  "key": "llm.default_model",
  "requested_value": "openrouter/.../model",
  "apply_scope": "runtime"
}
```

- 必須項目は `key`、`requested_value`、`apply_scope` である
- `key` は、ドット区切りの設定キー `string` に固定する
- `requested_value` は、`string`、`number`、`boolean`、`object`、`array` のいずれかを許可する
- `apply_scope` は、初期段階では `runtime`、`next_boot` を区別する

<!-- Block: Settings Override Response -->
### `POST /api/settings/overrides` の成功応答 JSON

```json
{
  "accepted": true,
  "override_id": "ovr_...",
  "status": "queued"
}
```

- 必須項目は `accepted`、`override_id`、`status` である
- `accepted` は `true` に固定する
- `status` は `queued` に固定する

<!-- Block: Chat Input Request -->
### `POST /api/chat/input` の入力 JSON

```json
{
  "text": "おはよう",
  "client_message_id": "cli_msg_001"
}
```

- 必須項目は `text` である
- `text` は、空文字列や空白のみを許可しない
- `client_message_id` は任意で、クライアント側の再送判定に使う

<!-- Block: Chat Input Response -->
### `POST /api/chat/input` の成功応答 JSON

```json
{
  "accepted": true,
  "input_id": "inp_...",
  "status": "queued",
  "channel": "browser_chat"
}
```

- 必須項目は `accepted`、`input_id`、`status`、`channel` である
- `accepted` は `true` に固定する
- `status` は `queued` に固定する
- `channel` は `browser_chat` に固定する

<!-- Block: Chat Cancel Request -->
### `POST /api/chat/cancel` の入力 JSON

```json
{
  "target_message_id": "msg_..."
}
```

- ルートオブジェクトは必須である
- `target_message_id` は任意で、省略時は現在のブラウザチャット応答全体を対象にしてよい

<!-- Block: Chat Cancel Response -->
### `POST /api/chat/cancel` の成功応答 JSON

```json
{
  "accepted": true,
  "status": "queued"
}
```

- 必須項目は `accepted`、`status` である
- `accepted` は `true` に固定する
- `status` は `queued` に固定する

<!-- Block: Status Response -->
### `GET /api/status` の成功応答 JSON

```json
{
  "server_time": 1760000000000,
  "runtime": {
    "is_running": true,
    "last_cycle_id": "cycle_...",
    "last_commit_id": 42
  },
  "self_state": {
    "current_emotion": {
      "v": 0.12,
      "a": 0.18,
      "d": 0.03,
      "labels": ["calm"]
    }
  },
  "attention_state": {
    "primary_focus": "browser_chat"
  },
  "task_state": {
    "active_task_count": 1,
    "waiting_task_count": 0
  }
}
```

- 必須項目は `server_time`、`runtime`、`self_state`、`attention_state`、`task_state` である
- `runtime` は、少なくとも `is_running`、`last_cycle_id`、`last_commit_id` を持つ
- `self_state.current_emotion` は、少なくとも `v`、`a`、`d`、`labels` を持つ
- `attention_state.primary_focus` は、表示用の短い `string` とする
- `task_state.active_task_count`、`task_state.waiting_task_count` は `integer` に固定する

<!-- Block: Stream Data -->
### `GET /api/chat/stream` の `data:` JSON

- `GET /api/chat/stream` の `data:` は、`ui_outbound_events.payload_json` と同一の JSON をそのまま使う
- `event_type` ごとの payload は、このドキュメントの `ui_outbound_events.payload_json` に従う
- Web サーバは、`data:` 用に別形式へ変換しない

<!-- Block: Fixed Decisions -->
## このドキュメントで確定したこと

- 制御面テーブルの JSON 列は、初期実装で使うキーと型をここで固定する
- `settings_overrides.requested_value_json` は、型付きの正規化オブジェクトで保持する
- `ui_outbound_events.payload_json` と `SSE data` は同一の JSON を使う
- `memory_job_payloads.payload_json` は、共通ヘッダと `job_kind` ごとの追加項目を持つ
- Web API の JSON 本文は、エンドポイントの意味を `docs/35_WebAPI仕様.md`、本文の形をこのドキュメントで分担して管理する
