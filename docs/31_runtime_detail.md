# ランタイム詳細設計

<!-- Block: Purpose -->
## このドキュメントの役割

- このドキュメントは、`docs/30_design_breakdown.md` をさらに実装直前の粒度まで分解した詳細設計である
- 目的は、ランタイムの処理単位、受け渡しデータ、状態更新境界を曖昧にしないことにある
- 責務分割の全体像は `docs/10_target_architecture.md` を見る
- 実装単位の責務分解は `docs/30_design_breakdown.md` を見る
- 記憶サブシステムの詳細は `docs/32_memory_detail.md` を見る
- `memory_jobs` の payload 契約は `docs/33_memory_job_contracts.md` を見る
- 関数の入出力や状態遷移で迷ったら、このドキュメントを正本として扱う

<!-- Block: Scope -->
## このドキュメントで固定する範囲

- 固定するのは、`人格ランタイム` の実行モデル、`設定 Web サーバ` との受け渡し、`LLM` への認知入力、`action_command` の確定、状態保存の単位である
- 固定するのは、論理的な状態断面と更新順序であり、物理的な SQL テーブル名やカラム名そのものではない
- 固定するのは、ランタイム内部の契約であり、LLM プロバイダ固有の prompt 文面や SDK 呼び出しではない
- 記憶の物理保存の輪郭は `docs/32_memory_detail.md` で固定し、最終的な SQL の細部は次に別ドキュメントで固定する前提とする
- `memory_jobs.payload_ref` と `job_kind` ごとの payload 詳細は `docs/33_memory_job_contracts.md` を正本とする

<!-- Block: Runtime Model Group -->
## 実行モデルと前提状態

<!-- Block: Runtime Invariants -->
### ランタイムの不変条件

- 同時に状態を書き換える実体は、常に 1 つの `人格ランタイム` だけである
- 1 回の短周期ループは、1 つの作業コピーに対して閉じた更新として完了する
- 1 回の長周期ループは、直前までに確定したイベントだけを材料にする
- `LLM` は認知判断の主担当だが、I/O 実行者にも DB 更新者にもならない
- `action proposal` と `action_command` は常に分離し、同一視しない
- 性格、感情、記憶を欠いた判断は、不完全な認知として扱う
- 受理した観測は、判断前に `input_journal` へ不変追記し、短周期の途中失敗でも失われない
- 長周期で扱う記憶育成は、`memory_jobs` に永続化された仕事だけを処理対象にする
- 暗黙の補完やフォールバックは行わず、失敗は明示的な失敗として扱う

<!-- Block: Runtime Execution Model -->
### 実行モデル

- ランタイムは、常に 1 本の主スレッド相当の処理単位で `短周期ループ` と `長周期ループ` を交互に管理する
- 同じ時刻に、2 つの短周期ループが並列で同じ状態を更新することはない
- `短周期ループ` は、外部刺激への反応と即時行動を担当する
- `長周期ループ` は、反省、記憶整理、スキル昇格、埋め込み同期を担当する
- 各ループには、`cycle_id`、`cycle_kind`、`trigger_reason`、`started_at` を必ず付与する
- `trigger_reason` は、少なくとも `external_input`、`sensor_change`、`task_resume`、`idle_tick`、`self_initiated`、`post_action_followup` を区別する
- `trigger_reason` はサイクル起動理由の分類であり、詳細な外部入力源は `observation_batch.source` に持つ

<!-- Block: Short Cycle Triggers -->
### 短周期ループの起動条件

- `pending_inputs` に未処理入力があるときは、短周期ループを起動する
- センサー取得で新しい観測が得られたときは、短周期ループを起動する
- `task_state` に再開条件を満たした保留タスクがあるときは、短周期ループを起動する
- 一定時間のアイドリング経過で、再観測や自発行動の判定時刻に達したときは、短周期ループを起動する
- 直前行動に `requires_reobserve` が立っているときは、その追跡のために短周期ループを起動する

