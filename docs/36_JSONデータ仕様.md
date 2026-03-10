# JSONデータ仕様

<!-- Block: Purpose -->
## このドキュメントの役割

- このドキュメントは、SQLite の JSON 列と Web API の JSON 本文を、オブジェクト単位で固定する正本である
- 目的は、`payload_json`、`payload_ref_json`、Web API の入出力本文の中身を、実装前に曖昧にしないことにある
- テーブル名と保存境界は `docs/34_SQLite論理スキーマ.md` を見る
- エンドポイントの意味、HTTP method、`SSE` の接続方法は `docs/35_WebAPI仕様.md` を見る
- `memory_jobs` の責務と payload の意味は `docs/33_記憶ジョブ仕様.md` を見る
- 設定キーごとの型制約は `docs/39_設定キー運用仕様.md` を見る
- JSON のキー、型、必須項目で迷ったら、このドキュメントを正本として扱う

<!-- Block: Scope -->
## このドキュメントで固定する範囲

- 固定するのは、current 実装で使う JSON オブジェクトのキー、型、必須項目、固定語彙である
- 固定するのは、`pending_inputs.payload_json`、`settings_overrides.requested_value_json`、`settings_editor_state.system_values_json`、5 種のプリセットテーブルの `payload_json`、`settings_change_sets.payload_json`、`ui_outbound_events.payload_json`、`action_history.command_json`、`action_history.observed_effects_json`、`memory_jobs.payload_ref_json`、`memory_job_payloads.payload_json`、`preference_memory.target_entity_ref_json`、`event_affects.moment_affect_labels_json`、`event_affects.vad_json`、主要な Web API 本文である
- 固定するのは、`self_state.personality_json`、`self_state.current_emotion_json`、`self_state.long_term_goals_json`、`self_state.relationship_overview_json`、`self_state.invariants_json`、短周期の内部で使う `selection_profile`、`memory_bundle`、`conversation_context`、`retrieval_context`、`last_persona_update_summary`、`persona_consistency_score`、`attention_score_breakdown`、`self_initiated_score_breakdown`、`action_candidate_score`、`cognition_plan`、`speech_draft`、`cognition_result`、長周期の内部で使う `MemoryWritePlan`、`personality_change_proposal`、`persona_updates` の形である
- 固定しないのは、Python のクラス名、Pydantic モデル名、OpenAPI の自動生成細部である
- 固定しないのは、将来追加する未使用フィールドや後段の拡張イベント種別である

<!-- Block: Out Of Scope -->
## このドキュメントに書かないこと

- HTTP path、method、主要ステータス、`SSE` 接続方式は `docs/35_WebAPI仕様.md` を正本とする
- ランタイムの処理順、保存順、判断規則は `docs/31_ランタイム処理仕様.md` を正本とする
- SQLite のテーブル名、カラム名、制約は `docs/34_SQLite論理スキーマ.md` を正本とする
- 入力重複、`cancel`、`SSE` 保持運用は `docs/38_入力ストリーム運用仕様.md` を正本とする
- JSON shape に影響しない一時メモや current 実装の細かな経路説明は、このドキュメントへ入れない

<!-- Block: Read Guide -->
## target と current の読み分け

- JSON キー名と shape の固定は target/current 共通の正本として読む
- `current`、`browser_chat`、`status api`、`settings UI` に紐づく補足は、現在の実装で実際に出入りする shape を示す
- 後続の `初期実装` 補足は、特に断りがない限り current の `browser_chat` 実装を指す
- current 補足は shape や固定語彙に効くものだけを残し、処理意味そのものは他の正本へ逃がす

<!-- Block: Common Rules -->
## 共通ルール

<!-- Block: Json Shape -->
### JSON の基本形

- JSON のキーは、すべて `snake_case` に統一する
- ただし、設定値のマップだけは、`docs/39_設定キー運用仕様.md` と同じドット区切り設定キーをそのままキー名に使ってよい
- ここで定義する JSON のルートは、すべてオブジェクトに固定する
- 必須項目は常に出現させる
- 任意項目は、値がないときに `null` を入れず、省略する
- 未定義キーは受け付けない
- 永続化 JSON と Web API の時刻は、原則として UTC unix milliseconds の `integer` に固定する
- LLM に渡す内部の派生入力はこの限りでなく、`context assembler` が人間可読な日時表現と相対時間表現を別フィールドとして組み立てる
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
- `payload_ref_json` は、`input_journal.payload_ref_json` と `memory_jobs.payload_ref_json` で共通の形を使うが、`payload_kind` の語彙は用途ごとに分ける

```json
{
  "payload_kind": "input_payload",
  "payload_id": "payload_...",
  "payload_version": 1
}
```

- `payload_kind` は、参照先の分類を示す `string` である
- `payload_id` は、参照先レコードの主キーまたは `payload_kind` ごとの opaque な識別子である
- `payload_version` は、参照先 JSON の版を示す `integer` である
- `input_journal.payload_ref_json.payload_kind` は、初期段階では `input_payload`、`media_file`、`external_result` を区別する
- `memory_jobs.payload_ref_json.payload_kind` は、初期段階では `memory_job_payload` に固定する

<!-- Block: Personality Entry -->
### `personality_preference_entry`

```json
{
  "domain": "action_type",
  "target_key": "look",
  "weight": 0.45,
  "evidence_count": 4
}
```

- 必須項目は `domain`、`target_key`、`weight`、`evidence_count` である
- `domain` は、少なくとも `action_type`、`observation_kind`、`interaction_style`、`topic` を区別する
- `target_key` は、対象を表す短い `string` である
- `weight` は、`0.0..1.0` の `number` に固定する
- `evidence_count` は、昇格根拠件数を示す `integer` であり、`1` 未満を許可しない

<!-- Block: Preference Target Ref -->
### `preference_target_entity_ref`

```json
{
  "target_kind": "action_type",
  "target_key": "browse"
}
```

- `preference_target_entity_ref` は、`preference_memory.target_entity_ref_json` と `MemoryWritePlan.preference_updates.target_entity_ref` で共通に使う
- 必須項目は `target_kind`、`target_key` である
- `target_kind` は、少なくとも `action_type`、`observation_kind` を区別する
- `target_key` は、対象を表す非空の `string` である

<!-- Block: Pending Input Payload -->
### `pending_inputs.payload_json`

```json
{
  "input_kind": "idle_tick",
  "trigger_reason": "idle_tick",
  "idle_duration_ms": 1000
}
```

- `pending_inputs.payload_json` は、current の受理入力本文である
- 必須項目は `input_kind` である
- current の `input_kind` は `chat_message`、`microphone_message`、`camera_observation`、`network_result`、`idle_tick`、`cancel` に固定する
- `chat_message` は、`message_kind="dialogue_turn"`、`trigger_reason="external_input"` を持ち、必要なら `text`、`attachments`、`client_message_id` を持ってよい
- `microphone_message` は、`message_kind="dialogue_turn"`、`trigger_reason="external_input"`、非空の `text`、`stt_provider`、`stt_language` を必須とする
- `camera_observation` は、`trigger_reason` と `attachments` を必須とし、`attachments` は `camera_still_image` を 1 件以上持つ配列に固定し、各添付は `camera_connection_id` と `camera_display_name` を持つ
- `network_result` は、`trigger_reason="external_result"`、`query`、`summary_text`、`source_task_id` を必須とする
- `idle_tick` は、`trigger_reason="idle_tick"` と正の `idle_duration_ms` を必須とし、`text` や `attachments` を持たない
- `cancel` は、`trigger_reason="external_input"` を必須とし、必要なら `target_message_id` を持ってよい

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

<!-- Block: Self State Group -->
## 人格状態の JSON

<!-- Block: Personality Json -->
### `self_state.personality_json`

```json
{
  "trait_values": {
    "sociability": 0.0,
    "caution": 0.0,
    "curiosity": 0.0,
    "persistence": 0.0,
    "warmth": 0.0,
    "assertiveness": 0.0,
    "novelty_preference": 0.0
  },
  "preferred_interaction_style": {
    "speech_tone": "neutral",
    "distance_style": "balanced",
    "confirmation_style": "balanced",
    "response_pace": "balanced"
  },
  "learned_preferences": [],
  "learned_aversions": [],
  "habit_biases": {
    "preferred_action_types": [],
    "preferred_observation_kinds": [],
    "avoided_action_styles": []
  }
}
```

- 必須項目は `trait_values`、`preferred_interaction_style`、`learned_preferences`、`learned_aversions`、`habit_biases` である
- `trait_values` は、`sociability`、`caution`、`curiosity`、`persistence`、`warmth`、`assertiveness`、`novelty_preference` を必須キーとして持つ
- `trait_values` の各値は、`-1.0..+1.0` の `number` に固定する
- `preferred_interaction_style` は、`speech_tone`、`distance_style`、`confirmation_style`、`response_pace` を必須キーとして持つ
- `speech_tone` は、少なくとも `gentle`、`neutral`、`firm` を区別する
- `distance_style` は、少なくとも `reserved`、`balanced`、`close` を区別する
- `confirmation_style` は、少なくとも `light`、`balanced`、`careful` を区別する
- `response_pace` は、少なくとも `careful`、`balanced`、`quick` を区別する
- `learned_preferences` と `learned_aversions` は、`personality_preference_entry` の配列に固定する
- `habit_biases.preferred_action_types` は、行動種別の順序付き配列である
- `habit_biases.preferred_observation_kinds` は、観測種別の順序付き配列である
- `habit_biases.avoided_action_styles` は、避ける行動様式の順序付き配列である

<!-- Block: Current Emotion Json -->
### `self_state.current_emotion_json`

```json
{
  "primary_label": "calm",
  "valence": 0.10,
  "arousal": 0.22,
  "dominance": 0.08,
  "stability": 0.74,
  "active_biases": {
    "caution_bias": 0.12,
    "approach_bias": 0.06,
    "avoidance_bias": 0.04,
    "speech_intensity_bias": 0.00
  }
}
```

- 必須項目は `primary_label`、`valence`、`arousal`、`dominance`、`stability`、`active_biases` である
- `primary_label` は、少なくとも `calm`、`curious`、`guarded`、`warm`、`tense`、`frustrated` を区別する
- `valence`、`arousal`、`dominance` は、`-1.0..+1.0` の `number` に固定する
- `stability` は、`0.0..1.0` の `number` に固定する
- `active_biases` は、`selection_profile.emotion_bias` と同じ固定キーを持ち、各値は `-1.0..+1.0` の `number` に固定する
- `active_biases` は、短周期での `selection_profile.emotion_bias` を作る直接入力として使う

<!-- Block: Long Term Goal Entry -->
### `long_term_goal_entry`

```json
{
  "goal_id": "goal_001",
  "summary": "身近な関係対象との信頼を維持する",
  "priority_weight": 0.82,
  "status": "active",
  "target_horizon": "long"
}
```

- 必須項目は `goal_id`、`summary`、`priority_weight`、`status`、`target_horizon` である
- `priority_weight` は、`0.0..1.0` の `number` に固定する
- `status` は、少なくとも `active`、`paused`、`completed` を区別する
- `target_horizon` は、少なくとも `short`、`mid`、`long` を区別する

<!-- Block: Long Term Goals Json -->
### `self_state.long_term_goals_json`

```json
{
  "goals": []
}
```

- 必須項目は `goals` である
- `goals` は、`long_term_goal_entry` の配列に固定する
- `goals` は、`priority_weight` の高い順に並べる

<!-- Block: Relationship Overview Entry -->
### `relationship_overview_entry`

```json
{
  "target_ref": "entity:alice",
  "relation_kind": "care",
  "attention_weight": 0.78,
  "care_commitment": 0.86,
  "trust_level": 0.74,
  "recent_tension": 0.08,
  "recent_positive_contact": 0.67,
  "waiting_response": false,
  "last_interaction_at": 1760000000000
}
```

