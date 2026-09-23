# world_state source pack

## 目的

`world_state` source pack は、`vision.capture` の視覚補助要約と、対人文脈、周囲環境、場所、外部サービス、身体、機器、予定の current summary を state-type 別 context として同じ source pack に入れ、LLM が `world_state` 候補を選びやすくする。
`vision.capture` は `source_kind` に関係なく `visual_context` の候補にする。

ここで扱う正本状態は **structured summary** である。
短いことが必要な `world_state.summary_text` は、LLM の出力契約で 1 文程度として生成させる。
server は source pack に入れる summary 文字列を文字数で切り詰めない。
詳細な視覚説明は `visual_observation_record` の正本に置き、source pack では `visual_observation_id` と補助根拠として参照する。
capability 自体の安定契約は [capability_manifest.md](capability_manifest.md) と [../api/README.md](../api/README.md) を正本にし、この文書は source pack に入れる summary 境界だけを扱う。
raw payload 保存、長い OCR、配送先 client の露出は入れない。

## 入力境界

`world_state` source pack に入れる入力は次に限る。

- `vision.capture` result から得た `visual_summary_text / visual_observation_id / image_interpreted / visual_confidence_hint / image_count / vision_source_id / source_kind / source_label`
- `client_context.social_context_summary`
- `client_context.environment_summary`
- `client_context.location_summary`
- `client_context.external_service_summary`
- 成功した `mcp.call_tool` result から得た `client_context.mcp_result_summary / mcp_server_id / tool_name`
- capability result の `client_context` から得た `social_context_summary`
- capability result の `client_context` から得た `body_state_summary`
- capability result の `client_context` から得た `device_state_summary`
- capability result の `client_context` から得た `schedule_summary`
- capability result の `client_context` から得た `environment_summary`
- capability result の `client_context` から得た `location_summary`
- wake で再評価対象として選ばれた pending-intent の
  `intent_kind / intent_summary / reason_summary / not_before / expires_at`

`visual_summary_text` は詳細な視覚説明であり、現在状態として永続化する値ではない。
`world_state` 候補の `summary_text` は 1 文程度の短い現在状態要約に留める。
raw response body、MCP の `content / structured_content / arguments`、client 固有 ID、資格情報、内部 URL、base64 本文は入れない。

## source pack shape

source pack 例:

```json
{
  "trigger_kind": "background_thinking",
  "current_input_summary": "定期思考。いまの文脈を再評価したい。",
  "source_kind": "client_context",
  "source_ref": "cycle:...",
  "time_context": "2026年4月25日 土曜日 9時00分（日本時間）",
  "current_person_ref": "person:external-123",
  "client_context": {
    "source": "example_client"
  },
  "state_sources": [
    {
      "candidate_ref": "state_source:social_context",
      "state_type": "social_context",
      "scope_type": "relationship",
      "scope_key": "self|person:external-123",
      "evidence_summary": "チャットでのやり取りが近い判断文脈にある。"
    },
    {
      "candidate_ref": "state_source:environment",
      "state_type": "environment",
      "scope_type": "world",
      "scope_key": "world",
      "evidence_summary": "作業部屋は静かである。"
    },
    {
      "candidate_ref": "state_source:schedule",
      "state_type": "schedule",
      "scope_type": "self",
      "scope_key": "self",
      "evidence_summary": "このあとレビュー確認を続ける予定が近い。"
    }
  ],
  "social_context_context": {
    "summary_text": "チャットでのやり取りが近い判断文脈にある。",
    "social_context_summary": "チャットでのやり取りが近い判断文脈にある。"
  },
  "environment_context": {
    "summary_text": "作業部屋は静かである。",
    "environment_summary": "作業部屋は静かである。"
  },
  "schedule_context": {
    "summary_text": "このあとレビュー確認を続ける予定が近い。",
    "schedule_summary": "このあとレビュー確認を続ける予定が近い。",
    "pending_intent": {
      "intent_kind": "conversation_follow_up",
      "intent_summary": "レビュー状況に合わせてまた声をかける。",
      "reason_summary": "あとで続きに触れる価値がある。",
      "slot_key": "pending_intent:topic:review",
      "not_before": "2026-04-25T09:10:00+09:00",
      "expires_at": "2026-04-25T15:00:00+09:00"
    }
  }
}
```