<!-- Block: Self Initiated Triggers -->
### 自発行動の起動条件

- `self_initiated` は、緊急度の高い外部入力、保留中の高優先タスク、外部待ちの戻りがないときだけ候補にする
- 自発行動の目的種別は、`task_progress`、`unexplored_check`、`self_maintenance`、`skill_rehearsal` の 4 つに固定する
- `self_initiated` を選ぶ場合でも、開始前に `goal_hint` と停止条件が作れない候補は採用しない
- 無目的な探索、無期限の巡回、根拠のないスキル試行は採用しない

<!-- Block: Long Cycle Triggers -->
### 長周期ループの起動条件

- 直近の短周期で新しいイベントが確定したときは、長周期ループの候補にする
- `reflection` 対象の失敗イベントが発生したときは、次の安全な境界で長周期ループを起動する
- 記憶更新の保留件数が閾値を超えたときは、長周期ループを起動する
- 一定時間ごとの整理時刻に達したときは、長周期ループを起動する
- 長周期ループは、短周期ループの保存完了前には起動しない

<!-- Block: Runtime Working Set -->
### 1 サイクルで扱う作業単位

- 1 回の短周期ループでは、`observation_batch`、`input_journal_batch`、`attention_set`、`cognition_input`、`cognition_result`、`action_command`、`commit_record` を作る
- 1 回の長周期ループでは、`claimed_memory_jobs`、`reflection_bundle`、`memory_updates`、`skill_updates`、`embedding_updates` を作る
- これらの作業単位は、永続状態そのものではなく、そのサイクル内だけで使う中間成果物である
- 永続状態へ反映されるのは、`state committer` が確定した差分だけである

<!-- Block: State Slices -->
### 状態断面の詳細

- `self_state` は、性格傾向、現在感情、長期目標、関係性、人格としての不変条件を持つ
- `attention_state` は、主注意対象、抑制対象、再確認待ち、直近の注意遷移理由を持つ
- `body_state` は、姿勢、移動状態、感覚器利用可否、出力ロック、現在負荷を持つ
- `world_state` は、現在地、周辺対象、状況要約、`affordances`、`constraints`、`attention_targets`、外部待ち状態を持つ
- `drive_state` は、内部欲求の強度と優先度への影響を持つ
- `task_state` は、進行中タスク、保留タスク、再開条件、中断可否、期限を持つ
- `memory_state` は、`working_memory`、`recent_event_window`、エピソード、意味、感情、対人、反省を持つ
- `skill_registry` は、再利用可能な行動列と、その発火条件、成功条件を持つ

<!-- Block: Task State Machine -->
### `task_state` の状態遷移

- `task_state` の主状態は、`idle`、`active`、`waiting_external`、`paused`、`completed`、`abandoned` の 6 つである
- `idle` は、現在の主タスクがない状態である
- `active` は、短周期で継続して処理すべき主タスクがある状態である
- `waiting_external` は、外部結果待ちで次の観測を待っている状態である
- `paused` は、緊急度の高い別件で一時中断している状態である
- `completed` は、目標を満たして終了した状態である
- `abandoned` は、安全制約、失敗、優先度低下で打ち切った状態である
- 遷移は、常に短周期または長周期の保存時にだけ確定する

<!-- Block: Short Cycle Group -->
## 短周期の処理契約

<!-- Block: Observation Batch -->
### `observation_batch` の契約

