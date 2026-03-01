# SQLite論理スキーマ

<!-- Block: Purpose -->
## このドキュメントの役割

- このドキュメントは、`data/core.sqlite3` に置く初期スキーマを、テーブル単位で固定する正本である
- 目的は、`docs/30_システム設計.md`、`docs/31_ランタイム処理仕様.md`、`docs/32_記憶設計.md`、`docs/33_記憶ジョブ仕様.md` で決めた論理領域を、実装可能な保存単位へ落とすことにある
- Web API からどのテーブルをどう使うかは `docs/35_WebAPI仕様.md` を見る
- JSON 列の中身は `docs/36_JSONデータ仕様.md` を見る
- 初回 seed と排他起動の前提は `docs/37_起動初期化仕様.md` を見る
- 入力重複とストリーム保持運用は `docs/38_入力ストリーム運用仕様.md` を見る
- 実際の初期 SQL 文は `sql/core_schema.sql` に置く
- ここで固定するのは、テーブル名、主キー、必須列、主要制約、主要索引である
- ここで固定しないのは、実際の `CREATE TABLE` 文、migration 手順、SQLite pragma の全文である
- SQLite の物理実装で迷ったら、このドキュメントを正本として扱う

<!-- Block: Scope -->
## このドキュメントで固定する範囲

- 固定するのは、初期実装で必須となる正本テーブル、派生索引、制御面ログである
- 固定するのは、`SQLite` 上の論理型であり、Python 側の ORM モデル名やクラス名ではない
- `events.jsonl` は外部派生ログなので、このドキュメントの直接管理対象にしない
- `config/` 配下の設定ファイルは SQLite に入れない

<!-- Block: Common Rules -->
## 共通ルール

<!-- Block: Data Types -->
### 型と表記の固定

- ID 列は、原則として `TEXT` の不透明キーとする
- 時刻列は、原則として `INTEGER` の UTC unix milliseconds とする
- 状態値や種別値は、原則として `TEXT` の固定語彙で持つ
- 可変長の構造データは、`TEXT` の JSON として持つ
- 真偽値は、`INTEGER` の `0 / 1` で持つ
- 信頼度、重要度、記憶強度、VAD は、`REAL` で持つ

<!-- Block: Table Classes -->
### テーブルの分類

- append-only の正本ログは、`ui_outbound_events`、`input_journal`、`events`、`action_history`、`retrieval_runs`、`revisions`、`commit_records` とする
- 更新で育つテーブルは、`db_meta`、`runtime_leases`、`self_state`、`attention_state`、`body_state`、`world_state`、`drive_state`、`task_state`、`working_memory_items`、`recent_event_window_items`、`skill_registry`、`pending_inputs`、`settings_overrides`、`memory_states`、`preference_memory`、`event_affects`、`event_links`、`event_threads`、`state_links`、`event_entities`、`state_entities`、`event_preview_cache`、`memory_jobs`、`memory_job_payloads`、`vec_items` とする
- append-only テーブルは、論理削除でなく追記を基本とし、通常更新を前提にしない
- 例外として `event_preview_cache`、`memory_jobs`、`pending_inputs`、`settings_overrides` は更新を前提とする

<!-- Block: Reference Policy -->
### 参照と整合性の固定

- SQLite の外部キー制約は有効化する
- `event_id`、`memory_state_id`、`job_id`、`input_id` などの参照先は、存在しない値を入れない
- `cycle_id` は横断相関キーとして多くのテーブルに置くが、初期段階では専用の `cycles` テーブルは作らない
- `commit_records` が、短周期の正本確定を辿る主な起点になる
- `ui_outbound_events` は制御面ストリームログであり、コア状態の保存単位とは分離する

<!-- Block: Boot Meta Group -->
## 起動メタテーブル

<!-- Block: Db Meta -->
### `db_meta`

- 役割: スキーマ版と初期化完了情報を保持する
- 主キー: `meta_key TEXT PRIMARY KEY`
- 必須列: `meta_value_json`, `updated_at`
- `meta_key` は、初期段階では `schema_version`、`schema_name`、`initialized_at`、`initializer_version` を必須とする
- 主要索引: 主キーのみでよい

