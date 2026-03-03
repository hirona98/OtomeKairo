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

- 固定するのは、初期実装で使う JSON オブジェクトのキー、型、必須項目、固定語彙である
- 固定するのは、`pending_inputs.payload_json`、`settings_overrides.requested_value_json`、`ui_outbound_events.payload_json`、`action_history.command_json`、`action_history.observed_effects_json`、`memory_jobs.payload_ref_json`、`memory_job_payloads.payload_json`、主要な Web API 本文である
- 固定するのは、`self_state.personality_json`、`self_state.current_emotion_json`、`self_state.long_term_goals_json`、`self_state.relationship_overview_json`、`self_state.invariants_json`、短周期の内部で使う `selection_profile`、`persona_consistency_score`、`attention_score_breakdown`、`self_initiated_score_breakdown`、`action_candidate_score`、`cognition_result`、長周期の内部で使う `personality_change_proposal`、`persona_updates` の形である
- 固定しないのは、Python のクラス名、Pydantic モデル名、OpenAPI の自動生成細部である
- 固定しないのは、将来追加する未使用フィールドや後段の拡張イベント種別である

<!-- Block: Common Rules -->
## 共通ルール

<!-- Block: Json Shape -->
### JSON の基本形

- JSON のキーは、すべて `snake_case` に統一する
- ただし、`GET /api/settings` の `effective_settings` だけは、`docs/39_設定キー運用仕様.md` と同じドット区切り設定キーをそのままキー名に使ってよい
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
- `speech_tone` は、少なくとも `soft`、`neutral`、`direct` を区別する
- `distance_style` は、少なくとも `reserved`、`balanced`、`close` を区別する
- `confirmation_style` は、少なくとも `minimal`、`balanced`、`careful` を区別する
- `response_pace` は、少なくとも `slow`、`balanced`、`quick` を区別する
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
- 初期実装では、`working_memory_items` に `memory_kind=summary`、`semantic_items` に `memory_kind=fact`、`recent_event_window` に直近 `5` 件の `searchable` な `events` を入れてよい
- 初期実装では、`current_observation.observation_text` と、必要なら `query` / `source_task_id` への一致を使い、関連しない要素を `memory_bundle` から落としてよい

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
- `personality_fit_score` は、必要なら `persona_consistency_score` の `trait_alignment`、`style_alignment`、`overall_score` を使って計算してよい
- `priority_hint_score` は、`proposal.priority` をそのまま信じるためではなく、同程度候補の補助比較にだけ使う
- 各 `*_score` は、比較前に同じ `0.0..1.0` 尺度へ正規化済みでなければならない
- `action_candidate_score` は永続化前提の正本ではなく、その短周期の候補比較ごとに再計算する

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
    "focus_kind": "current_input_only",
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
- `cognition_result` は、認知層が一度に返す JSON オブジェクトであり、後から補完前提で分割しない
- 必須項目は `intention_summary`、`decision_reason`、`action_proposals`、`step_hints`、`speech_draft`、`memory_focus`、`reflection_seed` である
- `action_proposals` と `step_hints` は配列に固定し、候補がない場合も空配列 `[]` を使う
- 初期実装の `browser_chat` では、`action_proposals` の各要素は少なくとも `action_type` と `priority` を持つ
- 初期実装の `browser_chat` では、`action_type` は `speak`、`browse`、`notify`、`wait` のいずれかだけを許可する
- 初期実装の `browser_chat` では、`priority` は `0.0..1.0` の `number` に固定する
- 初期実装の `browser_chat` では、`speak` と `notify` のとき `target_channel=\"browser_chat\"` を必須とする
- 初期実装の `browser_chat` では、`browse` のとき `query` に非空の検索文字列を必須とする
- `speech_draft` は、少なくとも `text`、`language`、`delivery_mode` を持つ
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
  "evidence_summary": "探索寄りの行動選択が複数周期で安定した"
}
```

- `personality_change_proposal` は、長周期の `write_memory` 内部で作る未適用の提案オブジェクトである
- 必須項目は `base_personality_updated_at`、`trait_deltas`、`preference_promotions`、`aversion_promotions`、`habit_updates`、`evidence_summary` である
- `style_updates` は任意で、変化がある場合だけ持つ
- `trait_deltas` の各要素は、`trait_name`、`delta`、`reason`、`evidence_count`、`source_cycle_ids` を必須とする
- `trait_name` は、`self_state.personality_json.trait_values` に存在するキーだけを許可する
- `delta` は、`-1.0..+1.0` の `number` だが、適用前の提案値である
- `source_cycle_ids` は空配列を許可しない
- `base_personality_updated_at` は、提案生成時に読んだ `self_state.personality_updated_at` の値である
- `source_cycle_ids` は、証拠に使った `events` を生んだ短周期の `cycle_id` だけを数える
- `preference_promotions` と `aversion_promotions` は、`personality_preference_entry` の配列である
- `habit_updates` は、`preferred_action_types`、`preferred_observation_kinds`、`avoided_action_styles` のうち変更対象だけを持つ部分オブジェクトでよい

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
  "evidence_summary": "探索寄りの行動選択が複数周期で安定した"
}
```

