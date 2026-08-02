# bootstrapと入力

## bootstrap 面

### `GET /api/bootstrap/probe`

- 認証: 不要
- 役割: bootstrap 面へ到達できるかを確認する

response:

```json
{
  "ok": true,
  "data": {
    "bootstrap_available": true,
    "https_required": true,
    "bootstrap_state": "unregistered"
  }
}
```

`bootstrap_state` は次のいずれかを返す。

| 値 | 意味 |
|----|------|
| `unregistered` | `console_access_token` が未発行であり、`acquire-console-access-token` が token を発行する |
| `registered` | `console_access_token` は発行済みであり、通常 API は認証を要求する |

`probe` は token 実値を返さない。

### `GET /api/bootstrap/server-identity`

- 認証: 不要
- 役割: 接続先の安定識別情報を読む

response:

```json
{
  "ok": true,
  "data": {
    "server_id": "server:...",
    "server_display_name": "OtomeKairo",
    "api_version": "0.7.0",
    "bootstrap_state": "unregistered",
    "console_access_token_issued": false
  }
}
```

`server-identity` は接続先識別と bootstrap 状態だけを返す。
`console_access_token_issued` は発行有無を示す boolean であり、token 実値は返さない。

### `POST /api/bootstrap/acquire-console-access-token`

- 認証: 不要
- 役割: CocoroConsoleが通常APIで使用するtokenを取得する
- request body: `{}` とする
- 成功時HTTP status: `200`

response:

```json
{
  "ok": true,
  "data": {
    "console_access_token": "tok_..."
  }
}
```

未発行状態では新しい token を発行し、発行済み状態では既存 token を返す。
同時要求ではすべてのrequestへ同じ tokenを返す。
CocoroConsoleは取得した token を `Connection.json` へ保存する。
保存tokenが未設定または不正な場合、CocoroConsoleは起動時にこのendpointからtokenを取得し直す。
このendpointへ到達できるクライアントはtokenを取得できる。

### `POST /api/bootstrap/reissue-console-access-token`

- 認証: 必要
- 役割: 認証済みの console が現在の token を新しい token へ置き換える
- request body: `{}` とする

response:

```json
{
  "ok": true,
  "data": {
    "console_access_token": "tok_..."
  }
}
```

再発行に成功したら旧 token は失効する。
新 token の実値はこの response だけで返し、ログ、inspection、状態 API には残さない。

## 対話面

### `POST /api/conversation`

- 認証: 必要
- 役割: 会話入力を受け、会話 1 サイクルを実行する

request:

```json
{
  "text": "こんにちは",
  "images": ["data:image/png;base64,..."],
  "autonomous_run_action": {
    "kind": "cancel_all"
  },
  "interaction_context": {
    "interaction_ref": "interaction:discord:channel-123",
    "speaker_ref": "person:external-123",
    "participants": [
      {
        "person_ref": "person:external-123",
        "display_name": "田中さん"
      }
    ]
  },
  "client_context": {
    "source": "CocoroConsole",
    "client_id": "console-...",
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  }
}
```