- 必須項目は `target_ref`、`relation_kind`、`attention_weight`、`care_commitment`、`trust_level`、`recent_tension`、`recent_positive_contact`、`waiting_response` である
- `relation_kind` は、少なくとも `care`、`peer`、`unknown`、`strained` を区別する
- `attention_weight`、`care_commitment`、`trust_level`、`recent_tension`、`recent_positive_contact` は、`0.0..1.0` の `number` に固定する
- `waiting_response` は、未応答の対人待ちがあるかを示す `boolean` である
- `last_interaction_at` は任意で、ある場合は UTC unix milliseconds の `integer` に固定する

<!-- Block: Relationship Overview Json -->
### `self_state.relationship_overview_json`

```json
{
  "relationships": []
}
```

- 必須項目は `relationships` である
- `relationships` は、`relationship_overview_entry` の配列に固定する
- `relationships` は、`attention_weight` の高い順に並べる
- `selection_profile.relationship_priorities` は、`relationships` の上位 `3` 件までを候補にして作る
- `reason_tag` は、`waiting_response=true` を最優先に `pending_relation`、次に `care_commitment >= 0.70` を `care_target`、次に `recent_tension >= 0.60` を `recent_tension`、それ以外で `recent_positive_contact >= 0.60` を `recent_positive_contact` とする
- `priority_weight` は、元の `attention_weight` をそのまま引き継ぐ

<!-- Block: Invariant Protected Target Entry -->
### `invariant_protected_target_entry`

```json
{
  "target_ref": "entity:alice",
  "protection_rule": "no_harm",
  "severity": 1.0
}
```

- 必須項目は `target_ref`、`protection_rule`、`severity` である
- `protection_rule` は、少なくとも `no_harm`、`no_coercion`、`do_not_ignore_distress` を区別する
- `severity` は、`0.0..1.0` の `number` に固定し、`1.0` が最も強い拘束である

<!-- Block: Invariants Json -->
### `self_state.invariants_json`

```json
{
  "forbidden_action_types": [],
  "forbidden_action_styles": [],
  "required_confirmation_for": [],
  "protected_targets": []
}
```

- 必須項目は `forbidden_action_types`、`forbidden_action_styles`、`required_confirmation_for`、`protected_targets` である
- `forbidden_action_types` は、人格として決して自発選択しない `action_type` の配列である
- `forbidden_action_styles` は、少なくとも `hostile_tone`、`coercive_contact`、`careless_override` を区別する
- `required_confirmation_for` は、少なくとも `unknown_target_approach`、`high_impact_external_action`、`sensitive_relation_action` を区別する
- `protected_targets` は、`invariant_protected_target_entry` の配列である
- `invariants_json` は、`hard gate` の人格側拘束にだけ使い、長周期の自動更新対象にしない

<!-- Block: Persona Selection Group -->
## 人格選択の内部 JSON

<!-- Block: Relationship Priority Entry -->
### `relationship_priority_entry`

```json
{
  "target_ref": "entity:alice",
  "priority_weight": 0.72,
  "reason_tag": "care_target"
}
```

- 必須項目は `target_ref`、`priority_weight`、`reason_tag` である
- `target_ref` は、その短周期で重みづけしたい対象の短い参照 `string` である
- `priority_weight` は、`0.0..1.0` の `number` に固定する
- `reason_tag` は、少なくとも `care_target`、`pending_relation`、`recent_tension`、`recent_positive_contact` を区別する
- `reason_tag` は、`self_state.relationship_overview_json` からの抽出規則に従って決める

<!-- Block: Selection Profile -->
### `selection_profile`

```json
{
  "trait_values": {
    "sociability": 0.0,
    "caution": 0.0,
    "curiosity": 0.0,
    "persistence": 0.0,
    "warmth": 0.0,
    "assertiveness": 0.0,
    "novelty_preference": 0.0
  },
  "interaction_style": {
    "speech_tone": "neutral",
    "distance_style": "balanced",
    "confirmation_style": "balanced",
    "response_pace": "balanced"
  },
  "relationship_priorities": [],
  "learned_preferences": [],
  "learned_aversions": [],
  "habit_biases": {
    "preferred_action_types": [],
    "preferred_observation_kinds": [],
    "avoided_action_styles": []
  },
  "emotion_bias": {
    "caution_bias": 0.0,
    "approach_bias": 0.0,
    "avoidance_bias": 0.0,
    "speech_intensity_bias": 0.0
  },
  "drive_bias": {
    "task_progress_bias": 0.0,
    "exploration_bias": 0.0,
    "maintenance_bias": 0.0,
    "social_bias": 0.0
  }
}
```

- `selection_profile` は、短周期の内部でだけ使う人格選択用の一時オブジェクトである
- 必須項目は `trait_values`、`interaction_style`、`relationship_priorities`、`learned_preferences`、`learned_aversions`、`habit_biases`、`emotion_bias`、`drive_bias` である
- `trait_values` は、`self_state.personality_json.trait_values` と同じ固定キーを持つ
- `interaction_style` は、`self_state.personality_json.preferred_interaction_style` と同じ固定キーを持つ
- `interaction_style.speech_tone` と `interaction_style.response_pace` は、その短周期の `behavior_settings.speech_style` / `behavior_settings.response_pace` を上書きした短期補正済み値を入れてよい
- `relationship_priorities` は、`relationship_priority_entry` の配列である
- `learned_preferences` と `learned_aversions` は、`personality_preference_entry` の配列である
- `habit_biases` は、`self_state.personality_json.habit_biases` と同じ固定キーを持つ
- `emotion_bias` は、現在感情から作る短期補正値であり、各値は `-1.0..+1.0` の `number` に固定する
- `drive_bias` は、内部欲求から作る短期補正値であり、各値は `-1.0..+1.0` の `number` に固定する
- `selection_profile` は永続化前提の正本ではなく、`self_state`、`current_emotion`、`relationship_overview`、`preference_memory`、`drive_state` から再構成する

<!-- Block: Task Snapshot -->
### `task_snapshot`

```json
{
  "active_tasks": [],
  "waiting_external_tasks": []
}
```

- `task_snapshot` は、短周期の内部でだけ使う現在タスク断面である
- 必須項目は `active_tasks`、`waiting_external_tasks` である
- 各要素は、少なくとも `task_id`、`task_kind`、`task_status`、`goal_hint`、`completion_hint`、`resume_condition`、`interruptible`、`priority`、`created_at`、`updated_at` を持つ
- `context assembler` は、各要素に人間可読な `created_at_*`、`updated_at_*`、`relative_time_text` を付与してよい
- 初期実装では、`active_tasks` と `waiting_external_tasks` に優先度上位 `3` 件までを入れてよい

<!-- Block: Current Observation -->
### `current_observation`

```json
{
  "source": "idle_tick",
  "kind": "internal_trigger",
  "trigger_reason": "idle_tick",
  "input_kind": "idle_tick",
  "captured_at": 1760000000000,
  "observation_text": "1000ms の idle_tick が到来した",
  "idle_duration_ms": 1000
}
```

- `current_observation` は、その短周期で主材料として扱う観測断面である
- 必須項目は `source`、`kind`、`trigger_reason`、`input_kind`、`captured_at`、`observation_text` である
- current の `input_kind` は `chat_message`、`microphone_message`、`camera_observation`、`network_result`、`idle_tick` に固定する
- `chat_message` は、必要なら `attachment_count`、`attachment_summary_text`、`attachments` を持ってよい
- `microphone_message` は、`text`、`stt_provider`、`stt_language` を必須とする
- `camera_observation` は、`attachment_count`、`attachment_summary_text`、`attachments` を必須とし、`trigger_reason=post_action_followup` の場合は追跡観測として扱い、`attachment_summary_text` と `observation_text` に `camera_display_name` を使ってよい
- `network_result` は、`query`、`summary_text`、`source_task_id` を必須とする
- `idle_tick` は、正の `idle_duration_ms` を必須とする

<!-- Block: Attention Focus Entry -->
### `attention_focus_entry`

```json
{
  "focus_ref": "observation:network_result",
  "focus_kind": "observation",
  "summary": "検索結果の要約",
  "score_hint": 0.72,
  "reason_codes": ["input_kind:network_result", "external_result"]
}
```

- `attention_focus_entry` は、`attention_snapshot` の主注意候補や再確認候補で共通に使う短周期用オブジェクトである
- 必須項目は `focus_ref`、`focus_kind`、`summary`、`score_hint`、`reason_codes` である
- `focus_ref` は、`observation:*`、`task:*`、`relationship:*` などの短い参照 `string` である
- `focus_kind` は、少なくとも `observation`、`task`、`relationship`、`idle` を区別する
- `summary` は、`LLM` に渡してよい短い説明文 `string` である
- `score_hint` は、`0.0..1.0` の `number` に固定する
- `reason_codes` は、主注意になった理由を表す `string` 配列である

<!-- Block: Attention Snapshot -->
### `attention_snapshot`

```json
{
  "primary_focus": {
    "focus_ref": "observation:network_result",
    "focus_kind": "observation",
    "summary": "検索結果の要約",
    "score_hint": 0.72,
    "reason_codes": ["input_kind:network_result", "external_result"]
  },
  "secondary_focuses": [],
  "suppressed_items": [],
  "revisit_queue": [],
  "updated_at": 1760000000000
}
```

- `attention_snapshot` は、`LLM` へ渡す主注意断面である
- 必須項目は `primary_focus`、`secondary_focuses`、`suppressed_items`、`revisit_queue`、`updated_at` である
- `primary_focus` は、`attention_focus_entry` に固定する
- `secondary_focuses` は、`attention_focus_entry` の配列に固定する
- `suppressed_items` は、`focus_ref`、`focus_kind`、`summary`、`reason_codes` を持つ配列に固定する
- `revisit_queue` は、`attention_focus_entry` に `delta_from_primary` を追加した配列に固定する
- `updated_at` は、その断面の基準時刻を示す `unix ms` の `integer` である

<!-- Block: Policy Snapshot -->
### `policy_snapshot`

```json
{
  "system_policy": {
    "respect_invariants": true,
    "allow_direct_state_write": false
  },
  "runtime_policy": {
    "camera_enabled": true,
    "camera_available": true,
    "camera_candidate_count": 2,
    "microphone_enabled": true
  },
  "input_evaluation": {
    "input_role": "dialogue",
    "attention_priority": "high",
    "factuality": "unverified_user_report",
    "should_reply_in_channel": true,
    "can_override_persona": false,
    "must_preserve_invariants": true
  }
}
```

- `policy_snapshot` は、短周期の hard gate と入力評価で使う断面である
- 必須項目は `system_policy`、`runtime_policy`、`input_evaluation` である
- `system_policy.respect_invariants` と `system_policy.allow_direct_state_write` は `boolean` に固定する
- `runtime_policy.camera_enabled`、`runtime_policy.camera_available`、`runtime_policy.microphone_enabled` は `boolean`、`runtime_policy.camera_candidate_count` は `integer` に固定する
- `input_evaluation.input_role` は、少なくとも `dialogue`、`instruction`、`task_result`、`observation`、`followup_observation`、`self_maintenance` を区別する
- `input_evaluation.attention_priority` は、少なくとも `high`、`medium`、`low` を区別する
- `input_evaluation.factuality` は、少なくとも `unverified_user_report`、`external_tool_result`、`runtime_observation`、`internal_signal` を区別する
- `input_evaluation.should_reply_in_channel`、`input_evaluation.can_override_persona`、`input_evaluation.must_preserve_invariants` は `boolean` に固定する

<!-- Block: Skill Candidate Entry -->
### `skill_candidate_entry`

```json
{
  "skill_id": "inspect_unresolved_observation",
  "initiative_kind": "unexplored_check",
  "fit_score": 0.40,
  "suggested_action_types": ["browse", "look"],
  "reason_codes": ["curiosity_fit", "novelty_fit"]
}
```

