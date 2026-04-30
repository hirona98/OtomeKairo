# world_state

## 目的

`world_state` は、次の判断にも持ち越す外界条件を保持する短期状態である。
生の観測 payload ではなく、判断に効く形へ正規化した外界の前景を持つ。

`world_state` は記憶の代替ではない。
長期に残す経験や継続理解は `events / episodes / memory_units` と感情モデルで扱う。
`world_state` は、いまの画面、周囲、外部サービス、身体や機器の条件など、現在判断に近い状態を扱う。

## 境界

`world_state` に入れるものは次である。

- 現在の画面や作業対象の前景
- 近い時間で判断に効く周囲や場所の状態
- 外部サービスから取得した現在条件の要約
- 身体、センサ、機器から得た現在条件の要約
- 直近の能力実行結果から次の判断へ持ち越す条件

次は `world_state` に入れない。

- 生の画像、音声、長い payload
- 長期に残すべき人物理解や関係理解
- 約束や未完了の正本
- capability manifest や binding
- `ongoing_action` の実行列
- クライアント UI のローカル状態
- 設定値

## 最小構造

1 件の `world_state` は、少なくとも次を持つ。

| 項目 | 役割 |
|------|------|
| `world_state_id` | 状態の識別子 |
| `state_type` | `screen / environment / location / external_service / body / device / schedule / social_context` のいずれか |
| `scope_type / scope_key` | 主にどの対象領域の外界状態か |
| `summary_text` | 判断へ渡す短い要約 |
| `source_kind` | `capability_result / user_input / client_context / system_observation` のいずれか |
| `source_ref` | 根拠となる `event_id`、`request_id`、`cycle_id` など |
| `confidence` | 状態解釈の確からしさ |
| `salience` | 判断前景へ出す強さ |
| `observed_at` | 外界条件を観測した時刻 |
| `expires_at` | 状態を失効させる時刻 |
| `updated_at` | 最終更新時刻 |

`expires_at` は必須とする。
`world_state` は永続理解ではないため、期限の無い状態を作らない。

## `state_type`

採用する `state_type` は次とする。

| `state_type` | 用途 |
|------|------|
| `screen` | 現在の画面、アプリ、ウィンドウ、視覚前景 |
| `environment` | 周囲の状況、音、明るさ、場の変化 |
| `location` | 現在位置や移動に関わる短期条件 |
| `external_service` | 外部サービスから取得した現在条件 |
| `body` | 身体や内部センサ由来の現在条件 |
| `device` | 接続機器や物理デバイスの現在条件 |
| `schedule` | 近い時刻の予定や締切の前景 |
| `social_context` | 近い文脈で効く相手や会話場の状況 |

個別のハードウェア名やサービス名は `state_type` に入れない。
具体的な取得元は `source_kind`、`source_ref`、capability result の payload 側で扱う。

## 更新責務

`world_state` は、次の入力を受けたときに更新する。

- capability result
- `desktop_watch` などの観測方針による観測結果
- 人からの入力で現在状況が明示された場合
- 外部サービス capability の結果
- 実行結果が次の判断条件を変えた場合

LLM は、観測や結果から `summary_text` と前景性を整理する。
コードは、契約検証、`state_type`、`scope`、`source_ref`、`confidence`、`salience`、`summary_source`、`expires_at`、統合単位、状態遷移を決める。

LLM が返した自由文をそのまま正本状態へ入れない。
コードが source、期限、件数上限、失効を管理する。

## LLM 更新契約

`world_state` 更新に使う LLM 契約は、観測や実行結果から短期外界状態候補を抽出するための補助契約である。
現行設計では専用のモデル role を増やさず、`model_preset.roles.input_interpretation` を使う。

LLM に渡す source pack は少なくとも次を持つ。

