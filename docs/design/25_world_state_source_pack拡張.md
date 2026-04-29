# world_state source pack 拡張

## 目的

`world_state` 第一段では、主に `client_context` の画面前景と `desktop_watch` の観測要約を source pack に入れていた。
この phase では、画面前景の補助要約と、外部サービス、身体、機器、予定の短い current summary も state-type 別 context として同じ source pack に入れ、LLM が `world_state` 候補を選びやすくする。

ここで扱うのは **短い structured summary だけ** である。
新しい capability、raw payload 保存、長い OCR、配送先 client の露出は入れない。

## 入力境界

`world_state` source pack に追加してよい入力は次に限る。

- `vision.capture` result から得た短い `visual_summary_text / image_interpreted / visual_confidence_hint / image_count`
- `client_context.external_service_summary`
- `external.status` result から得た短い `service / status_text`
- capability result の `client_context` から得た短い `body_state_summary`
- capability result の `client_context` から得た短い `device_state_summary`
- capability result の `client_context` から得た短い `schedule_summary`
- wake / `desktop_watch` で再評価対象として選ばれた pending-intent の
  `intent_kind / intent_summary / reason_summary / not_before / expires_at`

どれも 1 文程度の短い summary に留める。
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
    "source": "background_wake_scheduler",
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  },
  "screen_context": {
    "summary_text": "Slack の general チャンネルが前景で、やり取りが見えている。",
    "visual_summary_text": "Slack の general チャンネルが前景で、やり取りが見えている。",
    "image_interpreted": true,
    "visual_confidence_hint": "medium",
    "image_count": 1,
    "capability_id": "vision.capture"
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
    "capability_id": "vision.capture"
  },
  "device_context": {
    "summary_text": "デスクトップ client は利用可能な状態で接続中。",
    "device_state_summary": "デスクトップ client は利用可能な状態で接続中。",
    "capability_id": "vision.capture"
  },
  "schedule_context": {
    "summary_text": "このあとレビュー確認を続ける予定が近い。",
    "schedule_summary": "このあとレビュー確認を続ける予定が近い。",
    "capability_id": "vision.capture",
    "pending_intent": {
      "intent_kind": "conversation_follow_up",
      "intent_summary": "レビュー状況に合わせてまた声をかける。",
      "reason_summary": "あとで続きに触れる価値がある。",
      "slot_key": "pending_intent:topic:review",
      "not_before": "2026-04-25T09:10:00+09:00",
      "expires_at": "2026-04-25T15:00:00+09:00"
    }
  },
  "existing_foreground_world_state": []
}
```

source pack では、標準の `client_context` と state-type 別の structured context を分ける。
画面前景は `client_context` に加えて `screen_context` へ補助要約を載せ、その他の短い current summary は dedicated context へ載せる。
`external.status` のような capability result は、`external_service_context.summary_text` に加えて `service / status_text` を載せる。
`body_context / device_context / schedule_context` でも、capability result 由来のときは `capability_id` と state-type 別 summary field を載せる。

## コード責務

- request / capture response の `client_context` から短い summary を抜き出す
- `vision.capture` result の短い visual summary を `screen_context` へ投影する
- `external.status` result の `service / status_text` を `external_service_context` へ投影する
- capability result の `body_state_summary / device_state_summary / schedule_summary` を対応する state-type context へ投影する
- wake / `desktop_watch` の selected pending-intent があるときだけ `schedule_context.pending_intent` を作り、`slot_key` を付ける
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