- `observation_batch` は、その短周期で処理対象になる観測イベントの集合である
- `observation_batch` は、受理済みの観測を `input_journal` へ記録した後に、正規化して組み立てる
- 各項目は、`observation_id`、`source`、`kind`、`captured_at`、`priority_hint`、`normalized_summary`、`payload_ref` を持つ
- `observation_id` は、その観測を `input_journal` と結び付ける一意キーであり、再処理時の重複追記を防ぐ
- `source` は、少なくとも `web_input`、`camera`、`microphone`、`network_result`、`sns_result`、`line_result`、`idle_tick`、`post_action_followup`、`self_initiated` を区別する
- `kind` は、少なくとも `instruction`、`scene_change`、`audio_segment`、`search_result`、`social_reaction`、`internal_trigger` を区別する
- 生データ本体は `payload_ref` の先に閉じ込め、人格コア側では正規化後の要約を主に扱う
- 同一短周期で扱う観測は、時系列順に並べたうえで `priority_hint` を保持する
- 内部起点の観測は、`trigger_reason` と同じ語彙で `idle_tick`、`post_action_followup`、`self_initiated` を使う

<!-- Block: Input Journal Contract -->
### `input_journal` の契約

- `input_journal` は、受理した観測や外部入力を、判断前に残すための不変ログである
- `input_journal` の追記は `input collector` が担当し、`attention_set` の評価前に完了させる
- 各記録は、少なくとも `journal_id`、`observation_id`、`cycle_id`、`source`、`kind`、`captured_at`、`receipt_summary`、`payload_ref`、`created_at` を持つ
- `receipt_summary` は、正規化済みの意味要約ではなく、受理時点で分かる短い受領要約である
- `input_journal` は append-only とし、同じ `observation_id` を二重追記しない
- `input_journal` はスケジューリング用キューではなく、「何を受理したか」の正本であり、後段の `events` が置き換えない
- 後続で `events` を確定するときは、どの `input_journal` を材料にしたかを追跡可能にする

<!-- Block: Attention Set -->
### `attention_set` の契約

- `attention_set` は、その短周期で何を主対象にし、何を抑制するかを確定した結果である
- `attention_set` は、`primary_focus`、`secondary_focuses`、`suppressed_items`、`revisit_queue` を持つ
- 注意配分は、`safety`、`explicitness`、`urgency`、`novelty`、`task_continuity` の 5 軸で評価する
- 明示指示は、同じ安全条件の範囲では高く評価する
- 進行中タスクに関係する観測は、無関係な新奇性だけで上書きしない
- 抑制した観測も捨てず、`revisit_queue` に残して次周期の候補にする

<!-- Block: Cognition Input Contract -->
### `cognition_input` の契約

- `cognition_input` は、その時点の人格として判断させるために `LLM` へ渡す入力断面である
- `cognition_input` は、`cycle_meta`、`persona_snapshot`、`body_snapshot`、`world_snapshot`、`drive_snapshot`、`task_snapshot`、`attention_snapshot`、`memory_bundle`、`policy_snapshot`、`skill_candidates`、`current_observation`、`context_budget` を持つ
- `persona_snapshot` には、性格傾向、現在感情、長期目標、関係性、人格としての不変条件を含める
- `body_snapshot` には、移動可否、出力可否、利用可能センサー、直近負荷、現在の姿勢を含める
- `world_snapshot` には、現在地、周辺対象、`affordances`、`constraints`、`attention_targets`、現在の状況要約を含める
- `drive_snapshot` には、内部欲求の強度と、その短周期での優先度影響を含める
- `task_snapshot` には、進行中タスク、保留条件、再開条件、中断可否を含める
- `attention_snapshot` には、主注意対象、抑制対象、再確認候補を含める
- `memory_bundle` には、`working_memory`、関連エピソード、関連意味記憶、関連感情記憶、関連対人記憶、関連反省メモを含める
- `memory_bundle` には、選別済みの `working_memory` とは別に、直近の生イベント列である `recent_event_window` を含める
- `policy_snapshot` には、`system policy`、`runtime policy`、今回の `external input` の評価結果を含める
- `skill_candidates` には、今回の状況に適合しうるスキルだけを含める
- `current_observation` には、今回の主注意対象として選ばれた観測と、その周辺観測を含める
- `context_budget` には、今回の `LLM` 呼び出しで使える全体量上限と、`persona`、`situation`、`memory`、`output contract` への割当上限を含める
- `context assembler` は DB の全量を渡さず、この短周期で必要な断面だけを選別して `cognition_input` にする
- `context assembler` は、`context_budget` を超えた断面をそのまま詰め込まず、優先度の低い項目から落として構成する

