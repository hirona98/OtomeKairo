# autonomous_run

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
| `consecutive_step_count` | 明示待機を挟まず連続して完了した step 数 |
| `cooldown_until` | 20連続 step 後の強制休止中だけ入る再開可能時刻 |
| `created_at / updated_at / completed_at` | lifecycle 時刻 |
| `source_cycle_id` | run を開始した入力サイクル |
| `source_commitment_memory_unit_ids` | run の根拠になった commitment memory |
| `commitment_resolution` | terminal 時の commitment 更新結果 |

`autonomous_run` は capability request の wire payload に載せない。
`request_id` と `run_id` の紐付けは server 内部記録に保持する。

## commitment 連携

ユーザー依頼から `autonomous_run` を開始した場合、server は run に `source_cycle_id` を保存する。
入力サイクルの記憶統合で active な `commitment` が作成または更新された場合、server はその `memory_unit_id` を `source_commitment_memory_unit_ids` に保存する。
この紐付けは ID によって行い、目的文や発話文の文字列一致で推定しない。

run が `completed` に遷移した場合、server は紐付いた active `commitment` の `commitment_state` を `done` に更新する。
run が `cancelled` に遷移した場合、server は紐付いた active `commitment` の `commitment_state` を `cancelled` に更新する。
terminal 時の発話と terminal 監査イベントは `events` に残し、commitment 更新の evidence に使う。
更新は memory action と revision として記録し、`commitment_resolution` に結果を保持する。

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
人物依頼でも定期思考でも、個が `autonomous_run` または `capability_request` を選んでよい。server は due な関心や MCP 定義から作業を作らない。
`comparison_scope=self_activity` から始まる run の `source_current_input` は、自身の活動用に隔離した current input とする。周期の観測要約入り current input は使わない。
`origin_kind` が `wake` / `background_thinking` で `response_target_refs` が空の step は、前景 `world_state` から `visual_context` を外す。`external_service` など向き側の状態は残す。
MCP tool の連鎖も、他の capability や skill と同じく通常の run step で選ぶ。対象 server への固定や総step数の上限は置かない。連続実行の休止境界は「連続stepクールダウン」を正とする。

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

## 完了前意味検証

`transition.kind=complete` は、run の目的が今回までの実績で満たされたときだけ確定する。
server は capability request 以外の complete 候補について、状態遷移や発話配送より前に `autonomous_completion_review` を行う。

review には次だけを渡す。

- run の目的、現在段階、履歴、観測済み capability result 要約
- complete 候補の `action.kind / run_update`
- `action.kind=speech` の場合だけ生成済み候補本文

review の結果は `allow_complete / continue_run` のいずれかである。

- `allow_complete` は、観測済みの実行結果または今回の発話行為そのものによって目的が満たされ、候補発話も実績と一致していることを表す
- `continue_run` は、目的達成にまだ外界作用、観測、待機が必要か、候補発話が未実行の次行動を現在 run の続きとして表していることを表す

発話自体が目的である run では、今回の speech を完了実績にできる。
外界への作用が目的である run では、予定、準備、意思表明だけを作用の完了実績にしない。
`speech + complete` は、今回の発話で伝達目的を果たす場合、または既に得られた実績を報告して閉じる場合に使う。
次の外界作用を行う向きが残る場合は、その作用を capability request として実行するか、適切な時刻まで `wait_until` で run を維持する。

最初の review が `continue_run` の場合、候補発話を配送せず、complete 遷移も適用しない。
server は候補本文や reviewer の理由を戻さず、固定 feedback で `autonomous_step_generation` を 1 回だけ再実行する。
再生成した complete 候補も `continue_run` の場合、または review 自体が失敗した場合は、未検証の完了へ進めず当該 run を `cancelled` にして明示的な内部失敗として扱う。

各 review は `autonomous_completion_review` event として監査する。保存内容の正本は [デバッグ可能性.md](デバッグ可能性.md) とする。

## capability 連鎖

run 内では、目的に整合する capability 連鎖を許可する。
`vision.capture -> camera.ptz -> vision.capture -> desktop vision.capture` のような連鎖を扱う。
manifest は schema、権限、source 条件、timeout、busy 判定を担当する。
総step数や総観測回数の上限は置かない。

## 連続stepクールダウン

副作用と状態遷移まで成功した `continue` step を `consecutive_step_count` に数える。capability request の `waiting_result` とresult受信は同じstepの完了過程であり、連続回数をリセットしない。`wait_until / complete / cancel` はカウントを0へ戻す。

20回目の連続stepは実行し、21回目の開始前に5分待つ。20回目が capability request 以外なら `waiting_timer` とし、`next_run_at / cooldown_until` を20回目の完了時刻の5分後にする。capability requestなら `waiting_result`を維持し、同じ時刻を `cooldown_until` に保持する。result、timeout、startup時のorphan回復が先に到来した場合は残り時間だけ `waiting_timer` で待ち、到来時点ですでに5分経過していれば追加待機しない。