- `text` は必須の文字列
- `autonomous_run_action` は任意とし、全自律実行を明示的に停止する場合だけ `{"kind":"cancel_all"}` を渡す
- server は入力文から自律実行の停止意図を推定しない
- `interaction_context` は必須の object
- `interaction_context.interaction_ref` は空でない安定参照
- `interaction_context.speaker_ref` は `person:` で始まり、`participants` に含まれる値
- `interaction_context.participants` は初期実装では1件だけを受理する
- `participants[].person_ref` は `person:` で始まる安定参照
- `participants[].display_name` は敬称を含む完成済みの非空呼び名であり、表示、内部自然文、直接呼称に使用する
- server は `participants[].display_name` に敬称を追加せず、文字列全体を直接呼称として使用する
- `participants[].display_name` は人物同一性、対象選択、配送先の根拠に使用しない
- `images` は任意の画像 Data URI 配列とする。値がないときは省略する
- `images` は最大 1 件とする
- `client_context` は object とする。値がないときは省略する
- 標準の `client_context` には `source / client_id / active_app / window_title / locale` を含める
- `client_context` の任意 field として `social_context_summary / environment_summary / location_summary / external_service_summary / body_state_summary / device_state_summary / schedule_summary` を定義する。いずれも raw payload ではなく短い要約だけを渡す
- server は raw `images` を永続化せず、必要な場合だけ詳細な視覚説明へ変換して shared pipeline と視覚記録へ渡す
- 会話の `images` は `conversation_attachment` として扱い、`vision.capture` の capability result とは結び付けない
- 会話の `images` だけから `world_state.visual_context` を更新しない
- server は上記 summary をそのまま永続化せず、必要な場合だけ `world_state` source pack の補助文脈へ使う
- server は会話入力を `current_input.sender_kind=person`、`sender_ref=<speaker_ref>`、`source_kind=user_message`、`response_target_refs=<participant person_ref群>` として shared pipeline に渡す
- server は人物識別結果を信頼し、認証主体との対応確認となりすまし検出を実行しない
- 会話履歴は `interaction_ref` が一致する event だけから構成する
- 人物発話に対する `speech / noop` の意味判断と validator 境界は [../llm/プロンプト文脈分離方針.md](../llm/プロンプト文脈分離方針.md) を正とする

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "interaction_ref": "interaction:discord:channel-123",
    "recipient_person_refs": ["person:external-123"],
    "result_kind": "speech",
    "speech": {
      "text": "やわらかく穏やかに受け取ったよ。こんにちは",
      "audio_delivery": {
        "delivery_id": "tts_delivery:...",
        "status": "queued",
        "error_code": null
      }
    },
    "capability_request": null,
    "autonomous_run": null
  }
}
```

`result_kind` は外向きに返す主結果の種別であり、内部 `decision.kind` と常に一致しない。
`result_kind` は次のいずれかを返す。

- `speech`
- `capability_request`
- `noop`
- `internal_failure`

`speech.audio_delivery` は発話本文とは独立した音声配送の受付結果である。
shape と配送規則は [event_stream.md](event_stream.md) の `assistant_audio` を正とする。
TTS が無効でも `result_kind=speech` と `speech.text` は成功し、`audio_delivery.status=disabled` を返す。
TTS の配送先が接続されていない場合と queue が満杯の場合も発話本文は成功し、`audio_delivery.status=failed` を返す。

内部で `decision.kind=autonomous_run` が選ばれた場合、server は response の `result_kind` として `autonomous_run` を返さない。
会話入力で即時承諾発話を返す場合は `result_kind=speech` とし、`speech` と `autonomous_run` 要約を返す。
承諾発話がなく capability request を開始した場合は `result_kind=capability_request` とする。
承諾発話も capability request もない場合は `result_kind=noop` とする。

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "speech",
    "speech": {
      "text": "うん、1分後に声をかけるね。",
      "audio_delivery": {
        "delivery_id": "tts_delivery:...",
        "status": "queued",
        "error_code": null
      }
    },
    "capability_request": null,
    "autonomous_run": {
      "run_id": "autonomous_run:...",
      "memory_set_id": "memory_set:...",
      "status": "waiting_timer",
      "objective_summary": "1分後に声をかける。",
      "origin_kind": "user_message",
      "current_step_summary": "指定時刻まで待機する。",
      "history_summary": "action=none transition=wait_until",
      "next_run_at": "2026-06-08T01:11:45+09:00",
      "waiting_request_id": null,
      "pause_reason": null,
      "created_at": "2026-06-08T01:10:45+09:00",
      "updated_at": "2026-06-08T01:10:45+09:00",
      "completed_at": null
    }
  }
}
```

`result_kind=capability_request` のとき、server は capability request を `events/stream` へ配送済みであり、response には `capability_request` 要約を返す。
`capability_request` 要約には `request_id`、`capability_id`、`status`、`timeout_ms`、`readiness_digest` を含め、`target_client_id`、資格情報、内部 URL、transport 詳細は含めない。

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "result_kind": "capability_request",
    "speech": null,
    "capability_request": {
      "request_id": "vision_capture_request:...",
      "capability_id": "vision.capture",
      "status": "dispatched",
      "timeout_ms": 5000,
      "readiness_digest": {
        "family": "visual_observation",
        "world_state_type": "visual_context",
        "input_keys": ["vision_source_id", "mode"],
        "present_input_keys": ["vision_source_id", "mode"],
        "missing_input_keys": [],
        "input_keys_satisfied": true
      }
    }
  }
}
```

`result_kind=noop`、`result_kind=capability_request`、`result_kind=internal_failure` のとき、`speech` は `null` を返す。
`capability_request` が無い結果では、`capability_request` は `null` を返す。
`autonomous_run` が無い結果では、`autonomous_run` は `null` を返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_text` | `text` が文字列ではない |
| `400` | `invalid_images` | `images` が配列でない、2 件以上、Data URI でない、または要素が不正 |
| `400` | `invalid_client_context` | `client_context` が object ではない |
| `400` | `invalid_interaction_context` | `interaction_context` が object でない、または未対応fieldを含む |
| `400` | `invalid_autonomous_run_action` | `autonomous_run_action` が契約外 |
| `400` | `invalid_interaction_ref` | `interaction_ref` が空または文字列でない |
| `400` | `invalid_speaker_ref` | `speaker_ref` が無い、または `person:` 形式でない |
| `400` | `invalid_interaction_participants` | `participants` または人物参照が不正 |
| `400` | `invalid_person_display_name` | `participants[].display_name` が無い、空、または文字列でない |
| `400` | `interaction_speaker_not_participant` | `speaker_ref` が `participants` に含まれない |
| `400` | `unsupported_group_interaction` | `participants` が1件ではない |