<!-- Block: Prompt Layering -->
### LLM へ渡す層の分け方

- `LLM` へ渡す内容は、`system layer`、`persona layer`、`situation layer`、`memory layer`、`output contract layer` の 5 層に分けて組み立てる
- `system layer` は、安全制約と人格個体の不変条件を持つ
- `persona layer` は、性格、現在感情、長期目標、関係性を持つ
- `situation layer` は、今回の観測、身体状態、世界状態、タスク状態を持つ
- `memory layer` は、今回参照が必要な記憶だけを持つ
- `output contract layer` は、返答形式と禁止事項を持つ
- どの層も省略可能な任意要素として扱わず、必須断面として常に構成する

<!-- Block: Cognition Result Contract -->
### `cognition_result` の契約

- `cognition_result` は、`LLM` が返す構造化された認知結果である
- `cognition_result` は、`intention_summary`、`decision_reason`、`action_proposals`、`step_hints`、`speech_draft`、`memory_focus`、`reflection_seed` を持つ
- `intention_summary` は、この短周期で人格が何をしようとしているかを 1 つに定める
- `decision_reason` は、行動の根拠となる要約であり、後から検証できる形で残す
- `action_proposals` は、優先順に並んだ候補列であり、まだ実行命令ではない
- `step_hints` は、複数手順が必要な場合の後続候補列であり、未確定の補助計画として扱う
- `speech_draft` は、`speak` 系候補があるときだけ持つ
- `memory_focus` は、この判断で特に参照した記憶の要約を持つ
- `reflection_seed` は、後続の `reflection writer` が使う要点を持つ
- 構造化形式に合わない `cognition_result` は、その短周期では失敗として扱い、実行段へ進めない

<!-- Block: Decision Gate -->
### 短周期の判断ゲート

- `attention_set` と `cognition_input` を作った時点で、その短周期は `ignore`、`react`、`defer` の 3 つに分ける
- `ignore` は、保存だけ行って即時行動をしない状態である
- `react` は、`LLM` による認知判断から行動候補を作る状態である
- `defer` は、保留タスクとして残し、即時行動を見送る状態である
- `ignore` と `defer` も、判断結果として明示的に確定し、暗黙に捨てない

<!-- Block: Action Proposal Contract -->
### `action_proposal` の契約

- `action_proposal` は、`LLM` が提案する未確定の行動候補である
- 各候補は、`proposal_id`、`action_type`、`target_hint`、`parameter_hint`、`goal_hint`、`step_hints`、`completion_hint`、`priority`、`reason` を持つ
- `action_type` は、少なくとも `speak`、`move`、`look`、`browse`、`social`、`notify`、`wait` を区別する
- 候補は複数返してよいが、優先度の高い順に並んでいなければならない
- 候補の段階では、実行パラメータはまだ確定値ではなく、検証前のヒントとして扱う
- `step_hints` は、必要な場合だけ持つ手続き的な補助手順であり、実行命令ではない
- `completion_hint` は、何をもってその候補が完了したとみなすかの観測条件を持つ

<!-- Block: Planning Constraints -->
### 長い計画の拘束条件

- 1 回の短周期で `action_command` に落とすのは、常に次の 1 手だけである
- `step_hints` が複数あっても、未実行の後続手順をまとめて確定命令にしない
- 後続手順は `task_state` に保持してよいが、次周期の観測と優先度評価を通したうえで再判断する
- 前周期で妥当だった後続手順でも、外界変化や失敗があれば自動継続せず破棄または再編する

<!-- Block: Action Command Contract -->
### `action_command` の契約

