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
  "initial_step_summary": "最初の一手を判断する。"
}
```

run の次の一手は `autonomous_step_generation` が決める。

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
    "reason_code": "observe_next_context",
    "reason_summary": "目的に必要な視覚観測を行う。",
    "next_run_at": null
  },
  "run_update": {
    "objective_summary": "発言してからカメラを見て確認する。",
    "current_step_summary": "vision.capture の結果を待つ。",
    "history_summary": "action=capability_request:vision.capture transition=continue"
  }
}
```

`action` は常に `kind / capability_request / speech` の 3 キーを持つ。
`action.kind` は `capability_request / speech / none` のいずれかである。
使わない `capability_request` と `speech` は `null` にする。
`transition.kind` は `continue / wait_until / complete / cancel` のいずれかである。
`wait_until` は `next_run_at` を必ず持つ。
`capability_request` action は `transition.kind=continue` に固定する。

## capability 連鎖

run 内では、目的に整合する capability 連鎖を許可する。
`vision.capture -> camera.ptz -> vision.capture -> desktop vision.capture` のような連鎖を扱う。
manifest は schema、権限、source 条件、timeout、busy 判定を担当する。
固定 step 数や固定観測回数の上限は置かない。

## ユーザー割り込み

ユーザー入力開始時、`active` と due の `waiting_timer` run は `paused` に遷移し、`pause_reason=paused_by_user_interaction` を持つ。
in-flight capability result は受け取る。
ユーザー応答中は run の次 step を進めない。
ユーザー応答後、`paused_by_user_interaction` の run は再開する。
ユーザーが停止を明示した場合、対象 run は `cancelled` に遷移する。

## inspection

`GET /api/autonomous-runs` は run 一覧を返す。
`/api/status` は run 件数を返す。
`GET /api/inspection/current-state` は active、waiting、paused、terminal の run 要約を返す。
操作 API は pause、resume、cancel を提供する。