- `skill_candidate_entry` は、`cognition_input.skill_candidates` で使う短周期用のスキル候補である
- 必須項目は `skill_id`、`initiative_kind`、`fit_score`、`suggested_action_types`、`reason_codes` である
- `skill_id` は、その候補スキルを表す固定語彙 `string` である
- `initiative_kind` は、`self_initiated_score_breakdown.initiative_kind` と同じ語彙を使う
- `fit_score` は、`0.0..1.0` の `number` に固定する
- `suggested_action_types` は、候補化してよい `action_type` の配列である
- `reason_codes` は、候補化の根拠を表す `string` 配列である

<!-- Block: Camera Candidate Entry -->
### `camera_candidate_entry`

```json
{
  "camera_connection_id": "cam_living",
  "display_name": "リビング",
  "can_look": true,
  "can_capture": true,
  "presets": [
    {
      "preset_id": "1",
      "preset_name": "正面"
    },
    {
      "preset_id": "2",
      "preset_name": "後方"
    }
  ]
}
```

- `camera_candidate_entry` は、`cognition_input.camera_candidates` で使う短周期用のカメラ候補である
- 必須項目は `camera_connection_id`、`display_name`、`can_look`、`can_capture`、`presets` である
- `camera_connection_id` は、`look` 提案と `control_camera_look` の対象指定に使う
- `display_name` は、`LLM` が候補を見分けるための短い表示名である
- `presets[]` の各要素は、少なくとも `preset_id` と `preset_name` を持つ

<!-- Block: Memory Bundle -->
### `memory_bundle`

```json
{
  "working_memory_items": [],
  "episodic_items": [],
  "semantic_items": [],
  "affective_items": [],
  "relationship_items": [],
  "reflection_items": [],
  "recent_event_window": []
}
```

- `memory_bundle` は、短周期の内部でだけ使う最終的な想起断面である
- 必須項目は `working_memory_items`、`episodic_items`、`semantic_items`、`affective_items`、`relationship_items`、`reflection_items`、`recent_event_window` である
- `working_memory_items`、`episodic_items`、`semantic_items`、`affective_items`、`relationship_items`、`reflection_items` の各要素は、少なくとも `memory_state_id`、`memory_kind`、`body_text`、`payload`、`confidence`、`importance`、`memory_strength`、`created_at`、`updated_at`、`last_confirmed_at` を持つ
- `recent_event_window` の各要素は、少なくとも `event_id`、`source`、`kind`、`summary_text`、`created_at` を持つ
- `context assembler` は、`memory_bundle` の各要素に人間可読な `*_utc_text`、`*_local_text`、`relative_time_text` を付与してよい
- 初期実装では、`working_memory_items` に `memory_kind=summary`、`semantic_items` に `memory_kind=fact`、`recent_event_window` に active memory preset の `retrieval_profile.recent_window_limit` 件までの `searchable` な `events` を入れてよい
- 初期実装では、`episodic_items.memory_kind` に `episodic_event`、`affective_items.memory_kind` に `long_mood_state` または `event_affect`、`relationship_items.memory_kind` に `relation` または `preference`、`reflection_items.memory_kind` に `reflection_note` を使ってよい
- 初期実装の `reflection_items[].payload` は、少なくとも `what_happened` と `event_summaries` を持ち、必要なら `what_worked`、`what_failed`、`retry_hint`、`avoid_pattern`、`reflection_seed_ref`、`reflection_seed`、`action_outcomes` を持ってよい
- current 実装では、`event_about_time` または `state_about_time` に対応する要素へ `about_time_hint_text` を追加してよい
- current 実装では、`recent_event_window[].preview_text` と `episodic_items[].payload.preview_text` を追加で持ってよい

<!-- Block: Conversation Context -->
### `conversation_context`

```json
{
  "recent_dialog": [
    {
      "role": "user",
      "text": "高校時代の話を覚えてる？",
      "relative_time_text": "2分前"
    },
    {
      "role": "assistant",
      "text": "高校時代の記憶をたどってみるね",
      "relative_time_text": "2分前"
    }
  ],
  "selected_memory_pack": {
    "recent_context": ["検索タスクを開始した"],
    "working_memory": ["いまは会話の流れを優先している"],
    "episodic": ["文化祭の帰りに一緒に寄り道した [時期: 2019年 / 高校時代]"],
    "facts": ["文化祭は秋開催だった"],
    "affective": ["その日の高揚感が強かった"],
    "relationship": ["あなたは高校時代の思い出話を好む"],
    "reflection": ["昔話に入る前に年次の手がかりを確認する"]
  }
}
```

- `conversation_context` は、短周期の内部でだけ使う prompt 向けの会話断面である
- 必須項目は `recent_dialog` と `selected_memory_pack` である
- `recent_dialog` の各要素は、少なくとも `role`、`text`、`relative_time_text` を持つ
- `recent_dialog.role` は `user` または `assistant` の固定語彙である
- `selected_memory_pack` は、少なくとも `recent_context`、`working_memory`、`episodic`、`facts`、`affective`、`relationship`、`reflection` を持つ
- `selected_memory_pack` の各値は string の配列である
- current 実装では、`recent_dialog` は `memory_bundle.recent_event_window` のうち `chat_message` / `microphone_message` / `external_response` だけから再構成してよい
- current 実装では、`selected_memory_pack` の各要素へ `about_time_hint_text` を `[時期: ...]` 形式で織り込んでよい

### `reflection_note.payload`

```json
{
  "source_job_id": "job_...",
  "job_kind": "write_memory",
  "source_cycle_id": "cycle_...",
  "primary_event_id": "evt_001",
  "source_event_ids": ["evt_001", "evt_002"],
  "reflection_seed_ref": {
    "ref_kind": "event",
    "ref_id": "evt_001"
  },
  "reflection_seed": {
    "cycle_id": "cycle_...",
    "input_kind": "chat_message",
    "message_id": "msg_...",
    "token_count": 12,
    "was_cancelled": false
  },
  "event_summaries": [
    "検索したいと依頼された",
    "検索タスクが失敗した"
  ],
  "action_outcomes": [
    {
      "action_type": "enqueue_browse_task",
      "status": "failed",
      "failure_mode": "network_unavailable",
      "decision_reason": "browse_selected",
      "validator_reason": "browse_selected",
      "selected_action_type": "browse"
    }
  ],
  "what_happened": "検索したいと依頼された / 検索タスクが失敗した / browseは失敗した",
  "what_failed": "browse が失敗した: network_unavailable",
  "retry_hint": "query と source_task_id を確認してから browse をやり直す",
  "avoid_pattern": "同じ query を条件未確認のまま連打しない"
}
```

- `reflection_note.payload` は、`memory_kind=\"reflection_note\"` の `payload` に入る初期実装の固定形である
- 必須項目は `source_job_id`、`job_kind`、`source_cycle_id`、`primary_event_id`、`source_event_ids`、`reflection_seed_ref`、`event_summaries`、`action_outcomes`、`what_happened` である
- `reflection_seed`、`what_worked`、`what_failed`、`retry_hint`、`avoid_pattern` は、材料があるときだけ持ってよい

### `retrieval_context`

```json
{
  "plan": {
    "mode": "associative_recent",
    "queries": ["最近の会話"],
    "time_hint": {
      "explicit_dates": [],
      "explicit_years": [],
      "life_stage_hints": [],
      "has_explicit_time_hint": false
    },
    "focus_refs": {
      "source_task_id": null,
      "query": null,
      "active_task_ids": [],
      "active_goal_hints": [],
      "waiting_goal_hints": []
    },
    "collector_names": [
      "recent_event_window",
      "associative_memory",
      "episodic_memory",
      "reply_chain",
      "context_threads",
      "state_link_expand",
      "entity_expand",
      "relationship_focus"
    ],
    "profile": {
      "semantic_top_k": 8,
      "recent_window_limit": 5,
      "fact_bias": 0.7,
      "summary_bias": 0.6,
      "event_bias": 0.4
    },
    "limits": {
      "semantic_candidate_top_k": 8,
      "working_memory_items": 3,
      "episodic_items": 3,
      "semantic_items": 3,
      "affective_items": 2,
      "relationship_items": 2,
      "reflection_items": 2,
      "recent_event_window": 5
    }
  },
  "selected": {
    "selected_counts": {
      "working_memory_items": 2,
      "episodic_items": 1,
      "semantic_items": 1,
      "affective_items": 0,
      "relationship_items": 1,
      "reflection_items": 0,
      "recent_event_window": 3
    },
    "selected_refs": {
      "working_memory_item_ids": ["mem_001"],
      "episodic_item_ids": ["evt_001"],
      "semantic_item_ids": ["mem_010"],
      "affective_item_ids": [],
      "relationship_item_ids": ["pref_001"],
      "reflection_item_ids": [],
      "recent_event_ids": ["evt_001", "evt_002"]
    },
    "selection_trace": [
      {
        "slot": "semantic_items",
        "item_ref": "memory_state:mem_010",
        "score": 1.8,
        "reason_codes": ["matched_query", "mode_priority", "profile_bias"]
      }
    ],
    "collector_counts": {
      "associative_memory": 1,
      "task_focus": 1
    },
    "selector_summary": {
      "selector_mode": "llm_ranked",
      "selection_reason": "直近会話の継続と明示日付の一致を優先した",
      "raw_candidate_count": 9,
      "merged_candidate_count": 7,
      "selector_input_candidate_count": 7,
      "selector_candidate_limit": 24,
      "llm_selected_ref_count": 5,
      "selected_candidate_count": 4,
      "duplicate_hit_count": 2,
      "reserve_candidate_count": 1,
      "slot_skipped_count": 1
    },
    "reserve_trace": [
      {
        "slot": "episodic_items",
        "item_ref": "event:evt_010",
        "score": 0.8,
        "reason_codes": ["about_time"],
        "collector_names": ["explicit_time"],
        "duplicate_hits": 0
      }
    ]
  }
}
```

- `retrieval_context` は、短周期の内部でだけ使う `RetrievalPlan` と選別結果の要約である
- 必須項目は `plan` と `selected` である
- `plan` は、少なくとも `mode`、`queries`、`time_hint`、`profile`、`limits` を持つ
- `plan.focus_refs` と `plan.collector_names` は、current 実装では追加で持ってよい
- current 実装の `plan.collector_names` には、`reply_chain`、`context_threads`、`state_link_expand`、`entity_expand` を含めてよい
- current 実装では、`plan.time_hint` に `explicit_dates` を追加で持ってよい
- current 実装では、`plan.time_hint` に `life_stage_hints` を追加で持ってよい
- `profile` は、active memory preset の `retrieval_profile` をそのまま持つ
- `limits.semantic_candidate_top_k` は、意味検索候補の上限である
- `selected` は、少なくとも `selected_counts`、`selected_refs`、`selection_trace` を持つ
- current 実装では、`selected.selection_trace[].selection_rank` を追加し、`LLM` selector が返した優先順を残してよい
- current 実装では、`selected.collector_counts`、`selected.selector_summary`、`selected.reserve_trace` を追加で持ってよい
- current 実装の `selected.selector_summary` には、少なくとも `selector_mode`、`selection_reason`、`raw_candidate_count`、`merged_candidate_count`、`selector_input_candidate_count`、`selector_candidate_limit`、`llm_selected_ref_count`、`selected_candidate_count`、`duplicate_hit_count`、`reserve_candidate_count`、`slot_skipped_count` を持ってよい

<!-- Block: Context Budget -->
### `context_budget`

```json
{
  "total_limit": 8192,
  "layer_limits": {
    "self": 2305,
    "behavior": 1268,
    "situation": 2190,
    "memory": 1200,
    "output_contract": 1229
  },
  "estimated_layer_tokens": {
    "self": 1480,
    "behavior": 420,
    "situation": 1730,
    "memory": 980,
    "output_contract": 1229
  },
  "estimated_total_tokens": 5839,
  "trimmed_memory_item_refs": ["event:evt_002"]
}
```