- `action_command` は、`action validator` が確定する唯一の実行命令である
- 1 回の短周期で確定する主命令は、原則として 1 つだけである
- `action_command` は、`command_id`、`command_type`、`target`、`parameters`、`preconditions`、`stop_conditions`、`timeout_ms`、`requires_reobserve`、`expected_effects`、`proposal_ref` を持つ
- `proposal_ref` は、どの `action_proposal` から確定したかを追跡するために必須である
- `preconditions` を満たさない候補は確定しない
- `stop_conditions` は、行動をいつ止めるかを明示し、無制限な継続を許さない
- `requires_reobserve` は、行動直後の標準再観測に加えて、追加の追跡観測が必要かを明示する
- どの `action_command` も、必ず 1 つの明確な `actuator_port` に属する

<!-- Block: Action Validation Rules -->
### `action validator` の確定ルール

- `action validator` は、候補を優先順に検査し、最初に実行可能な候補だけを `action_command` にする
- 検査対象は、`system policy`、`runtime policy`、身体制約、世界制約、空間制約、`affordances`、現在タスク、命令階層である
- 安全に反する候補は、その時点で棄却する
- 身体能力を超える候補は、その時点で棄却する
- 現在のタスク連続性を壊す候補は、緊急性がなければ保留に回す
- 外部入力があっても、`system policy` と `runtime policy` を上書きする候補は確定しない
- 実行可能な候補が 1 つもない場合は、候補を棄却または保留として確定し、代替命令を捏造しない

<!-- Block: Action Execution Contract -->
### `action dispatcher` と実行結果の契約

- `action dispatcher` は、`action_command` を対応する `actuator_port` に渡して実行する
- 実行結果は、`result_id`、`command_id`、`started_at`、`finished_at`、`status`、`failure_mode`、`observed_effects`、`raw_result_ref`、`adapter_trace_ref` を持つ
- `status` は、少なくとも `succeeded`、`failed`、`stopped` を区別する
- `failure_mode` は、失敗時の原因種別であり、少なくとも `precondition_failed`、`device_rejected`、`network_timeout`、`sensor_mismatch`、`unexpected_side_effect` を区別する
- 実行後の再観測結果は、必ず `observed_effects` として束ねる
- `requires_reobserve` が立っている場合は、行動直後の標準再観測に加えて追跡観測を行う
- 実行器は、宣言されていない副作用を持ってはならない
- 実行失敗も成功と同じく、後続の保存と反省の入力にする
- `adapter_trace_ref` は、外部アダプタ側の詳細記録への参照であり、統合由来の失敗解析に使う

<!-- Block: Commit Contract -->
### `state committer` の保存契約

- `state committer` は、その短周期で確定した差分だけを永続状態へ反映する
- 1 回の短周期の保存単位は、`self_state`、`attention_state`、`body_state`、`world_state`、`drive_state`、`task_state`、`working_memory`、`recent_event_window`、`action_history`、`pending_inputs`、`settings_overrides`、新規に enqueue する `memory_jobs`、必要なら `retrieval_runs` の処理結果である
- `state committer` は、まず SQLite 側の正本更新と `commit_record` を確定し、その後に `events.jsonl` を派生同期する
- `commit_record` には、少なくとも `commit_id`、`cycle_id`、`committed_at`、`log_sync_status` を持たせる
- `input_journal` は、短周期の状態保存とは別に事前追記されるが、その短周期で確定した `events` は必ず `input_journal` の参照を持てるようにする
- `events.jsonl` は正本ではないため、内容は必ず SQLite で確定した `commit_record` から再構成する
- `events.jsonl` の追記は、`commit_id` を使って idempotent に行い、同じ `commit_record` を二重記録しない
- `events.jsonl` の追記に失敗した場合は、`log_sync_status` を `needs_replay` に更新し、同じ状態差分を再適用せずに派生ログ同期だけを再実行する
- 短周期の正本完了条件は、SQLite 側の状態差分と `commit_record` の確定であり、`events.jsonl` 同期状態は `log_sync_status` で別追跡する

