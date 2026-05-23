# capability manifest

## 基本方針

capability manifest は、OtomeKairo が使える能力の安定契約である。
本体が capability manifest の正本を持つ。
外部 client は capability manifest を定義しない。
capability manifest、binding、state、availability 判定、decision view の意味規則は、この文書を正本にする。
API 文書は stream、result endpoint、inspection response の wire 契約だけを定める。

capability は、観測、物理作用、外部サービス利用のいずれも同じ契約で扱う。
具体的なセンサ、機器、アプリ、通信先は manifest の実行実体ではなく、能力を実現する接続先として扱う。

モデル role の `web_search_enabled` は生成 provider への request option であり、capability manifest には入れない。
OtomeKairo が主体的に外部サービスへアクセスする機能だけを capability として定義する。

## 三つの層

capability は次の三つを分けて扱う。

| 層 | 役割 | 正本 |
|----|------|------|
| `CapabilityManifest` | 能力の静的な契約を定義する | server |
| `CapabilityBinding` | どの client がどの能力を実行できるかを表す | server が stream 接続から導出する |
| `CapabilityState` | その時点の利用可否、制約、直近失敗などを表す | server |

`CapabilityManifest` は、何ができるか、いつ使うか、何を禁止するか、どの入力と結果を受けるかを定義する。
`CapabilityBinding` は、接続中 client と manifest を結びつける。
`vision.capture` のように複数の実行対象を持つ capability は、binding に加えて `VisionSource` で具体的な観測対象を指定する。
`CapabilityState` は、manifest と binding を踏まえた現在の実行条件を持つ。

client の `hello.caps` は `CapabilityBinding` の材料であり、manifest の正本ではない。
未知の capability id、未対応の version、権限不足の binding は実行不可として `CapabilityState` に記録する。

## availability の正本

capability availability は、接続中 client の自己申告ではなく、server が導出した現在状態である。
server は次を照合して capability availability を決める。

- server が持つ `CapabilityManifest`
- stream 接続から導出した `CapabilityBinding`
- 認証済み client または接続主体の権限
- `CapabilityState` にある一時停止、busy、cooldown、直近成功/失敗、動的一時 unavailable、並列制限、前提条件

`hello.caps` は availability の正本ではない。
`hello.caps` は `CapabilityBinding` を作る入力であり、server に受理されたあとも権限、状態、制約によって実行不可になる。
外向きに現在の availability を確認する正本 API は `GET /api/inspection/capabilities` とする。
`GET /api/catalog` は capability manifest 一覧や availability を返さない。
`CapabilityState.paused` の切替 API は `PATCH /api/capabilities/{capability_id}/state` とし、manifest と binding は変更しない。
判断 LLM に渡す decision view と inspection の capability availability は、同じ server 派生状態から作る。
inspection には運用確認に必要な binding 要約を出すが、token、credential、内部 URL、transport 詳細は出さない。

## Manifest の最小構造

1 件の `CapabilityManifest` は少なくとも次を持つ。

| 項目 | 役割 |
|------|------|
| `id` | capability の canonical 識別子 |
| `version` | manifest 契約の版 |
| `kind` | `observation`、`action`、`external_service` のいずれか |
| `decision_description` | 判断へ渡す短い説明 |
| `when_to_use` | 使用する条件 |
| `do_not_use_when` | 使用しない条件 |
| `required_permissions` | 実行に必要な権限 |
| `input_schema` | capability 固有入力の schema |
| `result_schema` | capability 固有結果の schema |
| `side_effects` | 外界作用、利用者可視性、raw payload 保存有無 |
| `risk_level` | `low`、`medium`、`high` のいずれか |
| `timeout_ms` | 標準 timeout |
| `memory_policy` | 結果を記憶更新候補へ渡す条件 |
| `state_policy` | `ongoing_action`、result/follow-up hook、cooldown/unavailable 反映 |
| `decision_readiness` | 判断で使う family、対応 `world_state`、入力 key、結果要約 key |
| `inspection_fields` | inspection に残す要約項目 |

利用可否、接続 client、直近失敗、cooldown、権限不足は manifest に入れない。
これらは `CapabilityBinding` と `CapabilityState` に入れる。

`risk_level` は分類、判断入力、inspection のための項目である。
現時点では、`risk_level` に基づく承認フロー、禁止条件、追加確認の安全ポリシーは正本化しない。

## Manifest 例

`vision.capture` の manifest は次の形を基準にする。
現行の concrete capability は `vision.capture`、`external.status`、`schedule.status`、`device.status`、`body.status`、`environment.status`、`location.status`、`social.status` である。
`external.status` は短い外部状態要約、`schedule.status` は短い予定要約と deterministic な schedule slot、`device.status` は短い端末状態要約、`body.status` は短い身体状態要約、`environment.status` は短い周囲環境要約、`location.status` は短い場所状態要約、`social.status` は短い対人文脈要約を result として返す。
現行では各 capability の `client_context.body_state_summary / device_state_summary / schedule_summary / environment_summary / location_summary / social_context_summary`、`schedule.status.schedule_slots`、`device.status.device_state_summary`、`body.status.body_state_summary`、`environment.status.environment_summary`、`location.status.location_summary`、`social.status.social_context_summary` を inspection_fields 経由で短い観測要約へ投影する。