cooldown中のpauseは `cooldown_until` を保持し、resumeで残り時間を飛ばさない。cooldown終了後は通常の `autonomous_step_generation` で継続、完了、cancelを再評価する。serverがrunを終了させる回数上限ではない。

capability request が timeout した場合、server は該当 run の `waiting_request_id` を消し、timeout 事実を `last_result_context` と `history_summary` に記録する。
pause 中ではない run は、cooldownが残っていなければ `active` に戻して `autonomous_step_generation` の再評価対象にし、残っていれば `waiting_timer` にする。
pause 中の run は `paused` を維持し、再開時にresult待ちまたはtimer待ちを復元する。
timeout 後に再試行、待機、完了、cancel のどれを選ぶかは `autonomous_step_generation` が判断する。

run 内の capability result は、通常の会話 capability result と同じく `capability_result` event として残す。
`mcp.call_tool` の結果は `mcp_result_summary`、対象 server / tool、観測した `observed_person_refs` を event に持つ。
`last_result_context` は完了後も破棄しない。直近 result の要約と観測人物参照を terminal まで残す。
`run_update.history_summary` は LLM が更新してよい。観測事実は `observed_result_summaries` として追記だけし、上書きしない。
各要約は `capability_id / tool_name / result_status / is_error / summary_text / created_at` を持ち、成功実績と失敗到着を区別できるようにする。
公開の働きかけに返すときは、通知や一覧の短い抜粋だけでなく、その会話の根と流れを見てから返す。未読の有無だけで返信要否を決めない。
空の未読一覧や空の私信は、公開のやり取りが無いことの根拠にしない。自分の投稿や公開の会話履歴を見てから、やり取りの有無を確定する。

run が `completed / cancelled` へ遷移したとき、開始サイクルとは別に完了サイクルの `turn consolidation` を行う。
完了サイクルは開始許可ではなく、誰とどの場で何をしたかを episode と memory に残す。
根拠は terminal 発話、`history_summary`、`observed_result_summaries`、capability result event、`observed_persons` である。
完了サイクルは `1判断サイクル = 1episode` を守る。開始サイクルの episode を書き換えない。続き物である場合だけ `episode_series_id` を引き継ぐ。
記憶化の対象条件は [../memory/記憶更新と再整理.md](../memory/記憶更新と再整理.md#非会話サイクルの記憶化) を正とする。

process startup 時点では capability request の内部照合表が空になる。
このため、`waiting_result` の run と `waiting_request_id` を持つ `paused` run は、再起動前の result を照合できない orphan として扱う。
server は orphan を timeout と同じ再評価可能状態へ戻し、未完了 request で新しい能力実行を塞がない。

## 継続監視

期限なし監視、曖昧な期間の見守り、条件付き通知、継続観測は `autonomous_run` の目的として扱う。
run は必要に応じて `vision.capture`、`camera.ptz`、`wait_until`、`speech` を組み合わせる。
次の観測時刻、継続、完了、中断は `autonomous_step_generation` が目的、履歴、現在時刻、能力可否、直近 result から判断する。
server は特定語句の文字列一致で監視間隔や終了時刻へ変換しない。
特定 run は cancel API、会話からの全run停止は `autonomous_run_action.kind=cancel_all` で `cancelled` に遷移する。
server は会話本文から停止意図を推定しない。

## ユーザー割り込み

ユーザー入力開始時、`active` と `waiting_timer` run は `paused` に遷移し、`pause_reason=paused_by_user_interaction` を持つ。
in-flight capability result は受け取る。
ユーザー応答中は、ユーザー起点で開始した最初の step を除き、run の次 step を進めない。
ユーザー応答中に background / capability result 起点の step が完了しても、assistant_message と capability request は送信しない。
ユーザー応答後、`paused_by_user_interaction` の run は再開する。
特定 run の cancel API または `autonomous_run_action.kind=cancel_all` を受けた run は `cancelled` に遷移する。

## 実行直列化

1 つの run_id に対する step 実行は process-local lock で直列化する。
scheduler と capability result thread が同じ run を同時に実行しない。
step 実行前と LLM 後の副作用直前に run 状態を再読込し、terminal、paused、waiting_result、未到来の waiting_timer に変わっている場合は発話と capability request を行わない。

## inspection

`GET /api/autonomous-runs` は run 一覧を返す。
`/api/status` は run 件数を返す。
`GET /api/inspection/current-state` は active、waiting、paused、terminal の run 要約を返す。
操作 API は pause、resume、cancel を提供する。
少なくとも次を追えるようにする。

- run 内の capability result event
- `observed_result_summaries` と `observed_persons`
- 完了サイクルの `turn consolidation` 成否と episode 参照