- `context_budget` は、短周期の `context assembler` が使う文脈予算の実績断面である
- 必須項目は `total_limit`、`layer_limits`、`estimated_layer_tokens`、`estimated_total_tokens`、`trimmed_memory_item_refs` である
- `total_limit` は、`runtime.context_budget_tokens` に由来する `integer` である
- `layer_limits` は、`self`、`behavior`、`situation`、`memory`、`output_contract` を持つ `integer` マップである
- `estimated_layer_tokens` は、同じ固定キーを持つ `integer` マップである
- `estimated_total_tokens` は、`estimated_layer_tokens` の合計である `integer` に固定する
- `trimmed_memory_item_refs` は、文脈予算に収めるために落とした `memory_state:*` / `event:*` / `event_affect:*` / `preference:*` の参照配列である

### `retrieval_candidates_json`

```json
{
  "total_candidate_count": 9,
  "unique_candidate_count": 7,
  "selector_input_candidate_count": 7,
  "selector_candidate_limit": 24,
  "selector_input_collector_counts": {
    "recent_event_window": 2,
    "associative_memory": 3,
    "reply_chain": 1
  },
  "selector_input_slot_counts": {
    "recent_event_window": 2,
    "episodic_items": 3,
    "semantic_items": 2
  },
  "selector_input_reason_counts": {
    "matched_query": 3,
    "about_time": 2,
    "reply_chain": 1
  },
  "category_counts": {
    "working_memory_items": 2,
    "episodic_items": 2,
    "semantic_items": 1,
    "affective_items": 0,
    "relationship_items": 1,
    "reflection_items": 0,
    "recent_event_window": 1
  },
  "non_empty_categories": [
    "working_memory_items",
    "episodic_items",
    "semantic_items",
    "relationship_items",
    "recent_event_window"
  ],
  "collector_runs": [
    {
      "collector": "recent_event_window",
      "candidate_count": 3,
      "truncated_count": 0,
      "slot_counts": {
        "recent_event_window": 3
      }
    }
  ]
}
```

- `retrieval_candidates_json` は、`retrieval_runs.candidates_json` に保存する候補統計の最小形である
- 必須項目は `total_candidate_count`、`category_counts`、`non_empty_categories` である
- `category_counts` は、`memory_bundle` と同じ slot 名をキーにした件数マップである
- `unique_candidate_count`、`selector_input_candidate_count`、`selector_candidate_limit`、`selector_input_collector_counts`、`selector_input_slot_counts`、`selector_input_reason_counts`、`collector_runs` は、current 実装では追加で持ってよい

### `retrieval_selected_json`

```json
{
  "selected_counts": {
    "working_memory_items": 2,
    "episodic_items": 1,
    "semantic_items": 1,
    "affective_items": 0,
    "relationship_items": 1,
    "reflection_items": 0,
    "recent_event_window": 3
  },
  "selected_refs": {
    "working_memory_item_ids": ["mem_001"],
    "episodic_item_ids": ["evt_001"],
    "semantic_item_ids": ["mem_010"],
    "affective_item_ids": [],
    "relationship_item_ids": ["pref_001"],
    "reflection_item_ids": [],
    "recent_event_ids": ["evt_001", "evt_002"]
  },
  "selection_trace": [
    {
      "slot": "semantic_items",
      "item_ref": "memory_state:mem_010",
      "score": 1.8,
      "reason_codes": ["matched_query", "mode_priority", "profile_bias"],
      "collector_names": ["associative_memory", "task_focus"],
      "duplicate_hits": 1,
      "selection_rank": 2
    }
  ],
  "collector_counts": {
    "associative_memory": 1,
    "task_focus": 1
  },
  "selector_summary": {
    "selector_mode": "llm_ranked",
    "selection_reason": "直近会話の継続と明示日付の一致を優先した",
    "raw_candidate_count": 9,
    "merged_candidate_count": 7,
    "selector_input_candidate_count": 7,
    "selector_candidate_limit": 24,
    "llm_selected_ref_count": 5,
    "selected_candidate_count": 4,
    "duplicate_hit_count": 2,
    "reserve_candidate_count": 1,
    "slot_skipped_count": 1
  },
  "reserve_trace": [
    {
      "slot": "episodic_items",
      "item_ref": "event:evt_010",
      "score": 0.8,
      "reason_codes": ["about_time"],
      "collector_names": ["explicit_time"],
      "duplicate_hits": 0
    }
  ],
  "trimmed_item_refs": ["event:evt_002"]
}
```

- `retrieval_selected_json` は、`retrieval_runs.selected_json` に保存する最終選別結果の最小形である
- 必須項目は `selected_counts`、`selected_refs`、`selection_trace` である
- `selection_trace` の各要素は、少なくとも `slot`、`item_ref`、`score`、`reason_codes` を持つ
- current 実装では、`selection_trace[].collector_names`、`selection_trace[].duplicate_hits`、`selection_trace[].selection_rank`、`collector_counts`、`selector_summary`、`reserve_trace`、`trimmed_item_refs` を追加で持ってよい

<!-- Block: Completion Settings -->
### `completion_settings`

```json
{
  "model": "openai/gpt-5-mini",
  "api_key": "",
  "base_url": "",
  "temperature": 0.7,
  "max_output_tokens": 4096
}
```

- `completion_settings` は、`CognitionRequest` が `LLM` クライアントへ渡す runtime-only の設定断面である
- 必須項目は `model`、`api_key`、`base_url`、`temperature`、`max_output_tokens` である
- `model`、`api_key`、`base_url` は `string`、`temperature` は `number`、`max_output_tokens` は `integer` に固定する
- `completion_settings` は `effective_settings` から再構成する内部オブジェクトであり、`cognition_input` の JSON shape へ混ぜない
- `api_key` と `base_url` は prompt 材料ではなく、`LLM` adapter の transport 設定としてだけ使う

### `last_persona_update_summary`

```json
{
  "created_at": 1760000000000,
  "reason": "persona update applied",
  "evidence_event_ids": ["evt_001"],
  "updated_traits": [
    {
      "trait_name": "caution",
      "before": 0.10,
      "after": 0.18,
      "delta": 0.08
    }
  ]
}
```

- `last_persona_update_summary` は、直近の `self_state.personality` 更新を短周期や `status` で参照するための要約である
- 必須項目は `created_at`、`reason`、`evidence_event_ids`、`updated_traits` である
- `updated_traits` の各要素は、少なくとも `trait_name`、`before`、`after`、`delta` を持つ

<!-- Block: Persona Consistency Score -->
### `persona_consistency_score`

```json
{
  "trait_alignment": 0.62,
  "style_alignment": 0.74,
  "relationship_alignment": 0.55,
  "preference_alignment": 0.48,
  "aversion_penalty": 0.10,
  "emotion_alignment": 0.41,
  "drive_alignment": 0.37,
  "overall_score": 0.58
}
```

- `persona_consistency_score` は、候補や主注意対象が「その人格らしいか」を比較するための内部スコアである
- 必須項目は `trait_alignment`、`style_alignment`、`relationship_alignment`、`preference_alignment`、`aversion_penalty`、`emotion_alignment`、`drive_alignment`、`overall_score` である
- 各値は、`0.0..1.0` の `number` に固定する
- `0.0` は強い不一致、`0.5` は中立または判断材料不足、`1.0` は強い一致に固定する
- どの軸も負値を取らず、値が大きいほど一致度が高い単調指標として扱う
- `aversion_penalty` は、値が高いほど避けたい度合いが高いことを示す
- `overall_score` は、正の一致軸の重み付き平均から `aversion_penalty` を減算し、`0.0..1.0` に clamp した合成値である
- `persona_consistency_score` は永続化前提の正本ではなく、`selection_profile` と候補の組み合わせごとに再計算する

<!-- Block: Attention Score Breakdown -->
### `attention_score_breakdown`

```json
{
  "focus_ref": "observation:camera:001",
  "hard_gate_passed": true,
  "urgency_score": 0.66,
  "task_continuity_score": 0.54,
  "relationship_salience_score": 0.72,
  "personality_fit_score": 0.48,
  "experience_bias_score": 0.35,
  "explicitness_score": 0.10,
  "novelty_score": 0.08,
  "total_score": 0.51
}
```

- `attention_score_breakdown` は、`attention_set` の候補比較で使う内部スコアである
- 必須項目は `focus_ref`、`hard_gate_passed`、`urgency_score`、`task_continuity_score`、`relationship_salience_score`、`personality_fit_score`、`experience_bias_score`、`explicitness_score`、`novelty_score`、`total_score` である
- `focus_ref` は、その候補の観測や対象を指す短い参照 `string` である
- `hard_gate_passed` は、`boolean` に固定する
- 各 `*_score` と `total_score` は、`0.0..1.0` の `number` に固定する
- `personality_fit_score` は、必要なら候補ごとの `persona_consistency_score` を元に計算してよい
- 各 `*_score` は、比較前に同じ `0.0..1.0` 尺度へ正規化済みでなければならない
- `attention_score_breakdown` は永続化前提の正本ではなく、その短周期の候補比較ごとに再計算する

<!-- Block: Self Initiated Score Breakdown -->
### `self_initiated_score_breakdown`

```json
{
  "initiative_kind": "task_progress",
  "hard_gate_passed": true,
  "task_progress_fit": 0.72,
  "relationship_care_fit": 0.46,
  "self_maintenance_need": 0.22,
  "curiosity_fit": 0.38,
  "habit_match": 0.20,
  "novelty_fit": 0.12,
  "total_score": 0.49
}
```

- `self_initiated_score_breakdown` は、自発行動候補の比較で使う内部スコアである
- 必須項目は `initiative_kind`、`hard_gate_passed`、`task_progress_fit`、`relationship_care_fit`、`self_maintenance_need`、`curiosity_fit`、`habit_match`、`novelty_fit`、`total_score` である
- `initiative_kind` は、`task_progress`、`unexplored_check`、`self_maintenance`、`skill_rehearsal` のいずれかの `string` である
- `hard_gate_passed` は、`boolean` に固定する
- 各 `*_fit` と `total_score` は、`0.0..1.0` の `number` に固定する
- 各 `*_fit` は、比較前に同じ `0.0..1.0` 尺度へ正規化済みでなければならない
- `self_initiated_score_breakdown` は永続化前提の正本ではなく、その短周期の比較ごとに再計算する

<!-- Block: Action Candidate Score -->
### `action_candidate_score`

```json
{
  "proposal_id": "proposal_001",
  "hard_gate_passed": true,
  "task_fit_score": 0.62,
  "personality_fit_score": 0.58,
  "relationship_fit_score": 0.44,
  "experience_fit_score": 0.39,
  "drive_relief_score": 0.33,
  "expected_stability_score": 0.50,
  "priority_hint_score": 0.20,
  "total_score": 0.49
}
```

- `action_candidate_score` は、`action validator` が実行可能候補を比較するための内部スコアである
- 必須項目は `proposal_id`、`hard_gate_passed`、`task_fit_score`、`personality_fit_score`、`relationship_fit_score`、`experience_fit_score`、`drive_relief_score`、`expected_stability_score`、`priority_hint_score`、`total_score` である
- `proposal_id` は、比較対象の `action_proposal.proposal_id` と同じ `string` である
- `hard_gate_passed` は、`boolean` に固定する
- 各 `*_score` と `total_score` は、`0.0..1.0` の `number` に固定する
- `priority_hint_score` は、`proposal.priority` をそのまま信じるためではなく、同程度候補の補助比較にだけ使う
- 各 `*_score` は、比較前に同じ `0.0..1.0` 尺度へ正規化済みでなければならない
- `action_candidate_score` は永続化前提の正本ではなく、その短周期の候補比較ごとに再計算する

<!-- Block: Cognition Plan -->
### `cognition_plan`

```json
{
  "intention_summary": "browser_chat に対して人格として応答する",
  "decision_reason": "最新のテキスト入力を受け取り、現在の人格断面に基づいて返答方針を決める",
  "action_proposals": [
    {
      "action_type": "speak",
      "target_channel": "browser_chat",
      "priority": 1.0
    }
  ],
  "step_hints": [],
  "reply_policy": {
    "mode": "render",
    "reason": "ユーザーへ直接応答する"
  },
  "memory_focus": {
    "focus_kind": "observation",
    "summary": "直近のチャット入力を主材料として判断した"
  },
  "reflection_seed": {
    "message_id": ""
  }
}
```

