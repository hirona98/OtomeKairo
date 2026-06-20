# autonomous_run 設計RFC

## 目的

`autonomous_run` は、観測、能力実行、発話、待機をまたぐ目的単位を保持する上位実行状態である。
単発の `speech / capability_request / pending_intent / noop` は通常判断の結果として残し、複合行動だけを `autonomous_run` に載せる。

## 状態境界

`autonomous_run` は `ongoing_action` の上位状態である。
`autonomous_run` は目的、現在の段階、履歴、待機条件を持つ。
`ongoing_action` は直近 capability 実行の結果待ちを表す短期状態として残す。

1 件の `autonomous_run` は、少なくとも次を持つ。

| 項目 | 役割 |
|------|------|
| `run_id` | run の識別子 |
| `memory_set_id` | 属する記憶集合 |
| `status` | `active / waiting_timer / waiting_result / paused / completed / cancelled` |
| `objective_summary` | run が目指す目的の短い要約 |
| `origin_kind` | run の開始起点 |
| `current_step_summary` | 現在の段階の短い要約 |
| `history_summary` | これまでの実行履歴の短い要約 |
| `next_run_at` | timer 待機の再開時刻 |
| `waiting_request_id` | capability result 待ちの request |
| `pause_reason` | pause 理由 |
| `created_at / updated_at / completed_at` | lifecycle 時刻 |

`autonomous_run` は capability request の wire payload に載せない。
`request_id` と `run_id` の紐付けは server 内部記録に保持する。

## 判断契約

通常の `decision_generation` は `kind=autonomous_run` を返す。
この decision は run の目的を開始するだけで、次の capability や speech 本文を直接決めない。

`decision.autonomous_run` は次を持つ。

```json
{
  "objective_summary": "発言してからカメラを見て確認する。",
  "initial_step_summary": "最初の一手を判断する。",
  "coordination": {
    "mode": "create_new",
    "target_run_ids": [],
    "reason_summary": "既存 run と独立した新しい目的として開始する。"
  }
}
```

run の次の一手は `autonomous_step_generation` が決める。

## run 調整

`decision_generation` は active / waiting_timer / waiting_result / paused の既存 `autonomous_run` 要約を受け取り、新しい依頼と既存 run の関係を判断する。
`decision.autonomous_run.coordination.mode` は `create_new`、`replace_existing` のいずれかである。

`create_new` は既存 run と独立した目的を開始する。
`replace_existing` は `target_run_ids` の run を `cancelled` にしてから新しい run を開始する。
追加の依頼、タイマー、通知、リマインド、既存 run と並行する一時タスクは `create_new` とする。
既存 run の目的は作成後に変更しない。目的を変える場合は `replace_existing` で新しい run を開始する。

server は `coordination` の契約 shape、対象 run の存在、memory_set、terminal 状態を検証する。
server は既存 run との意味的な近さを文字列一致で判定しない。

## step 契約

`autonomous_step_generation` は、外へ出す action と run の transition を分けて返す。

```json
{
  "action": {
    "kind": "capability_request",
    "capability_request": {
      "capability_id": "vision.capture",
      "input": {
        "vision_source_id": "vision_source:main_display",
        "mode": "still"
      }
    },
    "speech": null
  },
  "transition": {
    "kind": "continue",
    "next_run_at": null
  },
  "run_update": {
    "current_step_summary": "vision.capture の結果を待つ。",
    "history_summary": "action=capability_request:vision.capture transition=continue"
  }
}
```

`action` は常に `kind / capability_request / speech` の 3 キーを持つ。
`action.kind` は `capability_request / speech / none` のいずれかである。
使わない `capability_request` と `speech` は `null` にする。
`transition.kind` は `continue / wait_until / complete / cancel` のいずれかである。
`transition` は `kind / next_run_at` の 2 キーだけを持つ。
`capability_request` action 以外で `wait_until` を使う場合、`next_run_at` を必ず持つ。
`capability_request` action 以外で `wait_until` 以外を使う場合、`next_run_at=null` にする。
`capability_request` action では server が `waiting_result` へ遷移し、`transition.kind` と `next_run_at` を run 遷移には使わない。
`capability_request` action の標準 transition は `kind=continue / next_run_at=null` とする。
`speech` action では `transition.kind=continue` を使わない。継続する場合は `wait_until`、完了する場合は `complete` を使う。
`run_update` は `current_step_summary / history_summary` の 2 キーだけを持つ。
run の `objective_summary` は作成時に固定し、`autonomous_step_generation` は更新しない。
ユーザー起点の開始直後で外向き承諾が自然な場合、`autonomous_step_generation` は `action.kind=speech` と `transition.kind=wait_until` を同時に選ぶ。
固定承諾文は server が生成しない。

## capability 連鎖

run 内では、目的に整合する capability 連鎖を許可する。
`vision.capture -> camera.ptz -> vision.capture -> desktop vision.capture` のような連鎖を扱う。
manifest は schema、権限、source 条件、timeout、busy 判定を担当する。
固定 step 数や固定観測回数の上限は置かない。

capability request が timeout した場合、server は該当 run の `waiting_request_id` を消し、timeout 事実を `last_result_context` と `history_summary` に記録する。
pause 中ではない run は `active` に戻し、`next_run_at` を現在時刻にして `autonomous_step_generation` の再評価対象にする。
pause 中の run は `paused` を維持し、再開時に `active` へ戻る状態にする。
timeout 後に再試行、待機、完了、cancel のどれを選ぶかは `autonomous_step_generation` が判断する。

process startup 時点では capability request の内部照合表が空になる。
このため、`waiting_result` の run と `waiting_request_id` を持つ `paused` run は、再起動前の result を照合できない orphan として扱う。
server は orphan を timeout と同じ再評価可能状態へ戻し、未完了 request で新しい能力実行を塞がない。

## 継続監視

期限なし監視、曖昧な期間の見守り、条件付き通知、継続観測は `autonomous_run` の目的として扱う。
run は必要に応じて `vision.capture`、`camera.ptz`、`wait_until`、`speech` を組み合わせる。
次の観測時刻、継続、完了、中断は `autonomous_step_generation` が目的、履歴、現在時刻、能力可否、直近 result から判断する。
server は特定語句の文字列一致で監視間隔や終了時刻へ変換しない。
ユーザーが停止を明示した場合、対象 run は `cancelled` に遷移する。

## ユーザー割り込み

ユーザー入力開始時、`active` と `waiting_timer` run は `paused` に遷移し、`pause_reason=paused_by_user_interaction` を持つ。
in-flight capability result は受け取る。
ユーザー応答中は、ユーザー起点で開始した最初の step を除き、run の次 step を進めない。
ユーザー応答中に background / capability result 起点の step が完了しても、assistant_message と capability request は送信しない。
ユーザー応答後、`paused_by_user_interaction` の run は再開する。
ユーザーが停止を明示した場合、対象 run は `cancelled` に遷移する。

## 実行直列化

1 つの run_id に対する step 実行は process-local lock で直列化する。
scheduler と capability result thread が同じ run を同時に実行しない。
step 実行前と LLM 後の副作用直前に run 状態を再読込し、terminal、paused、waiting_result、未到来の waiting_timer に変わっている場合は発話と capability request を行わない。

## inspection

`GET /api/autonomous-runs` は run 一覧を返す。
`/api/status` は run 件数を返す。
`GET /api/inspection/current-state` は active、waiting、paused、terminal の run 要約を返す。
操作 API は pause、resume、cancel を提供する。