- `persona_updates` は、`bounded apply` 後に `self_state.personality_json` へ反映可能な差分オブジェクトである
- 必須項目は `base_personality_updated_at`、`updated_trait_values`、`preference_promotions`、`aversion_promotions`、`habit_updates`、`evidence_summary` である
- `style_updates` は任意で、変化がある場合だけ持つ
- `updated_trait_values` は、`trait_name -> absolute_value` の部分オブジェクトである
- `updated_trait_values` に含めてよいキーは、`self_state.personality_json.trait_values` の固定キーだけである
- `updated_trait_values` の各値は、`-1.0..+1.0` の `number` に clamp 済みでなければならない
- `preference_promotions` と `aversion_promotions` は、`personality_preference_entry` の配列である
- `habit_updates` は、`self_state.personality_json.habit_biases` に上書きする部分オブジェクトである
- `evidence_summary` は、監査と `revisions` の理由づけに使う短い要約である
- `base_personality_updated_at` は、適用開始時の `self_state.personality_updated_at` と一致しなければならない
- 適用時に `base_personality_updated_at` が現在の `self_state.personality_updated_at` と一致しない場合、その `persona_updates` は stale として棄却し、後続の `write_memory` で再生成する

<!-- Block: Runtime Settings Group -->
## ランタイム設定テーブルの JSON

<!-- Block: Runtime Settings Values -->
### `runtime_settings.values_json`

```json
{
  "llm.default_model": "openrouter/default-model",
  "llm.embedding_model": "openrouter/default-embedding",
  "llm.temperature": 0.7,
  "llm.max_output_tokens": 2048,
  "runtime.idle_tick_ms": 1000
}
```

- `runtime_settings.values_json` は、現在有効な設定値を全設定キーぶん持つ完全オブジェクトである
- キーは、`docs/39_設定キー運用仕様.md` に登録されたドット区切り設定キーだけを許可する
- 値の型は、各キーの登録 `value_type` と一致しなければならない
- `apply_scope="runtime"` の `applied` は、このオブジェクトを同じ短周期で更新する
- `apply_scope="next_boot"` の `applied` は、このオブジェクトを即時更新せず、次回ランタイム起動時の materialize で更新する

<!-- Block: Runtime Settings Updated At -->
### `runtime_settings.value_updated_at_json`

```json
{
  "llm.default_model": 1760000000000,
  "llm.embedding_model": 1760000000000,
  "llm.temperature": 1760000000000,
  "llm.max_output_tokens": 1760000000000,
  "runtime.idle_tick_ms": 1760000000000
}
```

- `runtime_settings.value_updated_at_json` は、各設定キーの最終反映時刻を `key -> unix_ms` で持つ完全オブジェクトである
- キー集合は、`runtime_settings.values_json` と同一に固定する
- 各値は、UTC unix milliseconds の `integer` に固定する
- `next_boot` の materialize は、この時刻が既存値より新しいキーだけを更新する

<!-- Block: Event Group -->
## イベントテーブルの JSON

<!-- Block: Event Input Journal Refs -->
### `events.input_journal_refs_json`

```json
[
  "obs_inp_..."
]
```

- `events.input_journal_refs_json` は、その `events` 行の根拠になった `input_journal.observation_id` の順序付き配列である
- 各要素は、不透明な `string` に固定する
- 空配列は許可するが、外部入力や観測に由来する `events` では根拠がある限り省略しない

<!-- Block: Control Plane Group -->
## 制御面テーブルの JSON

<!-- Block: Pending Inputs -->
### `pending_inputs.payload_json`

- `pending_inputs.payload_json` は、少なくとも `input_kind` を持つ
- Web API が受け付ける初期段階の `browser_chat` 入力は、`chat_message` と `cancel` の 2 種だけである
- ランタイム内部では、外部検索結果を戻すために `network_result` を enqueue してよい

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
- `text` は、`4000` 文字を超えてはならない
- `client_message_id` は任意で、同一クライアントからの再送判定に使う
- `client_message_id` がある場合、Web サーバは `pending_inputs.client_message_id` にも同じ値を書き込む

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

<!-- Block: Pending Network Result -->
#### `network_result`

```json
{
  "input_kind": "network_result",
  "query": "OpenAI",
  "summary_text": "検索結果の要約",
  "source_task_id": "task_..."
}
```