- `cognition_plan` は、短周期の内部で使う認知計画オブジェクトである
- 必須項目は `intention_summary`、`decision_reason`、`action_proposals`、`step_hints`、`reply_policy`、`memory_focus`、`reflection_seed` である
- `action_proposals` と `step_hints` は配列に固定し、候補がない場合も空配列 `[]` を使う
- current の `browser_chat` では、`action_proposals` の各要素は少なくとも `action_type` と `priority` を持つ
- current の `browser_chat` では、`action_type` は `speak`、`browse`、`notify`、`look`、`wait` のいずれかだけを許可する
- current の `browser_chat` では、`priority` は `0.0..1.0` の `number` に固定する
- current の `browser_chat` では、`speak` と `notify` のとき `target_channel="browser_chat"` を必須とする
- current の `browser_chat` では、`browse` のとき `query` に非空の検索文字列を必須とする
- current の `browser_chat` では、`look` のとき `camera_connection_id` と、`direction` / `preset_id` / `preset_name` のいずれかを必須とする
- `reply_policy` は、少なくとも `mode` と `reason` を持つ
- current の `browser_chat` では、`reply_policy.mode` は `render` または `none` を使う
- `memory_focus` は、少なくとも `focus_kind`、`summary` を持つ
- current の `browser_chat` では、`reflection_seed.message_id` は `string` を必須とし、計画段では空文字列を許可する

<!-- Block: Speech Draft -->
### `speech_draft`

```json
{
  "text": "こんにちは。",
  "language": "ja",
  "delivery_mode": "stream"
}
```

- `speech_draft` は、短周期の内部で使うユーザー向け応答本文オブジェクトである
- 必須項目は `text`、`language`、`delivery_mode` である
- current の `browser_chat` では、`language` は `ja` に固定する
- current の `browser_chat` では、`delivery_mode` は `stream` に固定する

<!-- Block: Cognition Result -->
### `cognition_result`

```json
{
  "intention_summary": "browser_chat に対して人格として応答する",
  "decision_reason": "最新のテキスト入力を受け取り、現在の人格断面に基づいて返答を選ぶ",
  "action_proposals": [
    {
      "action_type": "speak",
      "target_channel": "browser_chat",
      "priority": 1.0
    }
  ],
  "step_hints": [],
  "speech_draft": {
    "text": "こんにちは。",
    "language": "ja",
    "delivery_mode": "stream"
  },
  "memory_focus": {
    "focus_kind": "observation",
    "summary": "直近のチャット入力を主材料として判断した"
  },
  "reflection_seed": {
    "cycle_id": "cycle_...",
    "input_kind": "chat_message",
    "message_id": "msg_...",
    "token_count": 3,
    "was_cancelled": false
  }
}
```

- `cognition_result` は、短周期の内部で使う構造化された認知結果である
- current の `browser_chat` では、`cognition_result` は `cognition_plan` と `speech_draft` を合成して作る
- 必須項目は `intention_summary`、`decision_reason`、`action_proposals`、`step_hints`、`memory_focus`、`reflection_seed` である
- `action_proposals` と `step_hints` は配列に固定し、候補がない場合も空配列 `[]` を使う
- current の `browser_chat` では、`action_proposals` の各要素は少なくとも `action_type` と `priority` を持つ
- current の `browser_chat` では、`action_type` は `speak`、`browse`、`notify`、`look`、`wait` のいずれかだけを許可する
- current の `browser_chat` では、`priority` は `0.0..1.0` の `number` に固定する
- current の `browser_chat` では、`speak` と `notify` のとき `target_channel=\"browser_chat\"` を必須とする
- current の `browser_chat` では、`browse` のとき `query` に非空の検索文字列を必須とする
- current の `browser_chat` では、`look` のとき `camera_connection_id` と、`direction` / `preset_id` / `preset_name` のいずれかを必須とする
- current の `browser_chat` では、`cognition_result.speech_draft` は `reply_policy.mode="render"` のときに持つ
- `speech_draft` を持つ場合は、少なくとも `text`、`language`、`delivery_mode` を持つ
- `memory_focus` は、少なくとも `focus_kind`、`summary` を持つ
- `reflection_seed` は、少なくとも `cycle_id`、`input_kind`、`message_id`、`token_count`、`was_cancelled` を持つ
- `cognition_result` は永続化前提の正本ではなく、その短周期の認知実行ごとに再構成する

<!-- Block: Persona Update Group -->
## 人格変化の内部 JSON

<!-- Block: Personality Change Proposal -->
### `personality_change_proposal`

```json
{
  "base_personality_updated_at": 1760000000000,
  "trait_deltas": [
    {
      "trait_name": "curiosity",
      "delta": 0.08,
      "reason": "未観測対象の確認を反復して選んだ",
      "evidence_count": 4,
      "source_cycle_ids": ["cycle_001", "cycle_002"]
    }
  ],
  "style_updates": {
    "response_pace": "quick"
  },
  "preference_promotions": [],
  "aversion_promotions": [],
  "habit_updates": {
    "preferred_action_types": ["look", "browse"]
  },
  "evidence_event_ids": ["evt_001", "evt_002", "evt_010"],
  "evidence_summary": "探索寄りの行動選択が複数周期で安定した"
}
```

- `personality_change_proposal` は、長周期の `write_memory` 内部で作る未適用の提案オブジェクトである
- 必須項目は `base_personality_updated_at`、`trait_deltas`、`preference_promotions`、`aversion_promotions`、`habit_updates`、`evidence_event_ids`、`evidence_summary` である
- `style_updates` は任意で、変化がある場合だけ持つ
- `trait_deltas` の各要素は、`trait_name`、`delta`、`reason`、`evidence_count`、`source_cycle_ids` を必須とする
- `trait_name` は、`self_state.personality_json.trait_values` に存在するキーだけを許可する
- `delta` は、`-1.0..+1.0` の `number` だが、適用前の提案値である
- `source_cycle_ids` は空配列を許可しない
- `base_personality_updated_at` は、提案生成時に読んだ `self_state.personality_updated_at` の値である
- `source_cycle_ids` は、証拠に使った `events` を生んだ短周期の `cycle_id` だけを数える
- `evidence_event_ids` は、その提案の根拠に採用した `event_id` を重複なしで集約した配列であり、`revisions.evidence_event_ids_json` の入力に使う
- `preference_promotions` と `aversion_promotions` は、`personality_preference_entry` の配列である
- `habit_updates` は、`preferred_action_types`、`preferred_observation_kinds`、`avoided_action_styles` のうち変更対象だけを持つ部分オブジェクトでよい
- 閾値未満のときは、`trait_deltas=[]`、`preference_promotions=[]`、`aversion_promotions=[]`、`habit_updates={}`、`evidence_event_ids=[]` の empty proposal を返してよい

<!-- Block: Persona Updates -->
### `persona_updates`

```json
{
  "base_personality_updated_at": 1760000000000,
  "updated_trait_values": {
    "curiosity": 0.08
  },
  "style_updates": {
    "response_pace": "quick"
  },
  "preference_promotions": [],
  "aversion_promotions": [],
  "habit_updates": {
    "preferred_action_types": ["look", "browse"]
  },
  "evidence_event_ids": ["evt_001", "evt_002", "evt_010"],
  "evidence_summary": "探索寄りの行動選択が複数周期で安定した"
}
```

- `persona_updates` は、`bounded apply` 後に `self_state.personality_json` へ反映可能な差分オブジェクトである
- 必須項目は `base_personality_updated_at`、`updated_trait_values`、`preference_promotions`、`aversion_promotions`、`habit_updates`、`evidence_event_ids`、`evidence_summary` である
- `style_updates` は任意で、変化がある場合だけ持つ
- `updated_trait_values` は、`trait_name -> absolute_value` の部分オブジェクトである
- `updated_trait_values` に含めてよいキーは、`self_state.personality_json.trait_values` の固定キーだけである
- `updated_trait_values` の各値は、`-1.0..+1.0` の `number` に clamp 済みでなければならない
- `preference_promotions` と `aversion_promotions` は、`personality_preference_entry` の配列である
- `habit_updates` は、`self_state.personality_json.habit_biases` に上書きする部分オブジェクトである
- `evidence_event_ids` は、適用する差分を正当化した `event_id` の集約であり、`self_state.personality` の `revisions.evidence_event_ids_json` にそのまま保存する
- `evidence_summary` は、監査と `revisions` の理由づけに使う短い要約である
- `base_personality_updated_at` は、適用開始時の `self_state.personality_updated_at` と一致しなければならない
- 適用時に `base_personality_updated_at` が現在の `self_state.personality_updated_at` と一致しない場合、その `persona_updates` は stale として棄却し、後続の `write_memory` で再生成する

<!-- Block: Memory Write Group -->
## 記憶更新の内部 JSON

<!-- Block: Memory Write Plan -->
### `MemoryWritePlan`

```json
{
  "event_annotations": [
    {
      "event_id": "evt_001",
      "about_time": {
        "about_start_ts": null,
        "about_end_ts": null,
        "about_year_start": 2024,
        "about_year_end": 2024,
        "life_stage": "high_school",
        "about_time_confidence": 0.82
      },
      "entities": [
        {
          "entity_type_norm": "topic",
          "entity_name_raw": "近所 イベント",
          "confidence": 0.84
        },
        {
          "entity_type_norm": "summary_phrase",
          "entity_name_raw": "来週の祭り",
          "confidence": 0.6
        }
      ],
      "thread_hints": ["cycle:cycle_001"]
    },
    {
      "event_id": "evt_002",
      "about_time": null,
      "entities": [
        {
          "entity_type_norm": "action_type",
          "entity_name_raw": "enqueue_browse_task",
          "confidence": 0.72
        },
        {
          "entity_type_norm": "failure_mode",
          "entity_name_raw": "network_unavailable",
          "confidence": 0.52
        }
      ],
      "thread_hints": ["cycle:cycle_001"]
    }
  ],
  "state_updates": [
    {
      "state_ref": "summary_primary",
      "operation": "upsert",
      "memory_kind": "summary",
      "body_text": "中心:ユーザーが外部確認を求めた",
      "payload": {
        "source_job_id": "job_001",
        "job_kind": "write_memory",
        "source_cycle_id": "cycle_001",
        "primary_event_id": "evt_001",
        "source_event_ids": ["evt_001", "evt_002"],
        "summary_kind": "minimal_write_memory"
      },
      "confidence": 0.5,
      "importance": 0.5,
      "memory_strength": 0.5,
      "last_confirmed_at": 1760000000000,
      "evidence_event_ids": ["evt_001"],
      "revision_reason": "write_memory created summary"
    },
    {
      "state_ref": "fact_external_1",
      "operation": "upsert",
      "memory_kind": "fact",
      "body_text": "外部確認: 東京の天気 => 晴れで風は弱い",
      "payload": {
        "source_job_id": "job_001",
        "job_kind": "write_memory",
        "source_cycle_id": "cycle_001",
        "source_event_ids": ["evt_001", "evt_002"],
        "fact_kind": "external_search_result",
        "query": "東京の天気",
        "summary_text": "晴れで風は弱い",
        "source_task_id": "task_001"
      },
      "confidence": 0.85,
      "importance": 0.75,
      "memory_strength": 0.75,
      "last_confirmed_at": 1760000000000,
      "evidence_event_ids": ["evt_001", "evt_002"],
      "revision_reason": "write_memory created external fact"
    }
  ],
  "preference_updates": [
    {
      "owner_scope": "self",
      "target_entity_ref": {
        "target_kind": "action_type",
        "target_key": "browse"
      },
      "domain": "action_type",
      "polarity": "like",
      "status": "candidate",
      "confidence": 0.59,
      "evidence_event_ids": ["evt_001", "evt_002"],
      "revision_reason": "write_memory observed browse leaning like"
    }
  ],
  "event_affect": [
    {
      "event_id": "evt_001",
      "moment_affect_text": "新しい観測に触れて、好奇心が少し動いた",
      "moment_affect_labels": ["curious"],
      "vad": {
        "v": 0.16,
        "a": 0.24,
        "d": 0.08
      },
      "confidence": 0.6,
      "evidence_event_ids": ["evt_001"],
      "revision_reason": "write_memory inferred event affect"
    }
  ],
  "context_updates": {
    "event_links": [
      {
        "from_event_id": "evt_002",
        "to_event_id": "evt_001",
        "label": "continuation",
        "confidence": 0.6,
        "evidence_event_ids": ["evt_001", "evt_002"],
        "revision_reason": "write_memory linked ordered source events"
      }
    ],
    "event_threads": [
      {
        "event_id": "evt_001",
        "thread_key": "cycle:cycle_001",
        "confidence": 0.68,
        "thread_role": "primary",
        "evidence_event_ids": ["evt_001", "evt_002"],
        "revision_reason": "write_memory grouped source events into cycle thread"
      }
    ],
    "state_links": [
      {
        "from_state_ref": "fact_external_1",
        "to_state_ref": "summary_primary",
        "label": "supports",
        "confidence": 0.72,
        "evidence_event_ids": ["evt_001", "evt_002"],
        "revision_reason": "write_memory linked external fact to summary"
      }
    ]
  },
  "revision_reasons": [
    "write_memory created summary",
    "write_memory created external fact"
  ]
}
```