source pack では、標準の `client_context` と state-type 別の structured context を分ける。
`state_sources` は LLM が選択できる候補集合の正本であり、各 structured context からコードが 1 件ずつ生成する。
`candidate_ref` は request-local な `state_source:<state_type>` とし、`state_type / scope_type / scope_key` はコードが確定する。
LLM は `state_sources` に存在しない候補を生成しない。
視覚前景は `vision.capture` result の視覚説明を根拠に `visual_context` へ載せ、`vision_source_id` で観測 source を識別する。
`vision.capture` result follow-up の `foreground_world_state` は、result の `vision_source_id` と一致する `visual_context` だけを decision / speech に渡す。
一致しない `visual_context` は保存済み state と inspection 用 trace に残し、同じ follow-up の判断材料にしない。
その他の current summary は dedicated context へ載せる。
`current_input_summary` は入力意図と、人が明示した状態値だけを補助する。
確認依頼だけの入力から現在場所、身体状態、端末状態、周囲環境、対人文脈を推測して state 候補を作らない。
`social_context_context / environment_context / location_context` は、`client_context` から取った summary をそのまま dedicated context へ写す。
`mcp.call_tool` result は、`status=completed`、`is_error=false`、`error` が空、`client_context.mcp_result_summary` が非空のときだけ `external_service_context` を作る。
この context は `summary_text / result_summary_text` に `mcp_result_summary`、`service` に `<mcp_server_id>/<tool_name>`、`mcp_server_id / tool_name` に各識別子、`summary_source_hint` に `capability_result.client_context.mcp_result_summary`、`capability_id` に `mcp.call_tool` を載せる。
LLM は結果要約が現在も成立する外部サービスの条件を表す場合に `external_service` 候補へ採用し、単発処理の完了を表す場合は候補を返さない。
`client_context.schedule_slots` が複数あるときは、`schedule_context.schedule_slots` に複数 slot を載せる。
前回の foreground `world_state` は LLM source pack に載せない。
既存状態との置換や inspection のため、コード側の `world_state_trace.previous_foreground_world_state` だけに残す。

## コード責務

- capability result の `client_context` から summary を抜き出す
- `vision.capture` result の visual summary、`visual_observation_id`、`vision_source_id / source_kind / source_label` を `visual_context` へ投影する
- `vision.capture` result follow-up では異なる `vision_source_id` の `visual_context` を判断入力から除外する
- `client_context.social_context_summary / environment_summary / location_summary` を対応する dedicated context へ投影する
- 成功した `mcp.call_tool` result の `mcp_result_summary` と server/tool 複合識別を `external_service_context` へ投影する
- `summary_source` が `capability_result.client_context.<field>` と `client_context.<field>` を区別できるように context へ source hint を残す
- `schedule_context.schedule_slots` があるときは deterministic な slot state を追加し、`schedule:self` と `schedule:<slot_key>` を併存させる
- wake の selected pending-intent があるときだけ `schedule_context.pending_intent` を作り、`slot_key` を付ける
- 対応 structured context が無い状態種別は `state_sources` に追加しない
- LLM が返した `candidate_ref / summary_text / hint` を validator で検証する
- `candidate_ref` からコード確定済みの `state_type / scope_type / scope_key` を解決する
- TTL は `summary_source` と state_type ごとの規則で決める
- `external_service` の統合単位は `service` を使い、MCP は大文字小文字を保持した `mcp_server_id / tool_name` の組単位で別状態にする
- `schedule` の TTL は pending-intent の `expires_at` を上限に使う
- 件数上限、統合、失効、永続化はコード側が決める

## やらないこと

- 外部サービス capability の定義
- 身体や機器の raw telemetry 保存
- pending-intent queue 全件を source pack に載せること
- `world_state` に capability manifest や binding を直接複写すること
- raw response body、MCP arguments、raw telemetry、長い OCR、複数段の外部 payload をそのまま LLM に渡すこと
