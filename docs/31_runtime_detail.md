# ランタイム詳細設計

<!-- Block: Purpose -->
## このドキュメントの役割

- このドキュメントは、`docs/30_design_breakdown.md` をさらに実装直前の粒度まで分解した詳細設計である
- 目的は、ランタイムの処理単位、受け渡しデータ、状態更新境界を曖昧にしないことにある
- 責務分割の全体像は `docs/10_target_architecture.md` を見る
- 実装単位の責務分解は `docs/30_design_breakdown.md` を見る
- 関数の入出力や状態遷移で迷ったら、このドキュメントを正本として扱う

<!-- Block: Scope -->
## このドキュメントで固定する範囲

- 固定するのは、`人格ランタイム` の実行モデル、`設定 Web サーバ` との受け渡し、`LLM` への認知入力、`action_command` の確定、状態保存の単位である
- 固定するのは、論理的な状態断面と更新順序であり、物理的な SQL テーブル名やカラム名そのものではない
- 固定するのは、ランタイム内部の契約であり、LLM プロバイダ固有の prompt 文面や SDK 呼び出しではない
- 物理スキーマの詳細は、次に別ドキュメントで固定する前提とする

<!-- Block: Runtime Invariants -->
## ランタイムの不変条件

- 同時に状態を書き換える実体は、常に 1 つの `人格ランタイム` だけである
- 1 回の短周期ループは、1 つの作業コピーに対して閉じた更新として完了する
- 1 回の長周期ループは、直前までに確定したイベントだけを材料にする
- `LLM` は認知判断の主担当だが、I/O 実行者にも DB 更新者にもならない
- `action proposal` と `action_command` は常に分離し、同一視しない
- 性格、感情、記憶を欠いた判断は、不完全な認知として扱う
- 暗黙の補完やフォールバックは行わず、失敗は明示的な失敗として扱う

<!-- Block: Runtime Execution Model -->
## 実行モデル

- ランタイムは、常に 1 本の主スレッド相当の処理単位で `短周期ループ` と `長周期ループ` を交互に管理する
- 同じ時刻に、2 つの短周期ループが並列で同じ状態を更新することはない
- `短周期ループ` は、外部刺激への反応と即時行動を担当する
- `長周期ループ` は、反省、記憶整理、スキル昇格、埋め込み同期を担当する
- 各ループには、`cycle_id`、`cycle_kind`、`trigger_reason`、`started_at` を必ず付与する
- `trigger_reason` は、少なくとも `external_input`、`sensor_change`、`task_resume`、`time_elapsed`、`post_action_followup` を区別する

<!-- Block: Short Cycle Triggers -->
## 短周期ループの起動条件

- `pending_inputs` に未処理入力があるときは、短周期ループを起動する
- センサー取得で新しい観測が得られたときは、短周期ループを起動する
- `task_state` に再開条件を満たした保留タスクがあるときは、短周期ループを起動する
- 一定時間のアイドリング経過で、再観測や自発行動の判定時刻に達したときは、短周期ループを起動する
- 直前行動に `requires_reobserve` が立っているときは、その追跡のために短周期ループを起動する

<!-- Block: Long Cycle Triggers -->
## 長周期ループの起動条件

- 直近の短周期で新しいイベントが確定したときは、長周期ループの候補にする
- `reflection` 対象の失敗イベントが発生したときは、次の安全な境界で長周期ループを起動する
- 記憶更新の保留件数が閾値を超えたときは、長周期ループを起動する
- 一定時間ごとの整理時刻に達したときは、長周期ループを起動する
- 長周期ループは、短周期ループの保存完了前には起動しない

<!-- Block: Runtime Working Set -->
## 1 サイクルで扱う作業単位

- 1 回の短周期ループでは、`observation_batch`、`attention_set`、`cognition_input`、`cognition_result`、`action_command`、`commit_record` を作る
- 1 回の長周期ループでは、`reflection_bundle`、`memory_updates`、`skill_updates`、`embedding_updates` を作る
- これらの作業単位は、永続状態そのものではなく、そのサイクル内だけで使う中間成果物である
- 永続状態へ反映されるのは、`state committer` が確定した差分だけである

<!-- Block: Observation Batch -->
## `observation_batch` の契約