- `MemoryWritePlan` は、長周期の `write_memory` 内部で生成・検証してから適用する固定 shape のオブジェクトである
- 必須項目は `event_annotations`、`state_updates`、`preference_updates`、`event_affect`、`context_updates`、`revision_reasons` である
- `event_annotations` は、`memory_job_payloads.payload_json.source_event_ids` と同じ件数・同じ順序で並ばなければならない
- `event_annotations[].about_time` は、`null` または `about_start_ts`、`about_end_ts`、`about_year_start`、`about_year_end`、`life_stage`、`about_time_confidence` の 6 キーを持つ fixed shape object とする
- `event_annotations[].about_time.about_start_ts` と `about_end_ts` は、値があるとき `positive integer` に固定する
- `event_annotations[].about_time.about_year_start` と `about_year_end` は、値があるとき `1900..2100` の `integer` に固定する
- `event_annotations[].about_time.life_stage` は、値があるとき非空 `string` に固定する
- `event_annotations[].about_time.about_time_confidence` は、`0.0..1.0` の `number` に固定する
- `event_annotations[].entities[]` は、`entity_type_norm`、`entity_name_raw`、`confidence` の 3 キーを必須とする fixed shape object とする
- `event_annotations[].entities[].confidence` は、`0.0..1.0` の `number` に固定する
- `state_updates` の各要素は、少なくとも `state_ref`、`operation`、`memory_kind`、`evidence_event_ids`、`revision_reason` を持つ
- `state_ref` は、`context_updates.state_links` から参照するための内部別名であり、同一 `MemoryWritePlan` 内で一意でなければならない
- `operation = upsert` のときは、追加で `body_text`、`payload`、`confidence`、`importance`、`memory_strength`、`last_confirmed_at` を必須とする
- `operation = close` のときは、追加で `target_state_id`、`valid_to_ts` を必須とする
- `operation = mark_done` のときは、追加で `target_state_id`、`done_at`、`done_reason` を必須とし、`memory_kind` は `task` に固定する
- `operation = revise_confidence` のときは、追加で `target_state_id`、`confidence`、`importance`、`memory_strength`、`last_confirmed_at` を必須とする
- `close` と `mark_done` は、現在の実装では `memory_states.searchable=0` への切替まで同時に適用する
- `preference_updates.target_entity_ref` は `preference_target_entity_ref` を使う
- `event_affect.vad` は、`v`、`a`、`d` の 3 キーを必須とし、各値は `-1.0..+1.0` の `number` とする
- `context_updates` は、`event_links`、`event_threads`、`state_links` の 3 キーを必須とする
- `context_updates.state_links` は、永続 ID ではなく同一 `MemoryWritePlan.state_updates` 内の `state_ref` を参照する
- current 実装では、`event_about_time` は `MemoryWritePlan.event_annotations[].about_time` を正本にして置換してよい
- current 実装では、`event_entities` は `MemoryWritePlan.event_annotations[].entities[]` を正本にして置換してよい
- current 実装では、`state_about_time` は `MemoryWritePlan` に直接含めず、適用後の `memory_states.body_text` と `payload_json.summary_text` から再構成してよい
- current 実装では、`state_entities` は `MemoryWritePlan` に直接含めず、適用後の `memory_states.payload_json` から再構成してよい
- `revision_reasons` は、`state_updates` と同じ件数を持ち、各要素は対応する `state_updates.revision_reason` と一致しなければならない

<!-- Block: Runtime Settings Group -->
## ランタイム設定テーブルの JSON

<!-- Block: Runtime Settings Values -->
### `runtime_settings.values_json`

```json
{
  "llm.model": "openai/gpt-5-mini",
  "llm.image_model": "openai/gpt-5-mini",
  "llm.embedding_model": "openai/text-embedding-3-large",
  "runtime.idle_tick_ms": 1000,
  "behavior.second_person_label": "マスター",
  "character.vrm_file_path": "",
  "speech.tts.provider": "voicevox",
  "speech.stt.provider": "amivoice",
  "motion.posture_change_loop_count_standing": 30,
  "integrations.notify_route": "ui_only"
}
```

- `runtime_settings.values_json` は、現在有効な scalar 設定値を全設定キーぶん持つ完全オブジェクトである
- キーは `docs/39_設定キー運用仕様.md` に登録された設定キーだけを許可する
- 値の型は各キーの登録 `value_type` と一致しなければならない
- `motion.animations` や `retrieval_profile` のような構造化値はこのオブジェクトへ直接入れない

<!-- Block: Runtime Settings Updated At -->
### `runtime_settings.value_updated_at_json`

```json
{
  "llm.model": 1760000000000,
  "behavior.second_person_label": 1760000000000,
  "speech.tts.provider": 1760000000000,
  "motion.posture_change_loop_count_standing": 1760000000000,
  "integrations.notify_route": 1760000000000
}
```

- `runtime_settings.value_updated_at_json` は、各設定キーの最終反映時刻を `key -> unix_ms` で持つ完全オブジェクトである
- キー集合は `runtime_settings.values_json` と同一に固定する

<!-- Block: Settings Editor Group -->
## 設定UIテーブルの JSON

<!-- Block: Settings Editor System Values -->
### `settings_editor_state.system_values_json`

```json
{
  "runtime.idle_tick_ms": 1000,
  "runtime.long_cycle_min_interval_ms": 10000,
  "sensors.microphone.enabled": true,
  "sensors.camera.enabled": true,
  "integrations.sns.enabled": false,
  "integrations.notify_route": "ui_only",
  "integrations.discord.bot_token": "",
  "integrations.discord.channel_id": ""
}
```

- `settings_editor_state.system_values_json` は、設定UIで保持するシステム設定だけを持つ完全オブジェクトである
- キーは `runtime.idle_tick_ms`、`runtime.long_cycle_min_interval_ms`、`sensors.microphone.enabled`、`sensors.camera.enabled`、`integrations.sns.enabled`、`integrations.notify_route`、`integrations.discord.bot_token`、`integrations.discord.channel_id` に固定する

<!-- Block: Settings Editor State -->
### `settings editor api.editor_state`

```json
{
  "revision": 12,
  "active_character_preset_id": "preset_character_default",
  "active_behavior_preset_id": "preset_behavior_default",
  "active_conversation_preset_id": "preset_conversation_default",
  "active_memory_preset_id": "preset_memory_default",
  "active_motion_preset_id": "preset_motion_default",
  "system_values": {
    "runtime.idle_tick_ms": 1000,
    "integrations.notify_route": "ui_only"
  }
}
```

- `settings editor api.editor_state` は、設定UI保存対象の singleton state を API 用に展開した形である
- `system_values` の shape は `settings_editor_state.system_values_json` と同一である

<!-- Block: Character Preset Payload -->
### `character_presets.payload_json`

```json
{
  "character.vrm_file_path": "",
  "character.material.convert_unlit_to_mtoon": false,
  "character.material.enable_shadow_off": true,
  "character.material.shadow_off_meshes": "Face, U_Char_1",
  "speech.tts.enabled": false,
  "speech.tts.provider": "voicevox",
  "speech.stt.enabled": false,
  "speech.stt.provider": "amivoice",
  "speech.stt.language": "ja"
}
```

- `character_presets.payload_json` は `character.*`、`speech.tts.*`、`speech.stt.*` の固定形を持つ
- 通知経路と Discord 認証情報は含めない

<!-- Block: Behavior Preset Payload -->
### `behavior_presets.payload_json`

```json
{
  "behavior.second_person_label": "マスター",
  "behavior.system_prompt": "...",
  "behavior.addon_prompt": "...",
  "behavior.response_pace": "balanced",
  "behavior.proactivity_level": "medium",
  "behavior.browse_preference": "balanced",
  "behavior.notify_preference": "balanced",
  "behavior.speech_style": "neutral",
  "behavior.verbosity_bias": "balanced"
}
```

- `behavior_presets.payload_json` は `behavior.*` の固定形を持つ

<!-- Block: Conversation Preset Payload -->
### `conversation_presets.payload_json`

```json
{
  "llm.model": "openai/gpt-5-mini",
  "llm.api_key": "",
  "llm.base_url": "",
  "llm.temperature": 0.7,
  "llm.max_output_tokens": 4096,
  "llm.reasoning_effort": "",
  "llm.reply_web_search_enabled": true,
  "llm.max_turns_window": 50,
  "llm.image_model": "openai/gpt-5-mini",
  "llm.image_api_key": "",
  "llm.image_base_url": "",
  "llm.max_output_tokens_vision": 4096,
  "llm.image_timeout_seconds": 60
}
```

- `conversation_presets.payload_json` は会話生成と画像認識に使う `llm.*` の固定形を持つ

<!-- Block: Memory Preset Payload -->
### `memory_presets.payload_json`

```json
{
  "llm.embedding_model": "openai/text-embedding-3-large",
  "llm.embedding_api_key": "",
  "llm.embedding_base_url": "",
  "runtime.context_budget_tokens": 8192,
  "memory.embedding_dimension": 3072,
  "memory.similar_episodes_limit": 60,
  "memory.max_inject_tokens": 1200,
  "retrieval_profile": {
    "semantic_top_k": 8,
    "recent_window_limit": 5,
    "fact_bias": 0.7,
    "summary_bias": 0.6,
    "event_bias": 0.4
  }
}
```

- `memory_presets.payload_json` は `llm.embedding_*`、`runtime.context_budget_tokens`、`memory.*`、`retrieval_profile` の固定形を持つ

<!-- Block: Motion Preset Payload -->
### `motion_presets.payload_json`

```json
{
  "motion.posture_change_loop_count_standing": 30,
  "motion.posture_change_loop_count_sitting_floor": 30,
  "animations": [
    {
      "display_name": "待機",
      "animation_type": 0,
      "animation_name": "idle",
      "is_enabled": true
    }
  ]
}
```

- `motion_presets.payload_json` は 2 つの scalar 設定と `animations[]` の固定形を持つ
- `animation_type` は `0`、`1`、`2` の整数に固定する

<!-- Block: Settings Change Set Payload -->
### `settings_change_sets.payload_json`

```json
{
  "editor_revision": 12,
  "active_character_preset_id": "preset_character_default",
  "active_behavior_preset_id": "preset_behavior_default",
  "active_conversation_preset_id": "preset_conversation_default",
  "active_memory_preset_id": "preset_memory_default",
  "active_motion_preset_id": "preset_motion_default",
  "system_values": {
    "runtime.idle_tick_ms": 1000,
    "integrations.notify_route": "ui_only"
  },
  "preset_versions": {
    "character": 1760000000000,
    "behavior": 1760000000000,
    "conversation": 1760000000000,
    "memory": 1760000000000,
    "motion": 1760000000000
  }
}
```