```json
{
  "id": "vision.capture",
  "version": "1",
  "kind": "observation",
  "decision_description": "指定された視覚 source の現在状態を観測する",
  "when_to_use": [
    "ユーザーが画面、カメラ、視覚 source の内容について質問した",
    "判断に指定 source の現在視覚状態が必要"
  ],
  "do_not_use_when": [
    "ユーザーが視覚観測を拒否している",
    "現在の判断に視覚情報が不要",
    "観測対象の vision_source_id が一意に定まらない"
  ],
  "required_permissions": ["observe_vision"],
  "input_schema": {
    "type": "object",
    "properties": {
      "vision_source_id": { "type": "string", "pattern": "^vision_source:" },
      "mode": { "type": "string", "enum": ["still"] }
    },
    "required": ["vision_source_id", "mode"],
    "additionalProperties": false
  },
  "result_schema": {
    "type": "object",
    "properties": {
      "images": {
        "type": "array",
        "items": { "type": "string" },
        "maxItems": 1
      },
      "client_context": {
        "type": ["object", "null"]
      },
      "error": {
        "type": ["string", "null"]
      }
    },
    "required": ["images"],
    "additionalProperties": false
  },
  "side_effects": {
    "external_world": false,
    "user_visible": false,
    "stores_raw_payload": false
  },
  "risk_level": "low",
  "timeout_ms": 5000,
  "memory_policy": {
    "record_result_event": true,
    "allow_memory_update": true
  },
  "state_policy": {
    "creates_ongoing_action": true,
    "blocks_parallel_capability": true,
    "result_context_hook": "vision_capture",
    "followup_hint_hook": "vision_capture",
    "error_cooldown_seconds": 15,
    "unavailable_seconds_on_dispatch_failure": 15,
    "unavailable_seconds_on_timeout": 15
  },
  "decision_readiness": {
    "family": "visual_observation",
    "world_state_type": "visual_context",
    "input_keys": ["vision_source_id", "mode"],
    "result_summary_keys": ["visual_summary_text"]
  },
  "inspection_fields": [
    "capability_id",
    "target_client_id",
    "vision_source_id",
    "source_kind",
    "source_label",
    "data_source",
    "unconnected_reason",
    "image_count",
    "image_interpreted",
    "visual_summary_text",
    "visual_confidence_hint",
    "body_state_summary",
    "device_state_summary",
    "schedule_summary",
    "error"
  ]
}
```

`vision.capture.input.vision_source_id` は `VisionSourceRegistry` に存在する id を指定する。
`VisionSource` はカメラ、画面、仮想画面、watch 対象を表し、具体的な client と source metadata を持つ。
`VisionSource` の意味境界は [26_視覚機能実装設計.md](26_視覚機能実装設計.md) を正とする。
server は `vision_source_id` から実行先 client と観測対象を一意に決める。
source が一意に定まらない場合、server は `vision.capture` を dispatch しない。
source ごとの `required_permissions` は `VisionSource` が持ち、manifest の `observe_vision` と合わせて検証する。
現行実装では event stream 接続へ `observe_vision / observe_desktop / observe_camera` を固定付与し、hello 登録時に manifest と source の `required_permissions` を検証する。

`state_policy` は少なくとも次を持つ。

- `creates_ongoing_action`: capability request を `ongoing_action` に結びつけるか
- `blocks_parallel_capability`: 同系統の result 待ち中に並列実行を止めるか
- `result_context_hook`: accepted async result を follow-up 入力へ意味付けするときの hook 名
- `followup_hint_hook`: follow-up 結果要約や runtime state 要約を作る hook 名
- `success_cooldown_seconds` / `error_cooldown_seconds`: 直近成功または失敗後に inspection state へ残す cooldown 秒数
- `unavailable_seconds_on_dispatch_failure` / `unavailable_seconds_on_timeout`: dispatch failure や timeout を一時 unavailable として残す秒数

## LLM へ渡す decision view

判断 LLM へ manifest 全体を渡さない。
server は manifest、binding、state から decision view を組み立てる。

decision view は少なくとも次を持つ。

| 項目 | 役割 |
|------|------|
| `id` | capability の canonical 識別子 |
| `version` | 使用する manifest 版 |
| `available` | 現在実行可能か |
| `kind` | 能力種別 |
| `what_it_does` | 判断用説明 |
| `when_to_use` | 使用条件 |
| `do_not_use_when` | 禁止条件 |
| `required_input` | LLM が組み立てる入力の要約 |
| `risk_level` | 判断上のリスク |
| `unavailable_reason` | 実行不可の場合の理由 |
| `readiness` | family、対応 `world_state` type、入力 key、結果要約 key |
| `fresh_world_state_available` | 非ユーザー起点で同じ state type の新鮮な foreground `world_state` があるか |
| `fresh_world_state` | 再取得せず判断根拠に使う短い `world_state` 要約 |
| `fresh_world_state_by_vision_source` | `vision.capture` 用の `vision_source_id` 単位の新鮮な `visual_context` 要約 |
| `fresh_world_state_policy` | 新鮮な `world_state` があるときの再取得禁止理由 |