<!-- Block: Runtime Leases -->
### `runtime_leases`

- 役割: `人格ランタイム` の排他起動 lease を保持する
- 主キー: `lease_name TEXT PRIMARY KEY`
- 必須列: `owner_token`, `acquired_at`, `heartbeat_at`, `expires_at`
- `lease_name` は、初期段階では `primary_runtime` を使う
- 主要索引: `(expires_at ASC)`

<!-- Block: Runtime State Group -->
## ランタイム状態テーブル

<!-- Block: Singleton States -->
### 単一断面の状態テーブル

- `self_state`
  - 役割: 現在の人格断面を 1 件で保持する
  - 主キー: `row_id INTEGER PRIMARY KEY CHECK(row_id = 1)`
  - 必須列: `personality_json`, `current_emotion_json`, `long_term_goals_json`, `relationship_overview_json`, `invariants_json`, `updated_at`

- `attention_state`
  - 役割: 現在の注意断面を 1 件で保持する
  - 主キー: `row_id INTEGER PRIMARY KEY CHECK(row_id = 1)`
  - 必須列: `primary_focus_json`, `secondary_focuses_json`, `suppressed_items_json`, `revisit_queue_json`, `updated_at`

- `body_state`
  - 役割: 現在の身体断面を 1 件で保持する
  - 主キー: `row_id INTEGER PRIMARY KEY CHECK(row_id = 1)`
  - 必須列: `posture_json`, `mobility_json`, `sensor_availability_json`, `output_locks_json`, `load_json`, `updated_at`

- `world_state`
  - 役割: 現在の世界認識断面を 1 件で保持する
  - 主キー: `row_id INTEGER PRIMARY KEY CHECK(row_id = 1)`
  - 必須列: `location_json`, `situation_summary`, `surroundings_json`, `affordances_json`, `constraints_json`, `attention_targets_json`, `external_waits_json`, `updated_at`

- `drive_state`
  - 役割: 現在の内部欲求断面を 1 件で保持する
  - 主キー: `row_id INTEGER PRIMARY KEY CHECK(row_id = 1)`
  - 必須列: `drive_levels_json`, `priority_effects_json`, `updated_at`

<!-- Block: Task State -->
### `task_state`

- 役割: 継続タスク、保留タスク、再開条件の正本を保持する
- 主キー: `task_id TEXT PRIMARY KEY`
- 必須列: `task_kind`, `task_status`, `goal_hint`, `completion_hint_json`, `resume_condition_json`, `interruptible`, `priority`, `created_at`, `updated_at`
- 任意列: `title`, `step_hints_json`, `deadline_at`, `abandon_reason`
- `task_status` は、少なくとも `idle`、`active`、`waiting_external`、`paused`、`completed`、`abandoned` を区別する
- 主要索引: `(task_status, priority DESC, updated_at DESC)`

<!-- Block: Working Memory -->
### `working_memory_items`

- 役割: 現在サイクルの作業文脈をスロット単位で保持する
- 主キー: `slot_no INTEGER PRIMARY KEY`
- 必須列: `item_kind`, `summary_text`, `source_refs_json`, `updated_at`
- 任意列: `confidence`
- `slot_no` は、`0` からの連番で詰め、空き番を放置しない

<!-- Block: Recent Window -->
### `recent_event_window_items`

- 役割: 直近の生イベント列を順序付きで保持する
- 主キー: `window_pos INTEGER PRIMARY KEY`
- 必須列: `source_kind`, `source_id`, `summary_text`, `captured_at`, `updated_at`
- `source_kind` は、少なくとも `input_journal`、`event` を区別する
- `window_pos` は、`0` が最新とし、短周期ごとに並び替えて詰め直す

<!-- Block: Skill Registry -->
### `skill_registry`

- 役割: 再利用可能な行動列を保持する
- 主キー: `skill_id TEXT PRIMARY KEY`
- 必須列: `trigger_pattern_json`, `preconditions_json`, `action_pattern_json`, `success_signature_json`, `enabled`, `created_at`, `updated_at`
- 任意列: `summary_text`, `last_used_at`
- 主要索引: `(enabled, updated_at DESC)`