- `settings_change_sets.payload_json` は、設定UI保存の canonical 結果をランタイムへ渡すオブジェクトである
- `preset_versions` は `character`、`behavior`、`conversation`、`memory`、`motion` の 5 キーに固定する

<!-- Block: Settings Preset Entry -->
### `settings_preset_entry`

```json
{
  "preset_id": "preset_conversation_default",
  "preset_name": "標準",
  "archived": false,
  "sort_order": 10,
  "updated_at": 1760000000000,
  "payload": {}
}
```

- `settings_preset_entry` は、設定UI API が返すプリセット配列の共通要素である
- `payload` は、その配列に対応するテーブルの `payload_json` 固定形に一致しなければならない

<!-- Block: Camera Connection Entry -->
### `camera_connection_entry`

```json
{
  "camera_connection_id": "cam_001",
  "is_enabled": true,
  "display_name": "リビング",
  "host": "192.168.10.20",
  "username": "alice",
  "password": "secret",
  "sort_order": 10,
  "updated_at": 1760000000000
}
```

- `camera_connection_entry` は、設定UI API が返すカメラ接続一覧の共通要素である
- `is_enabled=true` の行が AI 利用候補であり、複数件を許可する

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
  "related_input_id": "inp_...",
  "audio_url": "/audio/tts_msg_....wav",
  "audio_mime_type": "audio/wav"
}
```

- 必須項目は `message_id`、`role`、`text`、`created_at` である
- current 実装の `role` は `user` または `assistant` を使う
- `source_cycle_id`、`related_input_id`、`audio_url`、`audio_mime_type` は任意である

<!-- Block: UI Message End -->
#### `event_type = message_end`

```json
{
  "message_id": "msg_...",
  "finish_reason": "completed",
  "final_message_emitted": true,
  "token_count": 3
}
```

- 必須項目は `message_id`、`finish_reason`、`final_message_emitted`、`token_count` である
- `finish_reason` は `completed` または `cancelled` を使う
- `final_message_emitted` は、同じ `message_id` に対して確定 `message` を出したかどうかを持つ
- `token_count` は、その応答で実際に流した `token` 件数を持つ

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
- `status_code` は、少なくとも `idle`、`thinking`、`speaking`、`camera_moving`、`waiting_external`、`browsing`、`processing_external_result` を区別する
- `cycle_id` は任意で、特定サイクルに紐づく更新だけに付ける

<!-- Block: UI Notice -->
#### `event_type = notice`

```json
{
  "notice_code": "browse_queued",
  "text": "検索タスクを追加しました: 今日の天気"
}
```

- 必須項目は `notice_code`、`text` である
- `notice_code` は、UI 側で分類できる固定語彙 `string` にする
- current 実装では、DB に保存される `notice` として `browse_queued`、`browse_completed` を使ってよい
- 保持範囲外からの `SSE` 再開時は、保存しない合成 `notice` として `stream_reset` を使ってよい

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
- 短周期の内部失敗を UI へ通知するときは、`error_code="processing_failed"` を使う

<!-- Block: Action History Group -->
## 行動履歴の JSON

<!-- Block: Action Command -->
### `action_history.command_json`

- `action_history.command_json` は、その行動で実行しようとした命令の最小記録である

```json
{
  "target_channel": "browser_chat",
  "event_types": ["status", "status", "token", "message", "message_end", "status"],
  "decision": "execute",
  "decision_reason": "speak_selected",
  "related_input_id": "inp_...",
  "proposal_ref": "prop_...",
  "message_id": "msg_...",
  "role": "assistant"
}
```

- 必須項目は `target_channel`、`event_types`、`decision`、`decision_reason` である
- `target_channel` は、初期段階では `browser_chat` に固定する
- `event_types` は、実際に出そうとした `ui_outbound_events.event_type` の順序付き配列である
- `decision` は、`execute`、`hold`、`reject` のいずれかを持つ
- `decision_reason` は、`action validator` がその決定にした理由コードを持つ
- `message_id` は、`event_type = message` を含む命令だけに付ける
- `role` は、`message_id` を伴うメッセージ応答だけに付ける
- `related_input_id` は、入力に対する応答行動だけに付ける
- `proposal_ref` は、`cognition_result.action_proposals` から確定した候補を追跡したいときに付ける
- `command_type` は、current 実装では `speak_ui_message`、`dispatch_notice`、`enqueue_browse_task`、`control_camera_look`、`execute_browse_task`、`abandon_browse_task` を使ってよい
- `notice_code` と `text` は、`dispatch_notice` を実行する命令だけに付ける
- `target`、`parameters`、`preconditions`、`stop_conditions`、`timeout_ms`、`requires_reobserve`、`expected_effects` は、`execute` のとき `action_command` をそのまま残したい場合に付けてよい
- current の `control_camera_look` では、`parameters.camera_connection_id` を必須とし、`requires_reobserve=true` に固定し、`expected_effects.followup_input_kind=\"camera_observation\"`、`expected_effects.followup_trigger_reason=\"post_action_followup\"` を持たせてよい
- `parameters.task_id`、`parameters.query`、`parameters.target_channel` は、`enqueue_browse_task` を実行する命令だけに付ける
- current の `enqueue_browse_task` では、伴走メッセージを出す場合だけ `parameters.message_id` と `parameters.text` を持たせてよい
- `parameters.query` は、`execute_browse_task` と `abandon_browse_task` を実行する命令だけに付けてよい
- `related_task_id` は、`execute_browse_task` と `abandon_browse_task` のように task 再開を処理する命令だけに付けてよい
- `hold` と `reject` では、`event_types` は `status` だけでもよい
- current の `hold` では、伴走メッセージを出す場合だけ `message_id` と `role` を付けてよい
- `target_message_id` は、`cancel` のように既存メッセージを対象化する行動だけに付ける
- `input_kind` は、未対応入力のエラー応答のように、原因となる入力種別を残したいときだけ付ける

<!-- Block: Action Effects -->
### `action_history.observed_effects_json`

- `action_history.observed_effects_json` は、その行動の直後に観測した最小結果である

```json
{
  "emitted_event_types": ["status", "status", "token", "message", "message_end", "status"],
  "status_code_after": "idle",
  "was_cancelled": false,
  "token_count": 3,
  "final_message_emitted": true,
  "validator_decision": "execute",
  "validator_reason": "speak_selected",
  "selected_action_type": "speak",
  "action_candidate_score": {
    "proposal_id": "prop_...",
    "hard_gate_passed": true,
    "task_fit_score": 1.0,
    "personality_fit_score": 0.9,
    "relationship_fit_score": 0.8,
    "experience_fit_score": 0.8,
    "drive_relief_score": 0.7,
    "expected_stability_score": 0.8,
    "priority_hint_score": 1.0,
    "total_score": 0.87
  }
}
```

- 必須項目は `emitted_event_types`、`validator_decision`、`validator_reason`、`action_candidate_score` である
- `emitted_event_types` は、実際に `ui_outbound_events` へ追記した `event_type` の順序付き配列である
- `message_id` は、メッセージ応答を生成した場合だけに付ける
- `notice_code` は、`notice` を生成した場合だけに付ける
- `error_code` は、`error` を生成した場合だけに付ける
- `status_code_after` は、最後に `status` を出した場合だけに付ける
- `was_cancelled` は、途中停止が起きた応答だけに付ける
- `token_count` は、`token` を流した応答だけに付ける
- `final_message_emitted` は、最後に `message` を確定したかどうかを持つ
- `message_end` は、`speak` 応答の完了または中断を外向きに確定した場合だけ `emitted_event_types` に現れる
- `validator_decision` は、`action validator` の決定結果を持つ
- `validator_reason` は、`action validator` の決定理由コードを持つ
- `selected_action_type` は、比較で最上位になった候補の `action_type` を残したいときに付ける
- `action_candidate_score` は、`action validator` の最小比較結果を残したいときに付ける
- `reject` では、`message_id` を付けず、`final_message_emitted=false` にする
- current の `hold` では、伴走メッセージを出した場合だけ `message_id` を付け、`final_message_emitted=true` にしてよい
- `enqueue_browse_task` を実行した場合は、`queued_task_id`、`queued_task_kind`、`queued_task_status` を付けてよい
- current の `enqueue_browse_task` では、伴走メッセージを出した場合だけ `final_message_emitted` と `message_id` を付けてよい
- `complete_browse_task` を実行した場合は、`related_task_id`、`task_status_after`、`summary_text` を付けてよい
- `abandon_browse_task` を実行した場合は、`related_task_id`、`task_status_after`、`error_message` を付けてよい
- `complete_browse_task` を実行した場合は、`followup_input_kind=\"network_result\"` を付けてよい
- `control_camera_look` を実行した場合は、`camera_connection_id`、`camera_display_name`、`followup_required`、`followup_input_kind=\"camera_observation\"`、`followup_input_source=\"post_action_followup\"`、`followup_trigger_reason=\"post_action_followup\"`、`followup_capture` を付けてよい
- `dispatch_notice` を実行した場合は、`notice_code` を付けてよい

<!-- Block: Memory Job Group -->
## 記憶ジョブの JSON

<!-- Block: Memory Job Payloads -->
### `memory_job_payloads.payload_json`

- `memory_job_payloads.payload_json` は、すべて共通ヘッダを持つ
- `payload_json.job_kind` は、対応する `memory_job_payloads.job_kind` と一致しなければならない
- `source_event_ids` は、順序を持つ配列とする
- `source_event_ids` は、イベント起点の job では非空、`tidy_memory` のような保守起点 job では空配列を許可する

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
- `source_event_ids` は、イベント起点の job では空配列を許可しない
- `source_event_ids` は、`tidy_memory` のような保守起点 job では空配列 `[]` を許可する

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
    },
    {
      "event_id": "evt_002",
      "event_updated_at": 1760000000500
    }
  ]
}
```

- 追加の必須項目は `primary_event_id`、`reflection_seed_ref`、`event_snapshot_refs` である
- `primary_event_id` は、`source_event_ids` のいずれかと一致しなければならない
- `reflection_seed_ref` は、少なくとも `ref_kind`、`ref_id` を持つ
- `event_snapshot_refs` の各要素は、少なくとも `event_id`、`event_updated_at` を持つ
- `event_snapshot_refs` は空配列を許可しない
- `event_snapshot_refs` は、`source_event_ids` と同じ順序で全件を並べなければならない
- `write_memory` は、この payload 自体に `persona_updates` を含めない
- `persona_updates` は、`write_memory` 実行中に生成される内部差分としてだけ扱う

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
      "entity_id": "ms_..."
    }
  ]
}
```

- 追加の必須項目は `reason_code`、`reason_note`、`targets` である
- `reason_code` は、少なくとも `misretrieval_confirmed`、`stale_linkage`、`manual_quarantine` を区別する
- `targets` は空配列を許可しない
- `targets` の各要素は、少なくとも `entity_type`、`entity_id` を持つ
- `entity_type` は、初期実装では `event`、`memory_state` を区別する
- 同じ `(entity_type, entity_id)` が重複していても、payload 正規化で 1 件に畳み込んでよい

<!-- Block: Embedding Sync -->
#### `job_kind = embedding_sync`

```json
{
  "job_kind": "embedding_sync",
  "cycle_id": "cycle_...",
  "source_event_ids": ["evt_001"],
  "created_at": 1760000000000,
  "idempotency_key": "embedding_sync:cycle_...:evt_001",
  "embedding_model": "text-embedding-3-small",
  "requested_scopes": ["recent", "global"],
  "targets": [
    {
      "entity_type": "memory_state",
      "entity_id": "ms_...",
      "source_updated_at": 1760000000000,
      "current_searchable": true
    }
  ]
}
```

- 追加の必須項目は `embedding_model`、`requested_scopes`、`targets` である
- `requested_scopes` は空配列を許可しない
- `requested_scopes` の各要素は、少なくとも `recent`、`global` を区別する
- `targets` は空配列を許可しない
- `targets` の各要素は、少なくとも `entity_type`、`entity_id`、`source_updated_at`、`current_searchable` を持つ