<!-- Block: Commit Order -->
### 短周期の保存順序

- まず、観測原本の受理時点で `input_journal` を `observation_id` 単位に追記し、取りこぼしを防ぐ
- 次に、入力の取り込み結果として `pending_inputs` と `settings_overrides` の状態を更新する
- 次に、`self_state`、`attention_state`、`body_state`、`world_state`、`drive_state`、`task_state`、`working_memory`、`recent_event_window` の差分を反映する
- 次に、`action_history`、`retrieval_runs`、エピソード候補を保存する
- 次に、その短周期で確定した `events` を根拠に、必要な `memory_jobs` を enqueue する
- 次に、今回の `commit_record` を `log_sync_status="pending"` で確定して SQLite の更新を完了する
- 次に、確定済み `commit_record` から `events.jsonl` を `commit_id` 単位で追記する
- 最後に、`events.jsonl` 同期結果に応じて `log_sync_status` を `synced` または `needs_replay` に更新する
- この順序は固定し、同一サイクル内で入れ替えない

<!-- Block: Long Cycle Group -->
## 長周期の処理契約

<!-- Block: Memory Job Scheduler -->
### `memory job scheduler` の契約

- `memory job scheduler` は、`memory_jobs` から `queued` のジョブを claim し、その長周期で扱う仕事を確定する
- `memory_jobs` の主状態は、`queued`、`claimed`、`completed`、`dead_letter` の 4 つである
- 各ジョブは、少なくとも `job_id`、`job_kind`、`payload_ref`、`created_at`、`status`、`tries` を持つ
- `payload_ref` の解決規則と `job_kind` ごとの payload 本体は `docs/33_memory_job_contracts.md` に従う
- 1 回の長周期では、claim したジョブだけを処理し、未claim のジョブを暗黙に巻き込まない
- `memory_jobs` は、少なくとも `write_memory`、`refresh_preview`、`embedding_sync`、`tidy_memory`、`quarantine_memory` を区別する
- 短周期で新しい `events` が確定したときは、同じ短周期の保存単位で最低でも `write_memory` を enqueue する
- `refresh_preview` は、対象イベント本文または要約が変わったときだけ enqueue する
- 失敗したジョブは `tries` を増やして再実行対象に残し、上限到達時だけ `dead_letter` にする

<!-- Block: Reflect Contract -->
### `reflection writer` の契約

- `reflection writer` は、直近の `commit_record` と `cognition_result` と実行結果を材料に `reflection_bundle` を作る
- `reflection_bundle` は、`what_happened`、`what_failed`、`what_worked`、`retry_hint`、`avoid_pattern` を持つ
- `reflection` は感想文ではなく、次回判断に使える差分知識として残す
- 直近の短周期で `ignore` や `defer` を選んだ場合も、その判断妥当性を記録対象にできる

<!-- Block: Consolidation Contract -->
### `memory consolidator` の契約

- `memory consolidator` は、`claimed_memory_jobs` と `reflection_bundle` と直近イベントから、対象ジョブに応じた更新内容を選別する
- `job_kind="write_memory"` の選別結果は、`episodic_updates`、`semantic_updates`、`affective_updates`、`relationship_updates`、`quarantine_updates` に分ける
- `job_kind="refresh_preview"` の場合は、`event_preview_cache` の更新だけを行い、他の更新群を同時に確定しない
- 一度の出来事でも、事実、感情、関係性は別レイヤとして分けて保持する
- `working_memory` にしか価値がない内容は、長期記憶へ昇格させない
- `recent_event_window` は、次の短周期に必要な近接文脈だけを残し、長期記憶へそのまま昇格させない
- 短期的な出来事のうち、再参照価値が低いものはエピソード候補のまま減衰させる
- `quarantine_updates` は、誤想起として確定した項目を削除せず、`searchable` の切替で主要想起から外す

<!-- Block: Learning Contract -->
### `skill promoter` と学習の契約