<!-- Block: Control Plane Group -->
## 制御面テーブル

<!-- Block: Pending Inputs -->
### `pending_inputs`

- 役割: Web サーバや外部入力から入った未処理入力を保持する
- 主キー: `input_id TEXT PRIMARY KEY`
- 必須列: `source`, `channel`, `payload_json`, `created_at`, `priority`, `status`
- 任意列: `client_message_id`, `claimed_at`, `resolved_at`, `discard_reason`
- `status` は、少なくとも `queued`、`claimed`、`consumed`、`discarded` を区別する
- `channel` は、少なくとも `browser_chat` を区別する
- `client_message_id` は、`browser_chat` の重複受付防止に使う
- 主要制約: `UNIQUE(channel, client_message_id) WHERE client_message_id IS NOT NULL`
- 主要索引: `(status, priority DESC, created_at ASC)`

<!-- Block: Settings Overrides -->
### `settings_overrides`

- 役割: Web から入った設定変更要求を保持する
- 主キー: `override_id TEXT PRIMARY KEY`
- 必須列: `key`, `requested_value_json`, `apply_scope`, `created_at`, `status`
- 任意列: `claimed_at`, `resolved_at`, `reject_reason`
- `status` は、少なくとも `queued`、`claimed`、`applied`、`rejected` を区別する
- 主要索引: `(status, created_at ASC)`

<!-- Block: UI Outbound -->
### `ui_outbound_events`

- 役割: ブラウザ向けの応答トークン、応答完了、自発メッセージ、状態通知を append-only で保持する
- 主キー: `ui_event_id INTEGER PRIMARY KEY AUTOINCREMENT`
- 必須列: `channel`, `event_type`, `payload_json`, `created_at`
- 任意列: `source_cycle_id`
- `event_type` は、少なくとも `token`、`message`、`status`、`notice`、`error` を区別する
- `ui_event_id` は、`SSE` の `Last-Event-ID` にそのまま使える単調増加値とする
- 古い行の削除は append-only 運用の例外として、`docs/38_入力ストリーム運用仕様.md` の保持条件に従って stream janitor だけが行ってよい
- 主要索引: `(channel, ui_event_id ASC)`

<!-- Block: Event Group -->
## 観測・行動・コミットテーブル

<!-- Block: Input Journal -->
### `input_journal`

- 役割: 受理した観測や外部入力の不変ログを保持する
- 主キー: `journal_id TEXT PRIMARY KEY`
- 必須列: `observation_id`, `cycle_id`, `source`, `kind`, `captured_at`, `receipt_summary`, `payload_ref_json`, `created_at`
- `observation_id` は一意にし、二重追記を許さない
- 主要制約: `UNIQUE(observation_id)`
- 主要索引: `(cycle_id)`, `(source, captured_at DESC)`

<!-- Block: Action History -->
### `action_history`

- 役割: 実行した行動命令と結果を append-only で保持する
- 主キー: `result_id TEXT PRIMARY KEY`
- 必須列: `cycle_id`, `command_id`, `action_type`, `command_json`, `started_at`, `finished_at`, `status`
- 任意列: `failure_mode`, `observed_effects_json`, `raw_result_ref_json`, `adapter_trace_ref_json`
- `status` は、少なくとも `succeeded`、`failed`、`stopped` を区別する
- 主要索引: `(cycle_id)`, `(status, finished_at DESC)`

<!-- Block: Events -->
### `events`

- 役割: `input_journal` をもとに意味単位へ再構成したエピソード正本を保持する
- 主キー: `event_id TEXT PRIMARY KEY`
- 必須列: `cycle_id`, `created_at`, `source`, `kind`, `searchable`
- 任意列: `updated_at`, `observation_summary`, `action_summary`, `result_summary`, `payload_ref_json`, `input_journal_refs_json`
- `kind` は、少なくとも `observation`、`action`、`action_result`、`internal_decision`、`external_response` を区別する
- `searchable` は、`0 / 1` で持つ
- 主要索引: `(cycle_id)`, `(created_at DESC)`, `(searchable, created_at DESC)`, `(source, created_at DESC)`