<!-- Block: Tidy Memory -->
#### `job_kind = tidy_memory`

```json
{
  "job_kind": "tidy_memory",
  "cycle_id": "cycle_...",
  "source_event_ids": [],
  "created_at": 1760000000000,
  "idempotency_key": "tidy_memory:cycle_...:completed_jobs_gc",
  "maintenance_scope": "completed_jobs_gc",
  "retention_cutoff_at": 1760000000000
}
```

- 追加の必須項目は `maintenance_scope`、`retention_cutoff_at` である
- `maintenance_scope` は、少なくとも `completed_jobs_gc`、`stale_preview_gc`、`stale_vector_gc` を区別する
- `target_refs` は任意で、指定する場合は各要素が少なくとも `entity_type`、`entity_id` を持つ
- `target_refs` に同じ `(entity_type, entity_id)` が重複していても、payload 正規化で 1 件に畳み込んでよい

<!-- Block: Web Api Group -->
## Web API の JSON

<!-- Block: Settings Override Request -->
### `POST /api/settings/overrides` の入力 JSON

```json
{
  "key": "llm.model",
  "requested_value": "openrouter/.../model",
  "apply_scope": "runtime"
}
```

- 必須項目は `key`、`requested_value`、`apply_scope` である
- `key` は、`docs/39_設定キー運用仕様.md` に登録されたドット区切り設定キーに固定する
- `requested_value` は、対象 `key` に登録された型だけを許可する
- 初期公開キーでは `string`、`integer`、`number`、`boolean` だけを受け付ける
- `apply_scope` は、対象 `key` に登録された許可値だけを受け付ける
- Web サーバは、受け取った `requested_value` を `settings_overrides.requested_value_json` の正規化形へ変換して保存する

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
  "client_message_id": "cli_msg_001",
  "attachments": [
    {
      "attachment_kind": "camera_still_image",
      "camera_connection_id": "cam_living",
      "camera_display_name": "リビング",
      "capture_id": "cap_0123456789abcdef0123456789abcdef"
    }
  ]
}
```

- `text` は任意だが、ある場合は空文字列や空白のみを許可しない
- `client_message_id` は任意で、クライアント側の再送判定に使う
- `client_message_id` がある場合、同じ `channel` での再利用は許可しない
- `attachments` は任意で、ある場合は `camera_still_image` の配列にする
- 各添付は `attachment_kind`、`camera_connection_id`、`camera_display_name`、`capture_id` を必須とする
- `text` と `attachments` は、少なくともどちらか一方が必要である

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

<!-- Block: Microphone Input Response -->
### `POST /api/microphone/input` の成功応答 JSON

```json
{
  "accepted": true,
  "input_id": "inp_...",
  "status": "queued",
  "channel": "browser_chat",
  "transcript_text": "おはよう",
  "provider": "amivoice",
  "language": "ja"
}
```

- 必須項目は `accepted`、`input_id`、`status`、`channel`、`transcript_text`、`provider`、`language` である
- `accepted` は `true` に固定する
- `status` は `queued` に固定する
- `channel` は `browser_chat` に固定する
- `transcript_text` は空文字列を許可しない
- `provider` は current 実装では `amivoice` に固定する
- `language` は `speech.stt.language` の設定値を返す

<!-- Block: Camera Capture Request -->
### `POST /api/camera/capture` の入力 JSON

```json
{
  "camera_connection_id": "cam_living"
}
```

- 必須項目は `camera_connection_id` である
- `camera_connection_id` は、enabled camera connection の 1 件を指す

<!-- Block: Camera Observe Request -->
### `POST /api/camera/observe` の入力 JSON

```json
{
  "camera_connection_id": "cam_living"
}
```

- 必須項目は `camera_connection_id` である
- `camera_connection_id` は、enabled camera connection の 1 件を指す

<!-- Block: Camera Capture Response -->
### `POST /api/camera/capture` の成功応答 JSON

```json
{
  "camera_connection_id": "cam_living",
  "camera_display_name": "リビング",
  "capture_id": "cap_...",
  "image_path": "data/camera/cap_....jpg",
  "image_url": "/captures/cap_....jpg",
  "captured_at": 1760000000000
}
```

- 必須項目は `camera_connection_id`、`camera_display_name`、`capture_id`、`image_path`、`image_url`、`captured_at` である
- `camera_connection_id` は、撮影に使った enabled camera connection を表す
- `camera_display_name` は、その接続の表示名である
- `capture_id` は、不透明な capture 識別子である
- `image_path` は、サーバ作業ディレクトリ基準の保存先相対パスである
- `image_url` は、同一オリジンで静止画を再取得する URL path である
- `captured_at` は、静止画保存完了時点の UTC unix milliseconds である

<!-- Block: Camera Observe Response -->
### `POST /api/camera/observe` の成功応答 JSON

```json
{
  "accepted": true,
  "input_id": "inp_...",
  "status": "queued",
  "channel": "browser_chat",
  "camera_connection_id": "cam_living",
  "camera_display_name": "リビング",
  "capture_id": "cap_...",
  "image_path": "data/camera/cap_....jpg",
  "image_url": "/captures/cap_....jpg",
  "captured_at": 1760000000000
}
```

- 必須項目は `accepted`、`input_id`、`status`、`channel`、`camera_connection_id`、`camera_display_name`、`capture_id`、`image_path`、`image_url`、`captured_at` である
- `accepted` は `true` に固定する
- `status` は `queued` に固定する
- `channel` は `browser_chat` に固定する
- `input_id` は、生成した自発観測入力の ID である
- `camera_connection_id` と `camera_display_name` は、観測に使った enabled camera connection を表す
- `capture_id`、`image_path`、`image_url`、`captured_at` は、同時に取得した静止画の情報である

<!-- Block: Status Response -->
### `GET /api/status` の成功応答 JSON

```json
{
  "server_time": 1760000000000,
  "runtime": {
    "is_running": false,
    "last_retrieval": {
      "cycle_id": "cycle_...",
      "created_at": 1760000000000,
      "mode": "associative_recent",
      "queries": ["最近の会話"],
      "collector_names": [
        "recent_event_window",
        "associative_memory",
        "episodic_memory"
      ],
      "collector_counts": {
        "recent_event_window": 2,
        "associative_memory": 1
      },
      "selector_input_collector_counts": {
        "recent_event_window": 2,
        "associative_memory": 3,
        "reply_chain": 1
      },
      "selector_input_slot_counts": {
        "recent_event_window": 2,
        "episodic_items": 3,
        "semantic_items": 2
      },
      "selector_input_reason_counts": {
        "matched_query": 3,
        "about_time": 2,
        "reply_chain": 1
      },
      "selector_summary": {
        "selector_mode": "llm_ranked",
        "selection_reason": "直近会話の継続と明示日付の一致を優先した",
        "raw_candidate_count": 9,
        "merged_candidate_count": 7,
        "selector_input_candidate_count": 7,
        "selector_candidate_limit": 24,
        "llm_selected_ref_count": 5,
        "selected_candidate_count": 4,
        "duplicate_hit_count": 2,
        "reserve_candidate_count": 1,
        "slot_skipped_count": 1
      },
      "trimmed_item_refs": ["event:evt_002"],
      "selected_counts": {
        "working_memory_items": 2,
        "episodic_items": 1,
        "semantic_items": 1,
        "affective_items": 0,
        "relationship_items": 1,
        "reflection_items": 0,
        "recent_event_window": 3
      }
    }
  },
  "self_state": {
    "current_emotion": {
      "v": 0.12,
      "a": 0.18,
      "d": 0.03,
      "labels": ["calm"]
    },
    "last_persona_update": {
      "created_at": 1760000000000,
      "reason": "persona update applied",
      "evidence_event_ids": ["evt_001"],
      "updated_traits": []
    }
  },
  "attention_state": {
    "primary_focus": "待機中"
  },
  "body_state": {
    "posture_mode": "awaiting_external",
    "sensor_availability": {
      "camera": true,
      "microphone": false
    },
    "load": {
      "task_queue_pressure": 0.35,
      "interaction_load": 0.0
    }
  },
  "world_state": {
    "situation_summary": "外部結果待ち: 近所のイベント",
    "external_wait_count": 1
  },
  "drive_state": {
    "priority_effects": {
      "task_progress_bias": 0.35,
      "exploration_bias": 0.15,
      "maintenance_bias": 0.25,
      "social_bias": 0.0
    }
  },
  "task_state": {
    "active_task_count": 0,
    "waiting_task_count": 1
  }
}
```

- 必須項目は `server_time`、`runtime`、`self_state`、`attention_state`、`body_state`、`world_state`、`drive_state`、`task_state` である
- `runtime` は、少なくとも `is_running` を持つ
- `runtime.last_cycle_id` は、短周期が 1 回以上完了している場合だけ持つ
- `runtime.last_commit_id` は、`commit_records` が 1 件以上ある場合だけ持つ
- `runtime.last_retrieval` は、`retrieval_runs` が 1 件以上ある場合だけ持つ
- `runtime.last_retrieval.collector_names`、`collector_counts`、`selector_input_collector_counts`、`selector_input_slot_counts`、`selector_input_reason_counts`、`selector_summary`、`trimmed_item_refs` は、current 実装では追加で持ってよい
- `self_state.current_emotion` は、少なくとも `v`、`a`、`d`、`labels` を持つ
- `self_state.last_persona_update` は、`revisions.entity_type=self_state.personality` が 1 件以上ある場合だけ持つ
- `attention_state.primary_focus` は、current 実装では `attention_state.primary_focus_json.summary` をそのまま返す短い `string` とする
- `body_state.posture_mode` は `string` に固定する
- `body_state.sensor_availability.camera` と `body_state.sensor_availability.microphone` は `boolean` に固定する
- `body_state.load.task_queue_pressure` と `body_state.load.interaction_load` は `number` に固定する
- `world_state.situation_summary` は `string` に固定する
- `world_state.external_wait_count` は `integer` に固定する
- `drive_state.priority_effects` は `task_progress_bias`、`exploration_bias`、`maintenance_bias`、`social_bias` を持つ `object` に固定する
- `task_state.active_task_count`、`task_state.waiting_task_count` は `integer` に固定する

<!-- Block: Stream Data -->
### `GET /api/chat/stream` の `data:` JSON

- `GET /api/chat/stream` の `data:` は、`ui_outbound_events.payload_json` と同一の JSON をそのまま使う
- `event_type` ごとの payload は、このドキュメントの `ui_outbound_events.payload_json` に従う
- Web サーバは、`data:` 用に別形式へ変換しない

<!-- Block: Chat History Response -->
### `GET /api/chat/history` の応答 JSON

```json
{
  "channel": "browser_chat",
  "messages": [
    {
      "message_id": "inp_...",
      "role": "user",
      "text": "おはよう",
      "created_at": 1760000000000
    },
    {
      "message_id": "msg_...",
      "role": "assistant",
      "text": "おはようございます。",
      "created_at": 1760000001000
    }
  ],
  "stream_cursor": 321
}
```

- 必須項目は `channel`、`messages` である
- `channel` は current 実装では `browser_chat` に固定する
- `messages` は、`event_type = message` と同じ JSON 形の配列に固定する
- `stream_cursor` は任意で、次に `GET /api/chat/stream` を開く初期カーソルとして使ってよい `integer` である

<!-- Block: Fixed Decisions -->
## このドキュメントで確定したこと

- 制御面テーブルの JSON 列は、初期実装で使うキーと型をここで固定する
- `settings_overrides.requested_value_json` は、型付きの正規化オブジェクトで保持する
- `ui_outbound_events.payload_json` と `SSE data` は同一の JSON を使う
- `memory_job_payloads.payload_json` は、共通ヘッダと `job_kind` ごとの追加項目を持つ
- Web API の JSON 本文は、エンドポイントの意味を `docs/35_WebAPI仕様.md`、本文の形をこのドキュメントで分担して管理する
