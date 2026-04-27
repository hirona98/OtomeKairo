# world_state source pack 拡張

## 目的

`world_state` 第一段では、主に `client_context` の画面前景と `desktop_watch` の観測要約を source pack に入れていた。
この phase では、外部サービス、身体、機器、予定の短い current summary も同じ source pack に入れ、LLM が `world_state` 候補を選びやすくする。

ここで扱うのは **短い structured summary だけ** である。
新しい capability、raw payload 保存、長い OCR、配送先 client の露出は入れない。

## 入力境界

`world_state` source pack に追加してよい入力は次に限る。

- `client_context.external_service_summary`
- `client_context.body_state_summary`
- `client_context.device_state_summary`
- `client_context.schedule_summary`
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
  "external_service_context": {
    "summary_text": "GitHub の通知に未確認レビューが 1 件ある。"
  },
  "device_context": {
    "summary_text": "デスクトップ client は利用可能な状態で接続中。"
  },
  "schedule_context": {
    "summary_text": "このあとレビュー確認を続ける予定が近い。",
    "pending_intent": {
      "intent_kind": "conversation_follow_up",
      "intent_summary": "レビュー状況に合わせてまた声をかける。",
      "reason_summary": "あとで続きに触れる価値がある。",
      "not_before": "2026-04-25T09:10:00+09:00",
      "expires_at": "2026-04-25T15:00:00+09:00"
    }
  },
  "existing_foreground_world_state": []
}
```

source pack では、標準の `client_context` と state-type 別の structured context を分ける。
画面前景は `client_context`、その他の短い current summary は dedicated context へ載せる。

## コード責務

- request / capture response の `client_context` から短い summary を抜き出す
- wake / `desktop_watch` の selected pending-intent があるときだけ `schedule_context.pending_intent` を作る
- LLM が返した `state_type / scope / summary_text / hint` を validator で検証する
- TTL、件数上限、統合、失効、永続化は従来どおりコード側が決める

## やらないこと

- 外部サービス capability の新設
- 身体や機器の raw telemetry 保存
- pending-intent queue 全件を source pack に載せること
- `world_state` に capability manifest や binding を直接複写すること
- 長い summary や複数段の構造化 payload をそのまま LLM に渡すこと