decision view には token、credential、内部 URL、`target_client_id`、transport 詳細、raw schema の秘密値を入れない。
LLM は decision view に基づいて `capability_id` と capability 固有入力を提案する。
server は manifest、binding、state、権限で提案を検証する。
busy、権限不足、動的一時 unavailable は decision view の `available: false` に反映する。
cooldown、直近成功、直近失敗は inspection の `CapabilityState` へ残し、明示的な capability 要求まで一律に遮断する理由にはしない。
`readiness` は manifest の `decision_readiness` から作る。
`readiness.family` は `visual_observation / external_status / schedule_status / device_status / body_status / environment_status / location_status / social_status` のいずれかである。
`readiness.world_state_type` は fresh `world_state` 再利用、status family の不足判定、result 投影先を揃えるための正本である。
`readiness.input_keys` は LLM が capability 固有入力を組み立てる最小 key を表す。
`readiness.result_summary_keys` と `readiness.result_item_keys` は result が判断・記憶・inspection へ投影される要約 key を表す。
非ユーザー起点の判断では、`external.status / schedule.status / device.status / body.status / environment.status / location.status / social.status` が見る state type に、判断前から存在する新鮮な foreground `world_state` がある場合、server は該当 decision view 項目に `fresh_world_state_available=true` を付ける。
`vision.capture` は `visual_context` の state type だけでは判断せず、`vision_source_id` が一致する新鮮な foreground `world_state` を `fresh_world_state_by_vision_source` に入れる。
`source_kind=desktop` の `vision.capture` は `world_state` を作らないため、`fresh_world_state_by_vision_source` の対象にしない。
`capability_result` follow-up では、desktop 以外の result から作った current foreground `world_state` も同じ再利用対象にする。
`fresh_world_state_available=true` の capability request と、`fresh_world_state_by_vision_source` にある同じ `vision_source_id` の `vision.capture` request は、同じ現在状態の再取得として decision contract validation の repair 対象にする。
通常会話でユーザーが明示的に現在状態確認を依頼した場合、この freshness による遮断は適用しない。
通常会話でユーザーが `status` 系または観測系 capability に対応する現在状態確認を明示した場合、対応 capability が `available=true` なら decision contract validation は `capability_request` 以外の判断を repair 対象にする。
自律判断で強い `drive_state` が特定の status family を要求し、対応 state type が不足または古い場合、server は `initiative_context.candidate_families` の selected autonomous entry に `preferred_result_kind=capability_request` と対応 `preferred_capability_id` を入れる。

inspection の `CapabilityState` は少なくとも次を持つ。

- `paused`
- `busy` / `busy_request_id` / `busy_action_id`
- `cooldown_active` / `cooldown_until`
- `last_failure_at` / `last_failure_summary`
- `last_result_at` / `last_result_summary`
- `unavailable_active` / `unavailable_reason` / `unavailable_until`
- `parallel_blocked_by_action_id`

## 実行時の流れ

capability 実行は次の順序で行う。

1. client が `GET /api/events/stream` に接続し、`hello.caps` を送る。
2. server が `hello.caps` と既知の manifest を照合し、`CapabilityBinding` を更新する。
3. server が manifest、binding、state から判断用 decision view を作る。
4. LLM が `capability_id` と入力 payload を含む実行要求案を返す。
5. server が `input_schema`、権限、利用可否、`ongoing_action`、並列制限を検証し、`risk_level` を実行記録と inspection へ残す。
6. server が binding から実行先 client を選び、stream で request を送る。
7. client が capability family 共通の result endpoint へ結果を返す。
8. server が `request_id`、`target_client_id`、`result_schema` を検証する。
9. server が `capability_id` ごとの follow-up pipeline で `memory_policy`、`state_policy`、`inspection_fields` に従って記憶、状態、inspection を更新する。

どこまで実装済みか、どの result endpoint が concrete に開いているか、follow-up pipeline の現在地は [../01_現在地.md](../01_現在地.md) を正とする。

LLM が実行要求案を出しても、server の検証を通らない要求は実行しない。
検証失敗は判断サイクルの `internal_failure` または capability failure として記録する。

## 権限との関係

capability 実行可否は `required_permissions` と認証済み client または event stream 接続主体の権限を照合して決める。
権限は人格設定、記憶、LLM 判断結果から導出しない。

権限不足の capability は decision view で `available: false` とし、`unavailable_reason` に `permission_denied` を入れる。
権限不足を LLM 側の自粛だけに任せない。

## 追加ルール

新しい capability を追加するときは、同じ変更内で次を定義する。

- `CapabilityManifest`
- `hello.caps` で通知する `{id, version}`
- stream で送る request message
- result endpoint の request / response
- `CapabilityBinding` の選択規則
- `CapabilityState` に残す動的状態
- 権限名と必要条件
- memory / state / inspection への反映規則

センサや物理デバイスを追加するときも、上位設計には個別機器名ではなく capability 契約を追加する。