- `observation_batch` は、その短周期で処理対象になる観測イベントの集合である
- 各項目は、`observation_id`、`source`、`kind`、`captured_at`、`priority_hint`、`normalized_summary`、`payload_ref` を持つ
- `source` は、少なくとも `web_input`、`camera`、`microphone`、`network_result`、`sns_result`、`line_result`、`internal_timer` を区別する
- `kind` は、少なくとも `instruction`、`scene_change`、`audio_segment`、`search_result`、`social_reaction`、`internal_trigger` を区別する
- 生データ本体は `payload_ref` の先に閉じ込め、人格コア側では正規化後の要約を主に扱う
- 同一短周期で扱う観測は、時系列順に並べたうえで `priority_hint` を保持する

<!-- Block: Attention Set -->
## `attention_set` の契約

- `attention_set` は、その短周期で何を主対象にし、何を抑制するかを確定した結果である
- `attention_set` は、`primary_focus`、`secondary_focuses`、`suppressed_items`、`revisit_queue` を持つ
- 注意配分は、`safety`、`explicitness`、`urgency`、`novelty`、`task_continuity` の 5 軸で評価する
- 明示指示は、同じ安全条件の範囲では高く評価する
- 進行中タスクに関係する観測は、無関係な新奇性だけで上書きしない
- 抑制した観測も捨てず、`revisit_queue` に残して次周期の候補にする

<!-- Block: Cognition Input Contract -->
## `cognition_input` の契約

- `cognition_input` は、その時点の人格として判断させるために `LLM` へ渡す入力断面である
- `cognition_input` は、`cycle_meta`、`persona_snapshot`、`body_snapshot`、`world_snapshot`、`drive_snapshot`、`task_snapshot`、`attention_snapshot`、`memory_bundle`、`policy_snapshot`、`skill_candidates`、`current_observation` を持つ
- `persona_snapshot` には、性格傾向、現在感情、長期目標、関係性、人格としての不変条件を含める
- `body_snapshot` には、移動可否、出力可否、利用可能センサー、直近負荷、現在の姿勢を含める
- `world_snapshot` には、現在地、周辺対象、`affordances`、`constraints`、現在の状況要約を含める
- `drive_snapshot` には、内部欲求の強度と、その短周期での優先度影響を含める
- `task_snapshot` には、進行中タスク、保留条件、再開条件、中断可否を含める
- `attention_snapshot` には、主注意対象、抑制対象、再確認候補を含める
- `memory_bundle` には、`working_memory`、関連エピソード、関連意味記憶、関連感情記憶、関連対人記憶、関連反省メモを含める
- `policy_snapshot` には、`system policy`、`runtime policy`、今回の `external input` の評価結果を含める
- `skill_candidates` には、今回の状況に適合しうるスキルだけを含める
- `current_observation` には、今回の主注意対象として選ばれた観測と、その周辺観測を含める
- `context assembler` は DB の全量を渡さず、この短周期で必要な断面だけを選別して `cognition_input` にする

<!-- Block: Prompt Layering -->
## LLM へ渡す層の分け方

- `LLM` へ渡す内容は、`system layer`、`persona layer`、`situation layer`、`memory layer`、`output contract layer` の 5 層に分けて組み立てる
- `system layer` は、安全制約と人格個体の不変条件を持つ
- `persona layer` は、性格、現在感情、長期目標、関係性を持つ
- `situation layer` は、今回の観測、身体状態、世界状態、タスク状態を持つ
- `memory layer` は、今回参照が必要な記憶だけを持つ
- `output contract layer` は、返答形式と禁止事項を持つ
- どの層も省略可能な任意要素として扱わず、必須断面として常に構成する

<!-- Block: Cognition Result Contract -->
## `cognition_result` の契約

- `cognition_result` は、`LLM` が返す構造化された認知結果である
- `cognition_result` は、`intention_summary`、`decision_reason`、`action_proposals`、`speech_draft`、`memory_focus`、`reflection_seed` を持つ
- `intention_summary` は、この短周期で人格が何をしようとしているかを 1 つに定める
- `decision_reason` は、行動の根拠となる要約であり、後から検証できる形で残す
- `action_proposals` は、優先順に並んだ候補列であり、まだ実行命令ではない
- `speech_draft` は、`speak` 系候補があるときだけ持つ
- `memory_focus` は、この判断で特に参照した記憶の要約を持つ
- `reflection_seed` は、後続の `reflection writer` が使う要点を持つ
- 構造化形式に合わない `cognition_result` は、その短周期では失敗として扱い、実行段へ進めない

<!-- Block: Decision Gate -->
## 短周期の判断ゲート