- `skill promoter` は、反復成功した行動列だけを `skill_registry` の候補にする
- `skill` には、`skill_id`、`trigger_pattern`、`preconditions`、`action_pattern`、`success_signature` を持たせる
- 単発成功だけでは `skill` に昇格させない
- `embedding_updates` は、記憶本文の更新と同じ長周期で同期する
- `refresh_preview` や `quarantine_memory` が完了したときも、対応する `memory_jobs` の状態を同じ長周期で `completed` に確定する
- 記憶の忘却は削除ではなく、重要度、参照頻度、記憶強度の減衰として扱う

<!-- Block: Boundary Conditions Group -->
## 境界条件

<!-- Block: Web Handoff Contract -->
### Web サーバとの受け渡し契約

- `settings api` と `text input api` は、人格状態を直接変更せず、`pending_inputs` と `settings_overrides` に要求を書き込む
- `pending_inputs` の各項目は、`input_id`、`source`、`channel`、`payload`、`created_at`、`priority`、`status` を持つ
- `settings_overrides` の各項目は、`override_id`、`key`、`requested_value`、`apply_scope`、`created_at`、`status` を持つ
- `pending_inputs.status` は、少なくとも `queued`、`claimed`、`consumed`、`discarded` を区別する
- `settings_overrides.status` は、少なくとも `queued`、`claimed`、`applied`、`rejected` を区別する
- ランタイムは、短周期の先頭で `queued` を `claimed` にし、そのサイクルの責任範囲として取り込む
- ランタイムは、入力を処理した同じ短周期の保存で、`pending_inputs` の `claimed` を必ず `consumed` または `discarded` に確定する
- ランタイムは、設定変更を評価した同じ短周期の保存で、`claimed` を必ず `applied` または `rejected` に確定する
- Web サーバは、`self_state`、`world_state`、`memory_state` の正本を直接更新しない

<!-- Block: Error Policy -->
### エラー時の扱い

- `LLM` の構造化出力が壊れている場合は、その短周期を失敗として記録し、実行段へ進めない
- `action validator` が候補をすべて棄却した場合は、その事実を明示的に保存し、暗黙の代替行動は行わない
- 外部 I/O の失敗は、`action_history` とイベントに残し、次の `reflection` 対象にする
- SQLite 側の正本保存失敗は明示的なランタイムエラーとして扱い、黙って先へ進めない
- `events.jsonl` の同期失敗は、`log_sync_status="needs_replay"` として明示し、状態差分の再適用ではなく派生ログ同期の再実行対象にする
- エラーを握りつぶす処理は作らない

<!-- Block: Concurrency Policy -->
### 並行性の制約

- 同じ人格個体に対して、同時に複数の `人格ランタイム` を起動しない
- `設定 Web サーバ` は、入力要求と設定要求だけを並行に受け付けてよい
- ランタイムは、`claimed` にした入力だけをその短周期の責任範囲として扱う
- 同じ状態断面に対して、Web サーバとランタイムが同時に直接書き込む設計は採用しない
- 並列実行で性能を稼ぐより、状態一貫性を優先する

<!-- Block: Fixed Decisions -->
## このドキュメントで確定したこと

- `docs/30_design_breakdown.md` の各責務は、ここで定義した入出力契約で実装する
- `cognition_input` は、人格、状態、記憶、命令階層を必須断面として持つ
- `LLM` は `cognition_result` を返すが、`action_command` は返さない
- `action validator` は、候補を 1 つの実行命令へ確定するか、棄却または保留を明示する
- 受理した観測は、短周期の判断前に `input_journal` へ残す
- 1 回の短周期は、SQLite 側の正本更新を 1 つの保存単位として閉じる
- 長周期の記憶育成は、`memory_jobs` の永続状態を持って進める
- `events.jsonl` は観測ログであり、`commit_record` から再構成できる派生ログとして扱う
- エラーや不整合は明示的に失敗として扱い、暗黙の補完はしない