<!-- Block: Commit Records -->
### `commit_records`

- 役割: 各短周期の確定差分と派生ログ同期状態の正本を保持する
- 主キー: `commit_id INTEGER PRIMARY KEY AUTOINCREMENT`
- 必須列: `cycle_id`, `committed_at`, `log_sync_status`, `commit_payload_json`
- 任意列: `last_log_sync_error`
- `cycle_id` は一意とし、同じ短周期の二重 commit を許さない
- `log_sync_status` は、少なくとも `pending`、`synced`、`needs_replay` を区別する
- `commit_payload_json` には、`events.jsonl` 再生成に必要な確定差分の要点だけを保持する
- 主要制約: `UNIQUE(cycle_id)`
- 主要索引: `(log_sync_status, committed_at ASC)`

<!-- Block: Memory Group -->
## 記憶本体テーブル

<!-- Block: Memory States -->
### `memory_states`

- 役割: 育つ知識、要約、反省、長期感情を保持する主テーブルである
- 主キー: `memory_state_id TEXT PRIMARY KEY`
- 必須列: `memory_kind`, `body_text`, `payload_json`, `confidence`, `importance`, `memory_strength`, `searchable`, `last_confirmed_at`, `evidence_event_ids_json`, `created_at`, `updated_at`
- 任意列: `valid_from_ts`, `valid_to_ts`, `last_accessed_at`
- `memory_kind` は、少なくとも `fact`、`relation`、`task`、`summary`、`long_mood_state`、`reflection_note` を区別する
- 主要索引: `(memory_kind, searchable, updated_at DESC)`, `(searchable, last_confirmed_at DESC)`, `(last_accessed_at DESC)`

<!-- Block: Preference Memory -->
### `preference_memory`

- 役割: 好悪のような誤断定しやすい傾向を専用管理する
- 主キー: `preference_id TEXT PRIMARY KEY`
- 必須列: `owner_scope`, `target_entity_ref_json`, `domain`, `polarity`, `status`, `confidence`, `evidence_event_ids_json`, `created_at`, `updated_at`
- `owner_scope` は、少なくとも `self`、`other_entity` を区別する
- `polarity` は、`like`、`dislike` に固定する
- `status` は、`candidate`、`confirmed`、`revoked` に固定する
- 主要索引: `(owner_scope, status, updated_at DESC)`, `(domain, polarity, status)`

<!-- Block: Event Affects -->
### `event_affects`

- 役割: イベントに紐づく瞬間感情を保持する
- 主キー: `event_affect_id TEXT PRIMARY KEY`
- 必須列: `event_id`, `moment_affect_text`, `moment_affect_labels_json`, `vad_json`, `confidence`, `created_at`
- `event_id` は一意とし、1 イベント 1 件を基本とする
- `vad_json` は、`v`、`a`、`d` の 3 軸を持つ JSON とする
- 主要制約: `UNIQUE(event_id)`
- 主要索引: `(created_at DESC)`

<!-- Block: Event Links -->
### `event_links`

- 役割: イベント間の向き付き関係を保持する
- 主キー: `event_link_id TEXT PRIMARY KEY`
- 必須列: `from_event_id`, `to_event_id`, `label`, `confidence`, `evidence_event_ids_json`, `created_at`, `updated_at`
- `label` は、少なくとも `reply_to`、`same_topic`、`caused_by`、`continuation` を区別する
- 同一向き・同一ラベルの重複を許さない
- 主要制約: `UNIQUE(from_event_id, to_event_id, label)`
- 主要索引: `(from_event_id)`, `(to_event_id)`, `(label)`

<!-- Block: Event Threads -->
### `event_threads`

- 役割: イベントが属する文脈スレッドを保持する
- 主キー: `event_thread_id TEXT PRIMARY KEY`
- 必須列: `event_id`, `thread_key`, `confidence`, `created_at`, `updated_at`
- 任意列: `thread_role`
- 同一イベント・同一スレッドの重複を許さない
- 主要制約: `UNIQUE(event_id, thread_key)`
- 主要索引: `(event_id)`, `(thread_key)`

