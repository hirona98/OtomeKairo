# world_state source pack 拡張

## 目的

`world_state` 第一段では、視覚前景と短い current summary を同じ source pack に入れていた。
この拡張では、`vision.capture` の視覚補助要約と、対人文脈、周囲環境、場所、外部サービス、身体、機器、予定の短い current summary を state-type 別 context として同じ source pack に入れ、LLM が `world_state` 候補を選びやすくする。
`vision.capture` は `source_kind` に関係なく `visual_context` の候補にする。

ここで扱う正本状態は **短い structured summary だけ** である。
詳細な視覚説明は `visual_observation_record` の正本に置き、source pack では `visual_observation_id` と補助根拠として参照する。
capability 自体の安定契約は `17_capability_manifest.md` と API 文書を正本にし、この文書は source pack に入れる summary 境界だけを扱う。
raw payload 保存、長い OCR、配送先 client の露出は入れない。

## 入力境界

`world_state` source pack に追加する入力は次に限る。

- `vision.capture` result から得た `visual_summary_text / visual_observation_id / image_interpreted / visual_confidence_hint / image_count / vision_source_id / source_kind / source_label`
- `client_context.social_context_summary`
- `client_context.environment_summary`
- `client_context.location_summary`
- `client_context.external_service_summary`
- `external.status` result から得た短い `service / status_text`
- `schedule.status` result から得た短い `schedule_summary / schedule_slots`
- `device.status` result から得た短い `device_state_summary`
- `body.status` result から得た短い `body_state_summary`
- `environment.status` result から得た短い `environment_summary`
- `location.status` result から得た短い `location_summary`
- `social.status` result から得た短い `social_context_summary`
- capability result の `client_context` から得た短い `social_context_summary`
- capability result の `client_context` から得た短い `body_state_summary`
- capability result の `client_context` から得た短い `device_state_summary`
- capability result の `client_context` から得た短い `schedule_summary`
- capability result の `client_context` から得た短い `environment_summary`
- capability result の `client_context` から得た短い `location_summary`
- wake で再評価対象として選ばれた pending-intent の
  `intent_kind / intent_summary / reason_summary / not_before / expires_at`

`visual_summary_text` は詳細な視覚説明であり、現在状態として永続化する値ではない。
`world_state` 候補の `summary_text` は 1 文程度の短い現在状態要約に留める。
raw response body、client 固有 ID、資格情報、内部 URL、base64 本文は入れない。

## source pack shape

追加後の source pack 例:

```json
{
  "trigger_kind": "wake",
  "current_input_summary": "定期起床。いま保留中の会話候補を再評価したい。",
  "source_kind": "system_observation",
  "source_ref": "cycle:...",
  "time_context": "2026年4月25日 土曜日 9時00分（日本時間）",
  "client_context": {
    "source": "background_wake_scheduler"
  },
  "visual_context": {
    "summary_text": "チャットツールの general チャンネルが視覚前景で、やり取りが見えている。",
    "visual_summary_text": "チャットツールの general チャンネルが視覚前景で、やり取りが見えている。",
    "image_interpreted": true,
    "visual_confidence_hint": "medium",
    "image_count": 1,
    "capability_id": "vision.capture",
    "vision_source_id": "vision_source:main_display",
    "source_kind": "desktop",
    "source_label": "メイン画面"
  },
  "social_context_context": {
    "summary_text": "Slack 上のやり取りが近い判断文脈として前景にある。",
    "social_context_summary": "Slack 上のやり取りが近い判断文脈として前景にある。",
    "capability_id": "social.status"
  },
  "environment_context": {
    "summary_text": "作業部屋は静かで、集中しやすい環境にある。",
    "environment_summary": "作業部屋は静かで、集中しやすい環境にある。",
    "capability_id": "environment.status"
  },
  "location_context": {
    "summary_text": "自宅デスクで作業している。",
    "location_summary": "自宅デスクで作業している。",
    "capability_id": "location.status"
  },
  "external_service_context": {
    "summary_text": "GitHub の通知に未確認レビューが 1 件ある。",
    "service": "github",
    "status_text": "GitHub の通知に未確認レビューが 1 件ある。",
    "capability_id": "external.status"
  },
  "body_context": {
    "summary_text": "肩や首に疲れがありそう。",
    "body_state_summary": "肩や首に疲れがありそう。",
    "capability_id": "body.status"
  },
  "device_context": {
    "summary_text": "接続機器は正常に応答している。",
    "device_state_summary": "接続機器は正常に応答している。",
    "capability_id": "device.status"
  },
  "schedule_context": {
    "summary_text": "このあとレビュー確認を続ける予定が近い。",
    "schedule_summary": "このあとレビュー確認を続ける予定が近い。",
    "capability_id": "schedule.status",
    "schedule_slots": [
      {
        "slot_key": "calendar:review",
        "summary_text": "12:20 にレビュー確認がある。",
        "not_before": "2026-04-25T12:20:00+09:00",
        "expires_at": "2026-04-25T12:35:00+09:00"
      }
    ],
    "pending_intent": {
      "intent_kind": "conversation_follow_up",
      "intent_summary": "レビュー状況に合わせてまた声をかける。",
      "reason_summary": "あとで続きに触れる価値がある。",
      "slot_key": "pending_intent:topic:review",
      "not_before": "2026-04-25T09:10:00+09:00",
      "expires_at": "2026-04-25T15:00:00+09:00"
    }
  },
  "allowed_state_types": [
    "visual_context",
    "social_context",
    "environment",
    "location",
    "external_service",
    "body",
    "device",
    "schedule"
  ]
}
```