人物と相互作用の意味境界は [../foundation/人物と相互作用.md](../foundation/人物と相互作用.md) を正とする。

## 自律面

### `POST /api/wake`

- 認証: 必要
- 役割: API起床要求を受け、wake 1 サイクルを実行する

request:

```json
{
  "interaction_context": {
    "interaction_ref": "interaction:discord:channel-123",
    "participants": [
      {
        "person_ref": "person:external-123",
        "display_name": "田中さん"
      }
    ]
  },
  "client_context": {
    "source": "CocoroConsole",
    "client_id": "console-...",
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  },
  "reference": {
    "uri": "/tmp/otomekairo-watch/camera/latest.jpg",
    "label": "部屋カメラの変化",
    "reason_summary": "軽量CVが前回との差分を検出した。",
    "content_hint": "auto"
  }
}
```

- `client_context` は object とする。値がないときは省略する
- `interaction_context` は任意とし、wake の論理的な対象人物と会話が確定している場合に渡す
- `interaction_context` を渡す場合、`participants[].display_name` は必須の非空呼び名とする
- wake の `interaction_context.speaker_ref` は省略する。指定する場合は `participants` に含める
- `interaction_context` を渡した wake の応答と非同期処理は、その `interaction_ref / participant person_ref群` を引き継ぐ
- `reference` は object とする。値がないときは省略する
- `reference.uri` は必須文字列であり、ローカルパス、`file://` URL、`http://` URL、`https://` URL を受け付ける。最大長は 2048 文字である
- `reference.label` は参照先の短い名前である。値がないときは `reference.uri` を使う。最大長は 512 文字である
- `reference.reason_summary` は外部監視プロセスが wake を要求した理由の短い要約である。最大長は 512 文字である
- `reference.content_hint` は `auto / image / text` のいずれかである。値がないときは `auto` とする
- server は `reference.uri` を wake サイクル開始時に 1 回だけ解決する
- server は `reference` の PNG、JPEG、GIF、WebP 画像を visual observation として判断へ渡し、視覚記録と `world_state` の候補へ使う
- server は `reference` の UTF-8 テキストをその wake サイクルの判断入力へ一時的に渡す
- server は `reference` の raw text と raw image を cycle trace の `client_context` へ保存しない。trace には `uri / label / reason_summary / content_kind / media_type / byte_count / resolved_at` を保存し、画像理解に成功した場合は `visual_summary_text` も保存する
- wake でも `client_context` の `source / active_app / window_title / locale` を起床入力の整形に使う
- wake でも `client_context` の `social_context_summary / environment_summary / location_summary / external_service_summary / body_state_summary / device_state_summary / schedule_summary` があれば、`world_state` source pack の補助文脈へ使う

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "interaction_ref": "interaction:discord:channel-123",
    "recipient_person_refs": ["person:external-123"],
    "result_kind": "noop",
    "speech": null
  }
}
```

`result_kind` は次のいずれかを返す。

- `speech`
- `capability_request`
- `noop`
- `internal_failure`

保留意図は内部結果として扱い、外向きには返さない。
そのため、wake で内部的に保留意図が選ばれた場合も、response の `result_kind` は `noop` とする。
capability 実行を開始した場合は、`POST /api/conversation` と同じ `capability_request` 要約を返す。
内部で `decision.kind=autonomous_run` が選ばれた場合も、server は response の `result_kind` として `autonomous_run` を返さない。
外向き発話がない wake で run だけを開始または待機した場合は `result_kind=noop` とし、`autonomous_run` 要約を返す。

API起床は少なくとも次の挙動を持つ。

- API起床は `wake_policy.mode` と `wake_policy.interval_seconds` による due 判定を使わず即時に wake 1 サイクルを実行する
- API起床は `wake_policy.observations` を実行しない
- API起床に `reference` がある場合、server は参照先の解決結果を判断根拠として使う
- `reference` の取得結果が 5 MiB を超える場合、server は `413 wake_reference_too_large` を返す
- `reference` のテキストが 64 KiB を超える場合、server は `413 wake_reference_too_large` を返す
- `reference.uri` を読み取れない場合、server は `502 wake_reference_unavailable` を返す
- `reference` の内容が画像または UTF-8 テキストとして扱えない場合、server は `415 unsupported_wake_reference_content` を返す
- server は wake 入力を `current_input.sender_kind=system`、`source_kind=wake` として shared pipeline に渡し、`interaction_context` がある場合は参加人物参照を `response_target_refs` に使う
- server 内の定期思考スケジューラは `current_input.sender_kind=system`、`source_kind=background_thinking`、空の `response_target_refs` として shared pipeline に渡す
- server 内の定期思考スケジューラだけが `wake_policy.mode`、`wake_policy.interval_seconds`、`wake_policy.observations` を使う
- 定期思考で `mode=interval` かつ `wake_policy.observations` がある場合、enabled observation を順番に取得し、成功結果をその回の判断へ進む前景シグナルとして扱い、visual capture は `visual_observation` の構造化出力で `change_state` を受け取り、視覚記録と `world_state` を整理してから wake 判断を 1 回だけ行う
- 思考前観測が `failure_code=source_unavailable` の失敗だけで終わった場合、server は interval を消費せず短い再試行待ちにする。失敗コードの正本は [../capability/視覚機能.md](../capability/視覚機能.md) とする
- 思考前観測 の同期 capability request は内部観測として扱い、`ongoing_action` を作らない
- capability request は dispatch 時点の `current_input` を request record の `source_current_input` に保存し、capability result の `response_target_refs` は `source_current_input.response_target_refs` を引き継ぐ
- `source_current_input.response_target_refs=空配列` の capability result は内部観測結果として扱い、実効判断を `noop` に正規化し、assistant message を送信しない
- `source_current_input.response_target_refs=<current person_ref>` の capability request は request record に外向き応答先 client を内部保存し、follow-up capability request へ引き継ぐ
- capability result follow-up の assistant message は、capability result を返した client ではなく request record の外向き応答先 client へ送る
- `wake / background_thinking` の判断で `camera.ptz` を dispatch した場合も同じ `source_current_input` を保存し、result follow-up から同じ camera source の `vision.capture` を内部観測として発行できる
- visual observation は wake 判断へ渡し、`change_state=first_seen / changed` は wake 判断の `visual_observation` 前景候補として扱う。発話可否は観測差分だけで決めず、`speech / noop / pending_intent` の意味判断で比較する
- visual observation の意味変化は `visual_observation` の `change_state / change_basis / change_reason_summary` を正とする。signature は runtime 追跡用の診断値として扱う
- visual observation は wake 判断へ渡す。LLM は change_state、drive_state、world_state、同一観測の反復有無、直近で触れた内容、進行中コミットメントを合わせて `speech / noop / pending_intent` を選ぶ
- 再評価時刻に達した保留意図があれば再評価し、必要なら `speech`

server 内の定期思考スケジューラも、同じ wake 1 サイクルを内部的に使う。

`result_kind=noop`、`result_kind=capability_request`、`result_kind=internal_failure` のとき、`speech` は `null` を返す。
`capability_request` が無い結果では、`capability_request` は `null` を返す。
`autonomous_run` が無い結果では、`autonomous_run` は `null` を返す。

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_client_context` | `client_context` が object ではない |
| `400` | `invalid_interaction_context` | `interaction_context` が object でない、または未対応fieldを含む |
| `400` | `invalid_interaction_ref` | `interaction_ref` が空または文字列でない |
| `400` | `invalid_speaker_ref` | 指定した `speaker_ref` が `person:` 形式でない |
| `400` | `invalid_interaction_participants` | `participants` または人物参照が不正 |
| `400` | `invalid_person_display_name` | `participants[].display_name` が無い、空、または文字列でない |
| `400` | `interaction_speaker_not_participant` | 指定した `speaker_ref` が `participants` に含まれない |
| `400` | `unsupported_group_interaction` | `participants` が1件ではない |
| `400` | `invalid_wake_reference` | `reference` の形式が不正 |
| `413` | `wake_reference_too_large` | `reference` の取得結果が大きすぎる |
| `415` | `unsupported_wake_reference_content` | `reference` の内容が画像または UTF-8 テキストとして扱えない |
| `502` | `wake_reference_unavailable` | `reference.uri` を読み取れない |