- 必須項目は `input_kind`、`query`、`summary_text`、`source_task_id` である
- `input_kind` は `network_result` に固定する
- `query` は、外部検索に使った非空の文字列を持つ
- `summary_text` は、外部検索アダプタが返した非空の要約文字列を持つ
- `source_task_id` は、この結果を作った `browse` タスクを追跡するための ID を持つ

<!-- Block: Settings Requested Value -->
### `settings_overrides.requested_value_json`

- `settings_overrides.requested_value_json` は、要求値そのものではなく、型付きの正規化オブジェクトで保持する
- `POST /api/settings/overrides` の `requested_value` は、Web サーバでこの形へ正規化してから保存する
- `value_type` は、対象 `key` の登録定義と一致しなければならない

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
- 短周期の内部失敗を UI へ通知するときは、`error_code="processing_failed"` を使う

<!-- Block: Action History Group -->
## 行動履歴の JSON

<!-- Block: Action Command -->
### `action_history.command_json`

- `action_history.command_json` は、その行動で実行しようとした命令の最小記録である
- 初期実装では、ブラウザ向けの UI 応答命令と、`browse` の task 再開命令をこの形で保持する

```json
{
  "target_channel": "browser_chat",
  "event_types": ["status", "status", "token", "message", "status"],
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
- `command_type` は、初期実装では `speak_ui_message`、`dispatch_notice`、`enqueue_browse_task`、`execute_browse_task`、`abandon_browse_task` を使ってよい
- `notice_code` と `text` は、`dispatch_notice` を実行する命令だけに付ける
- `target`、`parameters`、`preconditions`、`stop_conditions`、`timeout_ms`、`requires_reobserve`、`expected_effects` は、`execute` のとき `action_command` をそのまま残したい場合に付けてよい
- `parameters.task_id`、`parameters.query`、`parameters.target_channel` は、`enqueue_browse_task` を実行する命令だけに付ける
- `parameters.query` は、`execute_browse_task` と `abandon_browse_task` を実行する命令だけに付けてよい
- `related_task_id` は、`execute_browse_task` と `abandon_browse_task` のように task 再開を処理する命令だけに付けてよい
- `hold` と `reject` では、`message_id` と `role` を付けず、`event_types` は `status` だけでもよい
- `target_message_id` は、`cancel` のように既存メッセージを対象化する行動だけに付ける
- `input_kind` は、未対応入力のエラー応答のように、原因となる入力種別を残したいときだけ付ける

<!-- Block: Action Effects -->
### `action_history.observed_effects_json`

- `action_history.observed_effects_json` は、その行動の直後に観測した最小結果である
- 初期実装では、実際に UI へ流したイベント種別と主要 ID だけを保持する

```json
{
  "emitted_event_types": ["status", "status", "token", "message", "status"],
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
- `validator_decision` は、`action validator` の決定結果を持つ
- `validator_reason` は、`action validator` の決定理由コードを持つ
- `selected_action_type` は、比較で最上位になった候補の `action_type` を残したいときに付ける
- `action_candidate_score` は、`action validator` の最小比較結果を残したいときに付ける
- `hold` と `reject` では、`message_id` を付けず、`final_message_emitted=false` にする
- `enqueue_browse_task` を実行した場合は、`queued_task_id`、`queued_task_kind`、`queued_task_status` を付けてよい
- `complete_browse_task` を実行した場合は、`related_task_id`、`task_status_after`、`summary_text` を付けてよい
- `abandon_browse_task` を実行した場合は、`related_task_id`、`task_status_after`、`error_message` を付けてよい
- `complete_browse_task` を実行した場合は、`followup_input_kind=\"network_result\"` を付けてよい
- `dispatch_notice` を実行した場合は、`line_delivery` を `delivered`、`skipped`、`failed` のいずれかで付けてよい

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
    }
  ]
}
```

- 追加の必須項目は `primary_event_id`、`reflection_seed_ref`、`event_snapshot_refs` である
- `primary_event_id` は、`source_event_ids` のいずれかと一致しなければならない
- `reflection_seed_ref` は、少なくとも `ref_kind`、`ref_id` を持つ
- `event_snapshot_refs` の各要素は、少なくとも `event_id`、`event_updated_at` を持つ
- `event_snapshot_refs` は空配列を許可しない
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
- `key` は、`docs/39_設定キー運用仕様.md` に登録されたドット区切り設定キーに固定する
- `requested_value` は、対象 `key` に登録された型だけを許可する
- 初期公開キーでは `string`、`integer`、`number`、`boolean` だけを受け付ける
- `apply_scope` は、対象 `key` に登録された許可値だけを受け付ける

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
- `client_message_id` がある場合、同じ `channel` での再利用は許可しない

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
    "is_running": false
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
- `runtime` は、少なくとも `is_running` を持つ
- `runtime.last_cycle_id` は、短周期が 1 回以上完了している場合だけ持つ
- `runtime.last_commit_id` は、`commit_records` が 1 件以上ある場合だけ持つ
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