- `attention_set` と `cognition_input` を作った時点で、その短周期は `ignore`、`react`、`defer` の 3 つに分ける
- `ignore` は、保存だけ行って即時行動をしない状態である
- `react` は、`LLM` による認知判断から行動候補を作る状態である
- `defer` は、保留タスクとして残し、即時行動を見送る状態である
- `ignore` と `defer` も、判断結果として明示的に確定し、暗黙に捨てない

<!-- Block: Action Proposal Contract -->
## `action_proposal` の契約

- `action_proposal` は、`LLM` が提案する未確定の行動候補である
- 各候補は、`proposal_id`、`action_type`、`target_hint`、`parameter_hint`、`goal_hint`、`priority`、`reason` を持つ
- `action_type` は、少なくとも `speak`、`move`、`look`、`browse`、`social`、`notify`、`wait` を区別する
- 候補は複数返してよいが、優先度の高い順に並んでいなければならない
- 候補の段階では、実行パラメータはまだ確定値ではなく、検証前のヒントとして扱う

<!-- Block: Action Command Contract -->
## `action_command` の契約

- `action_command` は、`action validator` が確定する唯一の実行命令である
- 1 回の短周期で確定する主命令は、原則として 1 つだけである
- `action_command` は、`command_id`、`command_type`、`target`、`parameters`、`preconditions`、`stop_conditions`、`timeout_ms`、`requires_reobserve`、`expected_effects`、`proposal_ref` を持つ
- `proposal_ref` は、どの `action_proposal` から確定したかを追跡するために必須である
- `preconditions` を満たさない候補は確定しない
- `stop_conditions` は、行動をいつ止めるかを明示し、無制限な継続を許さない
- `requires_reobserve` は、行動後に追加観測が必要かを明示する
- どの `action_command` も、必ず 1 つの明確な `actuator_port` に属する

<!-- Block: Action Validation Rules -->
## `action validator` の確定ルール

- `action validator` は、候補を優先順に検査し、最初に実行可能な候補だけを `action_command` にする
- 検査対象は、`system policy`、`runtime policy`、身体制約、世界制約、現在タスク、命令階層である
- 安全に反する候補は、その時点で棄却する
- 身体能力を超える候補は、その時点で棄却する
- 現在のタスク連続性を壊す候補は、緊急性がなければ保留に回す
- 外部入力があっても、`system policy` と `runtime policy` を上書きする候補は確定しない
- 実行可能な候補が 1 つもない場合は、候補を棄却または保留として確定し、代替命令を捏造しない

<!-- Block: Action Execution Contract -->
## `action dispatcher` と実行結果の契約

- `action dispatcher` は、`action_command` を対応する `actuator_port` に渡して実行する
- 実行結果は、`result_id`、`command_id`、`started_at`、`finished_at`、`status`、`observed_effects`、`raw_result_ref` を持つ
- `status` は、少なくとも `succeeded`、`failed`、`stopped` を区別する
- 実行中に新しい観測変化があった場合は、`observed_effects` として再観測結果を束ねる
- 実行器は、宣言されていない副作用を持ってはならない
- 実行失敗も成功と同じく、後続の保存と反省の入力にする

<!-- Block: Commit Contract -->
## `state committer` の保存契約

- `state committer` は、その短周期で確定した差分だけを永続状態へ反映する
- 1 回の短周期の保存単位は、`self_state`、`attention_state`、`body_state`、`world_state`、`task_state`、`working_memory`、`action_history`、`pending_inputs` の処理結果である
- `state committer` は、まず SQLite 側の正本更新を確定し、その直後に `events.jsonl` を追記する
- `events.jsonl` は正本ではないため、内容は必ず SQLite で確定した `commit_record` から再構成する
- `events.jsonl` の追記失敗は黙殺せず、明示的なエラーとして扱う
- 保存が完了するまでは、その短周期は未完了であり、次の長周期へ進めない

<!-- Block: Commit Order -->
## 短周期の保存順序

- まず、入力の取り込み結果として `pending_inputs` の状態を更新する
- 次に、`self_state`、`attention_state`、`body_state`、`world_state`、`task_state`、`working_memory` の差分を反映する
- 次に、`action_history` とエピソード候補を保存する
- 次に、今回の `commit_record` を確定して SQLite の更新を完了する
- 最後に、確定済み `commit_record` から `events.jsonl` を追記する
- この順序は固定し、同一サイクル内で入れ替えない

<!-- Block: Reflect Contract -->
## `reflection writer` の契約