<!-- Block: State Links -->
### `state_links`

- 役割: 状態どうしの関連、補足、矛盾、派生を保持する
- 主キー: `state_link_id TEXT PRIMARY KEY`
- 必須列: `from_state_id`, `to_state_id`, `label`, `confidence`, `evidence_event_ids_json`, `created_at`, `updated_at`
- `label` は、少なくとも `relates_to`、`derived_from`、`supports`、`contradicts` を区別する
- 同一向き・同一ラベルの重複を許さない
- 主要制約: `UNIQUE(from_state_id, to_state_id, label)`
- 主要索引: `(from_state_id)`, `(to_state_id)`, `(label)`

<!-- Block: Entity Tables -->
### `event_entities` と `state_entities`

- `event_entities`
  - 役割: `events` に付いたエンティティ索引を保持する
  - 主キー: `event_entity_id TEXT PRIMARY KEY`
  - 必須列: `event_id`, `entity_type_norm`, `entity_name_raw`, `entity_name_norm`, `confidence`, `created_at`
  - 主要索引: `(event_id)`, `(entity_type_norm, entity_name_norm)`

- `state_entities`
  - 役割: `memory_states` に付いたエンティティ索引を保持する
  - 主キー: `state_entity_id TEXT PRIMARY KEY`
  - 必須列: `memory_state_id`, `entity_type_norm`, `entity_name_raw`, `entity_name_norm`, `confidence`, `created_at`
  - 主要索引: `(memory_state_id)`, `(entity_type_norm, entity_name_norm)`

<!-- Block: Preview Cache -->
### `event_preview_cache`

- 役割: 想起時の LLM 選別に使う派生プレビューを保持する
- 主キー: `preview_id TEXT PRIMARY KEY`
- 必須列: `event_id`, `preview_text`, `source_event_updated_at`, `created_at`, `updated_at`
- `event_id` は一意とし、1 イベント 1 プレビューを基本とする
- 主要制約: `UNIQUE(event_id)`
- 主要索引: `(source_event_updated_at DESC)`

<!-- Block: Audit Tables -->
### `revisions`

- 役割: 記憶更新の監査履歴を保持する
- 主キー: `revision_id TEXT PRIMARY KEY`
- 必須列: `entity_type`, `entity_id`, `before_json`, `after_json`, `reason`, `evidence_event_ids_json`, `created_at`
- 主要索引: `(entity_type, entity_id, created_at DESC)`, `(created_at DESC)`

<!-- Block: Retrieval Runs -->
### `retrieval_runs`

- 役割: 想起がどう実行されたかを観測する
- 主キー: `run_id TEXT PRIMARY KEY`
- 必須列: `cycle_id`, `created_at`, `plan_json`, `candidates_json`, `selected_json`
- 任意列: `resolved_event_ids_json`
- 主要索引: `(cycle_id)`, `(created_at DESC)`

<!-- Block: Job Group -->
## 記憶ジョブテーブル

<!-- Block: Memory Jobs -->
### `memory_jobs`

- 役割: 長周期で処理する記憶更新ジョブを保持する
- 主キー: `job_id TEXT PRIMARY KEY`
- 必須列: `job_kind`, `payload_ref_json`, `status`, `tries`, `created_at`, `updated_at`
- 任意列: `claimed_at`, `completed_at`, `last_error`
- `job_kind` は、少なくとも `write_memory`、`refresh_preview`、`embedding_sync`、`tidy_memory`、`quarantine_memory` を区別する
- `status` は、少なくとも `queued`、`claimed`、`completed`、`dead_letter` を区別する
- `payload_ref_json` は、`payload_kind`、`payload_id`、`payload_version` を必須とする
- 主要索引: `(status, created_at ASC)`, `(job_kind, status, created_at ASC)`

<!-- Block: Memory Job Payloads -->
### `memory_job_payloads`