```json
{
  "trigger_kind": "desktop_watch",
  "current_input_summary": "desktop_watch が Slack の general チャンネルを前景として観測した。",
  "source_kind": "client_context",
  "source_ref": "cycle:...",
  "time_context": "2026年4月25日 土曜日 9時00分（日本時間）",
  "client_context": {
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  },
  "screen_context": {
    "summary_text": "Slack の general チャンネルが前景で、会話一覧と現在のスレッドが見えている。",
    "visual_summary_text": "Slack の general チャンネルが前景で、会話一覧と現在のスレッドが見えている。",
    "image_interpreted": true,
    "visual_confidence_hint": "medium",
    "image_count": 1,
    "capability_id": "vision.capture"
  },
  "social_context_context": {
    "summary_text": "Slack 上のやり取りが近い判断文脈として前景にある。",
    "social_context_summary": "Slack 上のやり取りが近い判断文脈として前景にある。"
  },
  "environment_context": {
    "summary_text": "作業部屋は静かで、集中しやすい環境にある。",
    "environment_summary": "作業部屋は静かで、集中しやすい環境にある。"
  },
  "location_context": {
    "summary_text": "自宅デスクで作業している。",
    "location_summary": "自宅デスクで作業している。"
  },
  "capability_result_summary": {
    "capability_id": "vision.capture",
    "image_count": 1,
    "image_interpreted": true,
    "visual_summary_text": "Slack の general チャンネルが前景で、会話一覧と現在のスレッドが見えている。",
    "visual_confidence_hint": "medium",
    "error": null
  },
  "existing_foreground_world_state": [
    {
      "state_type": "screen",
      "scope": "topic:current_work",
      "summary_text": "画面では Discord の DM が前景にある。",
      "age_label": "4分前"
    }
  ]
}
```

画面前景の補助要約がある場合は `screen_context` を追加する。
対人文脈、周囲環境、場所、外部サービス、身体、機器、予定の短い summary がある場合は、`social_context_context / environment_context / location_context / external_service_context / body_context / device_context / schedule_context` を追加する。
`schedule_context` には `summary_text` に加えて、wake や `desktop_watch` が再評価対象として選んだ pending-intent の `intent_summary / reason_summary / slot_key / not_before / expires_at` を含める。
`external_service_context` には `summary_text` に加えて、`service / status_text / capability_id` と、必要なら `client_summary_text / result_summary_text / summary_source_hint` のような短い境界補助 field を含める。
`screen_context` には `visual_summary_text / image_interpreted / visual_confidence_hint / image_count / capability_id` を含める。
`social_context_context` には `social_context_summary`、`environment_context` には `environment_summary`、`location_context` には `location_summary` を含める。
`body_context` には `body_state_summary / capability_id`、`device_context` には `device_state_summary / capability_id`、`schedule_context` には `schedule_summary / capability_id` を含める。
real source と client summary が両方あるときは、`body / device / schedule` でも `client_summary_text / result_summary_text / summary_source_hint` を持ってよい。

source pack には、画像、音声、長い外部サービス応答、資格情報、内部 URL、配送先 client を含めない。
画像意味理解を通した場合は `visual_summary_text` を短い補助要約として渡す。
ただし raw image payload 自体は source pack に含めない。

LLM の出力は JSON object 1 個に固定する。

```json
{
  "state_candidates": [
    {
      "state_type": "screen",
      "scope": "topic:current_work",
      "summary_text": "画面では Slack の general チャンネルが前景にある。",
      "confidence_hint": "medium",
      "salience_hint": "medium",
      "ttl_hint": "short"
    }
  ]
}
```

契約は次とする。

- 必須トップレベルキーは `state_candidates` だけにする
- `state_candidates` は配列にする
- 各候補は `state_type / scope / summary_text / confidence_hint / salience_hint / ttl_hint` だけを持つ
- `state_type` はこの文書の `state_type` enum だけを使う
- `scope` は `self / user / entity:<key> / topic:<key> / relationship:<key> / world` のいずれかにする
- `summary_text` は 1 文、改行なし、内部識別子なしにする
- `confidence_hint` と `salience_hint` は `low / medium / high` のいずれかにする
- `ttl_hint` は `short / medium / long` のいずれかにする
- raw payload、資格情報、内部 URL、配送先 client を出力しない

コードは LLM 出力を受けて次を決める。

- `world_state_id`
- `scope_type / scope_key`
- `source_kind / source_ref`
- 数値 `confidence / salience`
- `observed_at / expires_at / updated_at`
- 既存 state との統合、置換、失効
- 件数上限と TTL