- `reflection writer` は、直近の `commit_record` と `cognition_result` と実行結果を材料に `reflection_bundle` を作る
- `reflection_bundle` は、`what_happened`、`what_failed`、`what_worked`、`retry_hint`、`avoid_pattern` を持つ
- `reflection` は感想文ではなく、次回判断に使える差分知識として残す
- 直近の短周期で `ignore` や `defer` を選んだ場合も、その判断妥当性を記録対象にできる

<!-- Block: Consolidation Contract -->
## `memory consolidator` の契約

- `memory consolidator` は、`reflection_bundle` と直近イベントから長期記憶へ残す内容を選別する
- 選別結果は、`episodic_updates`、`semantic_updates`、`affective_updates`、`relationship_updates` に分ける
- 一度の出来事でも、事実、感情、関係性は別レイヤとして分けて保持する
- `working_memory` にしか価値がない内容は、長期記憶へ昇格させない
- 短期的な出来事のうち、再参照価値が低いものはエピソード候補のまま減衰させる

<!-- Block: Learning Contract -->
## `skill promoter` と学習の契約

- `skill promoter` は、反復成功した行動列だけを `skill_registry` の候補にする
- `skill` には、`skill_id`、`trigger_pattern`、`preconditions`、`action_pattern`、`success_signature` を持たせる
- 単発成功だけでは `skill` に昇格させない
- `embedding_updates` は、記憶本文の更新と同じ長周期で同期する
- 記憶の忘却は削除ではなく、重要度、参照頻度、記憶強度の減衰として扱う

<!-- Block: Web Handoff Contract -->
## Web サーバとの受け渡し契約

- `settings api` と `text input api` は、人格状態を直接変更せず、`pending_inputs` と `settings_overrides` に要求を書き込む
- `pending_inputs` の各項目は、`input_id`、`source`、`channel`、`payload`、`created_at`、`priority`、`status` を持つ
- `settings_overrides` の各項目は、`override_id`、`key`、`requested_value`、`apply_scope`、`created_at`、`status` を持つ
- `status` は、少なくとも `queued`、`claimed`、`applied`、`rejected` を区別する
- ランタイムは、短周期の先頭で `queued` を `claimed` にし、そのサイクルの責任範囲として取り込む
- Web サーバは、`self_state`、`world_state`、`memory_state` の正本を直接更新しない

<!-- Block: State Slices -->
## 状態断面の詳細

- `self_state` は、性格傾向、現在感情、長期目標、関係性、人格としての不変条件を持つ
- `attention_state` は、主注意対象、抑制対象、再確認待ち、直近の注意遷移理由を持つ
- `body_state` は、姿勢、移動状態、感覚器利用可否、出力ロック、現在負荷を持つ
- `world_state` は、現在地、周辺対象、状況要約、`affordances`、`constraints`、外部待ち状態を持つ
- `drive_state` は、内部欲求の強度と優先度への影響を持つ
- `task_state` は、進行中タスク、保留タスク、再開条件、中断可否、期限を持つ
- `memory_state` は、`working_memory`、エピソード、意味、感情、対人、反省を持つ
- `skill_registry` は、再利用可能な行動列と、その発火条件、成功条件を持つ

<!-- Block: Task State Machine -->
## `task_state` の状態遷移

- `task_state` の主状態は、`idle`、`active`、`waiting_external`、`paused`、`completed`、`abandoned` の 6 つである
- `idle` は、現在の主タスクがない状態である
- `active` は、短周期で継続して処理すべき主タスクがある状態である
- `waiting_external` は、外部結果待ちで次の観測を待っている状態である
- `paused` は、緊急度の高い別件で一時中断している状態である
- `completed` は、目標を満たして終了した状態である
- `abandoned` は、安全制約、失敗、優先度低下で打ち切った状態である
- 遷移は、常に短周期または長周期の保存時にだけ確定する

<!-- Block: Error Policy -->
## エラー時の扱い

- `LLM` の構造化出力が壊れている場合は、その短周期を失敗として記録し、実行段へ進めない
- `action validator` が候補をすべて棄却した場合は、その事実を明示的に保存し、暗黙の代替行動は行わない
- 外部 I/O の失敗は、`action_history` とイベントに残し、次の `reflection` 対象にする
- 保存失敗は明示的なランタイムエラーとして扱い、黙って先へ進めない
- エラーを握りつぶす処理は作らない

<!-- Block: Concurrency Policy -->
## 並行性の制約

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
- 1 回の短周期は、1 つの保存単位として閉じる
- `events.jsonl` は観測ログであり、正本は常に SQLite 側である
- エラーや不整合は明示的に失敗として扱い、暗黙の補完はしない