source pack では、標準の `client_context` と state-type 別の structured context を分ける。
視覚前景は `vision.capture` result の視覚説明を根拠に `visual_context` へ載せ、`vision_source_id` で観測 source を識別する。
`vision.capture` result follow-up の `foreground_world_state` は、result の `vision_source_id` と一致する `visual_context` だけを decision / reply に渡す。
一致しない `visual_context` は保存済み state と inspection 用 trace に残し、同じ follow-up の判断材料にしない。
その他の短い current summary は dedicated context へ載せる。
`current_input_summary` は入力意図と、人が明示した状態値だけを補助する。
確認依頼だけの入力から現在場所、身体状態、端末状態、周囲環境、対人文脈を推測して state 候補を作らない。
`social_context_context / environment_context / location_context` は、`client_context` から取った短い summary をそのまま dedicated context へ写す。
`social.status` result は、`social_context_context.summary_text / social_context_summary` へ投影する。
`external.status` のような capability result は、`external_service_context.summary_text` に加えて `service / status_text` を載せる。
`schedule.status` result は、`schedule_context.summary_text / schedule_summary / schedule_slots` へ投影する。
`device.status` result は、`device_context.summary_text / device_state_summary` へ投影する。
`body.status` result は、`body_context.summary_text / body_state_summary` へ投影する。
`environment.status` result は、`environment_context.summary_text / environment_summary` へ投影する。
`location.status` result は、`location_context.summary_text / location_summary` へ投影する。
同時に client 側 summary もあるときは、`client_summary_text / result_summary_text / summary_source_hint` を追加して境界を残す。
`body_context / device_context / schedule_context` でも、capability result 由来のときは `capability_id` と state-type 別 summary field を載せる。
client summary と result summary が両方あるときは、同様に `client_summary_text / result_summary_text / summary_source_hint` を追加する。
real schedule source が複数あるときは、`schedule_context.schedule_slots` に複数 slot を載せる。
前回の foreground `world_state` は LLM source pack に載せない。
既存状態との置換や inspection のため、コード側の `world_state_trace.previous_foreground_world_state` だけに残す。

## コード責務

- capability result の `client_context` から短い summary を抜き出す
- `vision.capture` result の visual summary、`visual_observation_id`、`vision_source_id / source_kind / source_label` を `visual_context` へ投影する
- `vision.capture` result follow-up では異なる `vision_source_id` の `visual_context` を判断入力から除外する
- `client_context.social_context_summary / environment_summary / location_summary` を対応する dedicated context へ投影する
- `social.status` result の `social_context_summary` を `social_context_context` へ投影する
- `external.status` result の `service / status_text` を `external_service_context` へ投影する
- `schedule.status` result の `schedule_summary / schedule_slots` を `schedule_context` へ投影する
- `device.status` result の `device_state_summary` を `device_context` へ投影する
- `body.status` result の `body_state_summary` を `body_context` へ投影する
- `environment.status` result の `environment_summary` を `environment_context` へ投影する
- `location.status` result の `location_summary` を `location_context` へ投影する
- capability result の `body_state_summary / device_state_summary / schedule_summary / social_context_summary / environment_summary / location_summary` を対応する state-type context へ投影する
- `summary_source` が `capability_result.<field>` と `client_context.<field>` を区別できるように context へ source hint を残す
- `schedule_context.schedule_slots` があるときは deterministic な slot state を追加し、`schedule:self` と `schedule:<slot_key>` を併存させる
- wake の selected pending-intent があるときだけ `schedule_context.pending_intent` を作り、`slot_key` を付ける
- user input の確認依頼だけから作られた現在状態候補は、対応 structured context が無い場合に正規化で落とす
- LLM が返した `state_type / scope / summary_text / hint` を validator で検証する
- TTL は `summary_source` と state_type ごとの規則で決める
- `external_service` の統合単位は `service` を使う
- `schedule` の TTL は pending-intent の `expires_at` を上限に使う
- 件数上限、統合、失効、永続化はコード側が決める

## やらないこと

- 外部サービス capability の新設
- 身体や機器の raw telemetry 保存
- pending-intent queue 全件を source pack に載せること
- `world_state` に capability manifest や binding を直接複写すること
- 長い summary や複数段の構造化 payload をそのまま LLM に渡すこと