validator 失敗時は 1 回だけ再生成する。
再生成後も契約を満たさない場合は、そのサイクルの `world_state` 更新だけを失敗として扱い、判断サイクル本体は入力と想起が成立していれば継続する。
失敗は `world_state_trace` と audit event に残す。

## 失効と整理

`world_state` は、古い外界条件を残し続けない。

少なくとも次の規則を持つ。

- `expires_at` を過ぎた state は判断文脈へ出さない
- `screen / body / device` は state_type ごとの foreground slot 単位で置換する
- `external_service` は `service` 単位で統合または置換する
- `schedule` は generic summary を `schedule:self` へ置き、selected pending-intent がある場合は `slot_key` 単位で統合または置換する
- それ以外は同じ `state_type / scope_type / scope_key` の近い状態を統合または置換する
- `screen` と `environment` は短い TTL を標準にする
- `external_service` は `capability_result.status_text` と `client_context.external_service_summary` のどちらを正本にしたかで TTL を変える
- `schedule` は `schedule_summary` を基準に TTL を決め、pending-intent の `expires_at` があればそれを上限にする
- 長期理解へ育てる条件を満たす出来事は、`turn consolidation` で記憶へ渡す

Phase 7 以降は、`summary_source` として少なくとも次を区別してよい。

- `capability_result.status_text`
- `capability_result.body_state_summary`
- `capability_result.device_state_summary`
- `capability_result.schedule_summary`
- `capability_result.client_context.<field>`
- `client_context.<field>`
- `pending_intent`

古い `world_state` は記憶更新の根拠として扱わない。
根拠は `events` と capability result 側へ辿る。

## 判断入力

判断へ渡す `world_state` は、全件ではなく前景要約にする。

最小形は次とする。

```json
{
  "foreground_world_state": [
    {
      "state_type": "screen",
      "scope": "topic:current_work",
      "summary_text": "画面では Slack の general チャンネルが前景にある。",
      "confidence": 0.82,
      "salience": 0.74,
      "age_label": "1分前"
    }
  ]
}
```

判断 LLM には source credential、内部 URL、raw payload、配送先 client を渡さない。
画像や音声の raw payload は、専用の観測意味理解が入るまで判断入力へ直接渡さない。

## 記憶との関係

`world_state` は、現在条件を判断へ渡すための状態である。
経験として残すかどうかは `turn consolidation` が判断する。

関係は次のとおりである。

- `events`
  - 観測や結果の根拠を残す
- `episodes`
  - その判断サイクルで何が起きたかを束ねる
- `memory_units`
  - 継続理解へ育ったものを持つ
- `world_state`
  - 次の判断に効く現在条件だけを持つ

`world_state.summary_text` をそのまま `memory_units.summary_text` に複写しない。
長期記憶へ入れる場合は、通常の記憶更新規則で候補化、正規化、比較、操作決定を行う。

## inspection

inspection では、`world_state` について少なくとも次を追えるようにする。

- 判断入力へ入った `world_state` 件数
- 前景に出した state の要約
- 更新された state の件数
- 置換された state の件数
- 失効した state の件数
- source pack から `world_state` 更新へ渡した sanitized context summary
- `world_state_trace.source_pack_state_type_hooks` として、`screen / social_context / environment / location / external_service / body / device / schedule` ごとの `summary_text / summary_source / signal_fields / capability_id` 要約
- `world_state_trace.normalized_candidate_policies` として、候補ごとの `summary_source / effective_ttl_seconds / integration_key` 要約
- source kind と source ref の要約
- 失敗した更新の理由

通常の `GET /api/status` には、`world_state` の生 row を返さない。
詳細確認は inspection 面で扱う。

## やらないこと

次は採らない。

- 生 payload を `world_state` 正本にすること
- `world_state` を長期記憶の代替にすること
- `world_state` だけで人格や関係理解を更新すること
- capability availability を `world_state` に入れること
- `ongoing_action` の進行を `world_state` に入れること
- TTL の無い外界状態を作ること