- 役割: `memory_jobs.payload_ref` が指す payload 本体を保持する
- 主キー: `payload_id TEXT PRIMARY KEY`
- 必須列: `payload_kind`, `payload_version`, `job_kind`, `payload_json`, `created_at`, `idempotency_key`
- `payload_kind` は、初期段階では `memory_job_payload` に固定する
- `idempotency_key` は、同一仕事の二重投入判定に使う
- 同じ `idempotency_key` の payload を重複作成しない
- 主要制約: `UNIQUE(idempotency_key)`
- 主要索引: `(job_kind, created_at DESC)`

<!-- Block: Search Group -->
## 検索・派生索引テーブル

<!-- Block: Vec Items -->
### `vec_items`

- 役割: `sqlite-vec` で使う埋め込み索引を保持する
- 主キー: `vec_item_id TEXT PRIMARY KEY`
- 必須列: `entity_type`, `entity_id`, `embedding_model`, `embedding_scope`, `searchable`, `source_updated_at`, `embedding`
- `entity_type` は、少なくとも `event`、`memory_state`、`event_affect` を区別する
- `embedding_scope` は、少なくとも `recent`、`global` を区別する
- 同一対象・同一モデル・同一スコープの重複を許さない
- 主要制約: `UNIQUE(entity_type, entity_id, embedding_model, embedding_scope)`
- 主要索引: `(entity_type, searchable, source_updated_at DESC)`

<!-- Block: Events FTS -->
### `events_fts`

- 役割: `events` の文字 n-gram 検索用の派生索引を保持する
- 形式: FTS5 の仮想テーブルとする
- 必須列: `event_id`, `search_text`
- `event_id` は `UNINDEXED` で保持し、正本は常に `events` とする

<!-- Block: Transaction Boundaries -->
## 保存単位の固定

<!-- Block: Boot Boundary -->
### 起動初期化の保存境界

- 同じ起動 transaction に含めるのは、`db_meta`、`self_state`、`attention_state`、`body_state`、`world_state`、`drive_state` とする
- `runtime_leases` の取得と更新は、seed 用 transaction と分けてよい
- スキーマ版不一致や seed 失敗時は、起動を中断し、短周期や長周期を開始しない

<!-- Block: Short Cycle Boundary -->
### 短周期の保存境界

- 同じ短周期 transaction に含めるのは、`pending_inputs`、`settings_overrides`、`self_state`、`attention_state`、`body_state`、`world_state`、`drive_state`、`task_state`、`working_memory_items`、`recent_event_window_items`、`action_history`、`events`、`memory_jobs`、`memory_job_payloads`、`retrieval_runs`、`commit_records` とする
- `input_journal` は、短周期 transaction の前に先行追記してよい
- `ui_outbound_events` は、短周期 transaction と分離した append-only 追記を許す
- `events.jsonl` は、`commit_records` をもとに後段で派生同期する

<!-- Block: Long Cycle Boundary -->
### 長周期の保存境界

- 同じ長周期 transaction に含めるのは、`memory_jobs`、`memory_states`、`preference_memory`、`event_affects`、`event_links`、`event_threads`、`state_links`、`event_entities`、`state_entities`、`event_preview_cache`、`revisions`、`vec_items`、必要なら `skill_registry` とする
- `write_memory` は、必要なら同じ長周期 transaction 内で followup の `memory_jobs` と `memory_job_payloads` を追加してよい
- `refresh_preview` は、`event_preview_cache` 以外を更新してはならない
- `quarantine_memory` は、`searchable` 系の更新と監査痕跡だけを確定する

<!-- Block: Fixed Decisions -->
## このドキュメントで確定したこと

- `data/core.sqlite3` の初期スキーマは、状態、制御面、イベント、記憶、ジョブ、索引を分けた複数テーブルで持つ
- コア状態の短周期保存と、ブラウザ向け `ui_outbound_events` は保存境界を分ける
- `commit_records` が、短周期確定と `events.jsonl` 再生成の主な起点になる
- `memory_jobs` と `memory_job_payloads` を分け、`payload_ref` は JSON 参照として保持する
- `events` テーブルのエピソード正本と、`events.jsonl` の外部追跡ログは別物として扱う
- このドキュメントを基準に、`sql/core_schema.sql` を更新するか、次は ORM モデルを作る
