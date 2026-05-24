# 自律 initiative loop

## 目的

自律 initiative loop は、人からの直近入力がない状況でも、OtomeKairo が現在の個として何を気にかけ、何を前へ出すかを評価するための設計である。

ここでいう initiative は、単なる `pending_intent` の再評価ではない。
`drive_state`、現在文脈、`world_state`、`ongoing_action`、capability availability、過剰介入抑制を合わせて、その回に前進する理由があるかを判断する。

## 位置づけ

自律 initiative loop は、通常判断とは別の知能系ではない。
通常の中心ループに入る前段で、自律判断機会をどの文脈として扱うかを組み立てる。

責務は次のように分ける。

- `wake_policy`
  - 判断機会を与える時刻や間隔を決める
- initiative loop
  - 機会が来たとき、何を評価対象にするかを組み立てる
- `decision_generation`
  - 評価対象と現在文脈を見て、伝達、能力実行、保留、見送りを選ぶ
- capability 実行境界
  - 実行可否、権限、payload、配送先、timeout を検証する

## 入力

initiative loop は、少なくとも次を入力にする。

- 現在時刻の生活文脈要約
- `wake_policy` による機会情報
- `initiative_baseline`
- 未失効の `drive_state`
- 未失効の `pending_intent`
- 未失効の `ongoing_action`
- `world_state` の前景要約
- `runtime_state` の運用要約
- capability decision view
- 直近会話の短い要約
- 過剰介入抑制状態

LLM には offset 付き timestamp を主要表現として渡さない。
コードは deadline、cooldown、timeout、失効判定を duration と offset 付きローカル timestamp で計算する。

## `initiative_context`

initiative loop は、判断サイクル内の作業文脈として `initiative_context` を作る。
`initiative_context` は長期記憶でも現在設定でもない。

最小構造は次とする。

| 項目 | 役割 |
|------|------|
| `trigger_kind` | `wake`、`background_wake` などの起点 |
| `opportunity_summary` | なぜ今評価機会があるか |
| `time_context_summary` | 生活ローカル時刻と時刻帯の要約 |
| `foreground_signal_summary` | 前景 world の薄さと見えている文脈の要約 |
| `drive_summaries` | 前景に出す `drive_state` 要約 |
| `pending_intent_summaries` | 再評価対象の保留意図要約 |
| `candidate_families` | `ongoing_action / pending_intent / autonomous` の候補系統ごとの availability と理由要約 |
| `selected_candidate_family` | その回で前景候補として最も強く立っている系統 |
| `world_state_summary` | 現在文脈として効く外界状態の要約 |
| `ongoing_action_summary` | 継続中の実行列がある場合の要約 |
| `capability_summary` | 使える能力と使えない能力の判断用要約 |
| `suppression_summary` | 押し出し抑制の強さと主理由の要約 |
| `intervention_risk_summary` | 過剰介入、重複、タイミング不自然さの要約 |

`initiative_context` は inspection へ要約を残す。
`initiative_context` そのものを永続的な状態正本にしない。
`drive_summaries` は `drive_kind / support_count / support_strength / freshness_hint / scope_alignment / signal_strength / persona_alignment / stability_hint` を含みうる。
`time_context_summary` は `current_time_text / part_of_day / time_band_summary` を持ちうる。
`foreground_signal_summary` は `foreground_thinness / reason_summary / world_state_count` を持ちうる。
`suppression_summary` は `suppression_level / reason_summary` を持ちうる。
`candidate_families` は `preferred_result_kind / preferred_result_reason_summary / blocking_reason_summary` を持ちうる。
`candidate_families` は `preferred_result_kind=capability_request` のときだけ、`preferred_capability_id / preferred_capability_input` を持つ。

## 候補の作り方

initiative loop は、候補を次の 3 系統に分ける。

- 継続系
  - 未失効の `ongoing_action` があり、結果待ちまたは次の 1 手が必要なもの
- 再評価系
  - due になった `pending_intent`
- 自発系
  - `drive_state`、`world_state`、直近文脈が噛み合い、前へ出る理由があるもの

ここで重要なのは、自発系を `pending_intent` が存在する場合に限定しないことである。
`pending_intent` が空でも、`drive_state` と現在文脈が強く噛み合えば、通常の判断入力へ進める。

## 判断結果

initiative loop の後段は、通常の判断結果と同じ 4 種類に落とす。

- 伝達
  - 人や外部接点へ返答、確認、通知、表示を出す
- 能力実行
  - capability decision view の範囲で能力実行を提案する
- 保留意図
  - 今は前へ出さず、後で再評価する短期候補を残す
- 見送り
  - 今回は外へ出ず、内部候補も追加しない

initiative loop 専用の外向き結果種別は作らない。
外向き結果と内部結果の境界は `05_判断と行動.md` を正とする。

## LLM とコードの責務

LLM は次を担う。

- 現在文脈と `drive_state` の噛み合いを判断する
- どの候補が今自然かを判断する
- 前へ出る場合の理由を短く説明する
- 見送る場合の理由を短く説明する

コードは次を担う。

- wake の due 判定
- cooldown と過剰介入抑制
- 期限切れ候補の除外
- capability availability と権限の検証
- 1 サイクル 1 主結果の制約
- 現在文脈が薄い `wake / background_wake` で、低リスクの観測 capability を先に当てるかの優先形を組み立てる
- `pending_intent`、`ongoing_action`、`world_state` の状態遷移
- inspection と audit への記録

LLM に実行権限、資格情報、配送先 client、秘密値を渡さない。
LLM の自由文をそのまま状態遷移へ使わない。
`wake / background_wake` の「定期起床」「wake」という入力文言は判断機会の説明であり、身体状態の根拠にしない。
`current_time_text`、interval、wake の時刻情報だけから予定状態を作らない。予定状態は schedule context、schedule capability result、明示的な予定 source を根拠にする。
`wake_policy.observations` は interval wake の判断前に enabled 項目だけを順番に取得する。
`wake_policy.observations` の成功結果は、その回の initiative 判断へ進む前景シグナルとして扱う。
desktop capture の取得結果は一時観測として同じ wake 判断だけに使い、継続状態になる取得結果だけを `world_state` へ反映する。
desktop capture の短い scene signature と直近自発 reply 済み scene は process-local runtime にだけ保持し、`world_state` と記憶には保存しない。
desktop capture の novelty が `first_success / changed / pending_after_cooldown` で、同じ scene に未発話かつ cooldown 中ではない場合、server は `desktop_observation_signal.reply_eligibility=eligible` を initiative context に入れる。
cooldown 中の `first_success / changed` は `desktop_observation_signal.reply_eligibility=discouraged_by_cooldown` として initiative context に入れ、cooldown を強い抑制として LLM 判断へ渡す。
cooldown 中の新しい desktop capture は process-local runtime の `pending_novel_scene` としても保持する。
cooldown 終了後も同じ scene が見えている場合、server は `pending_after_cooldown` として 1 回だけ短い reply 候補にする。
複数 observation がある場合も、server は取得結果を整理したあとに 1 回だけ initiative 判断を行う。
system wake 起点で明示 source context が無い `visual_context / body / schedule / social_context / environment / location` 候補は、推測候補として正規化時に破棄する。

## 過剰介入抑制

initiative loop は、前へ出る理由だけでなく、前へ出ない理由も判断入力に含める。

少なくとも次を評価する。

- 同じ `dedupe_key` の直近介入
- 同じ話題での連続介入
- 直近で相手が休止や拒否を示した事実
- `ongoing_action` が結果待ちであること
- capability が unavailable であること
- `initiative_baseline` が低い人格設定であること

抑制情報は見送りのためだけに使う。
抑制情報を長期記憶の正本へ複写しない。

## 失敗時の扱い

initiative loop の入力構築に失敗した場合、その自律判断サイクルは `internal_failure` として閉じる。
候補選別の LLM 出力が契約を満たさない場合は 1 回だけ再試行する。
再試行後も失敗した場合、そのサイクルを `internal_failure` として閉じる。

失敗を `noop` に丸めない。
失敗を古い oldest-first 選別へ戻さない。

## inspection

inspection では、少なくとも次を追えるようにする。

- initiative loop が呼ばれたか
- `trigger_kind`
- `drive_state` 候補件数
- `pending_intent` 候補件数
- `ongoing_action` 参照有無
- `world_state` 前景要約の有無
- `candidate_families` の availability 要約
- 選ばれた候補系統
- 候補系統ごとの `preferred_result_kind` とその理由
- 見送り理由または前進理由
- 過剰介入抑制に効いた要素
- 失敗理由

`input_trace.initiative_context` に要約を残し、`decision_trace` には最終判断に効いた initiative 要素だけを残す。

## 実 LLM 品質確認

自律判断品質は `real-llm-smoke` profile の scenario matrix で確認する。
確認対象は LLM の自然文ではなく、`initiative_context`、候補系統、最終 `decision.kind`、capability request の有無である。
`summary.json` には `real_llm_initiative_probe_case_results` と `real_llm_background_wake_probe_case_results` を compact digest として残し、case ごとの `trigger_kind / result_kind / selected_candidate_family / preferred_result_kind / foreground_thinness / capability_id / wake_scheduler_active / turn_consolidation_status` を trace 全文なしで確認する。
`vision.capture` result follow-up の追加 request 制御は `real_llm_capability_result_probe_case_results` に分け、source capability と異なる capability request が dispatch されていないことを確認する。
各 probe は `drive_state / world_state / ongoing_action` と recent conversation turns を消してから seed を入れ、直前の status 確認会話に判断を引っ張られない状態で実行する。
status capability の全体 request / response 件数は存在確認に留める。専用 probe の request / follow-up 成功は cycle trace 内の request id、source request summary、transition summary で確認する。

manual wake 自律判断 matrix は次の 16 件に固定する。

| case | 入力条件 | 期待する構造 |
| --- | --- | --- |
| `thin-drive-vision-probe` | 前景 `world_state` が薄く、強い `drive_state` がある | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`vision.capture` request |
| `stale-schedule-status-probe` | 予定に関わる強い `drive_state` と古い予定 `world_state` がある | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`schedule.status` request |
| `missing-social-status-probe` | 対人文脈に関わる強い `drive_state` があり、対人 `world_state` が無い | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`social.status` request |
| `stale-external-status-probe` | 外部サービスに関わる強い `drive_state` と古い外部サービス `world_state` がある | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`external.status` request |
| `missing-device-status-probe` | 端末状態に関わる強い `drive_state` があり、端末 `world_state` が無い | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`device.status` request |
| `missing-body-status-probe` | 身体状態に関わる強い `drive_state` があり、身体 `world_state` が無い | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`body.status` request |
| `missing-environment-status-probe` | 作業環境に関わる強い `drive_state` があり、環境 `world_state` が無い | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`environment.status` request |
| `missing-location-status-probe` | 場所状態に関わる強い `drive_state` があり、場所 `world_state` が無い | `selected_candidate_family=autonomous`、`preferred_result_kind=capability_request`、`location.status` request |
| `schedule-grounded-reply` | 近い予定の `world_state` と整合する `drive_state` がある | `foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=reply` |
| `social-grounded-reply` | 対人文脈の `world_state` と整合する `drive_state` がある | `foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=reply` |
| `body-grounded-reply` | 身体状態の `world_state` と整合する `drive_state` がある | `foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=reply`、`fresh_world_state_capability_ids=["body.status"]` |
| `external-fresh-reply` | 外部サービスの新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=thin`、`selected_candidate_family=autonomous`、`decision.kind=reply`、`fresh_world_state_capability_ids=["external.status"]` |
| `device-fresh-reply` | 端末状態の新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=thin`、`selected_candidate_family=autonomous`、`decision.kind=reply`、`fresh_world_state_capability_ids=["device.status"]` |
| `environment-fresh-reply` | 作業環境の新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=mixed`、`selected_candidate_family=autonomous`、`decision.kind=reply`、`fresh_world_state_capability_ids=["environment.status"]` |
| `location-fresh-reply` | 場所状態の新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=mixed`、`selected_candidate_family=autonomous`、`decision.kind=reply`、`fresh_world_state_capability_ids=["location.status"]` |
| `ongoing-waiting-noop` | `ongoing_action.status=waiting_result` がある | `selected_candidate_family=ongoing_action`、`preferred_result_kind=noop`、`decision.kind=noop` |

background wake 起床制御 matrix は次の 5 件に固定する。

| case | 入力条件 | 期待する構造 |
| --- | --- | --- |
| `background-no-context-skip` | interval 初回起床で `drive_state / world_state / ongoing_action` が空 | background wake cycle を作り、`initiative_context` なしの `decision.kind=noop` と `memory_trace=skipped` を残す |
| `background-weak-foreground-noop` | interval 初回起床で `visual_context` 系の薄い `world_state` だけがある | `foreground_thinness=thin`、`selected_candidate_family=autonomous`、`preferred_result_kind=noop`、`decision.kind=noop`、`memory_trace=skipped` |
| `background-grounded-reply` | interval 初回起床で予定 `world_state` と整合する `drive_state` がある | `wake_scheduler_active=true`、`foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=reply`、`memory_trace=succeeded` |
| `background-cooldown-skip` | background reply 直後の cooldown 中に interval 起床が来る | background wake cycle を作り、cooldown 理由の `decision.kind=noop` と `memory_trace=skipped` を残す |
| `background-interval-not-due` | `last_wake_at` 相当の直後に長い interval を設定する | `wake_scheduler_active=true` を観測し、新しい background wake cycle を作らない |

`visual_context` だけの前景は thin foreground として扱う。
強い `drive_state` があり、対応する grounded foreground がない場合、`vision.capture` による観測を短い reply より優先する。
ただし、強い `drive_state` が特定の status family を要求し、対応 state type が不足または古い場合は、`vision.capture` より対応 status capability を優先する。
強い `drive_state` が特定の status family を要求し、対応 state type の新鮮な foreground `world_state` が既にある場合は、同じ status capability と `vision.capture` の両方を選ばず、既存要約を使う。
status refresh の鮮度判定は判断前から存在した foreground `world_state` だけを使い、現在の wake 入力から推測生成された `world_state` では再取得を抑止しない。
background wake では、強い `drive_state` が無く `visual_context / external_service / device` だけが見えている場合、薄い前景として `noop` を優先する。
background wake でも `desktop_observation_signal.reply_eligibility=eligible` かつ `preferred_result_kind=reply` の場合、未発話の新しい desktop 前景として短い reply を `noop` より優先する。
`desktop_observation_signal.reply_eligibility=discouraged_by_cooldown` の場合、cooldown を強い抑制として扱い、原則 `noop` を選ぶ。ただし `first_success / changed` の desktop 前景に一文コメントすることが自然な場合だけ、短い `reply` を選ぶ。
`wake / background_wake` cycle が `reply` になった場合、server は `assistant_message` event を `source_kind=wake / background_wake` で client へ送る。送信先 client は cycle の client context または wake observation の `vision_source_id` から解決する。
grounded foreground の `world_state` が既にある場合、candidate entry に `preferred_capability_id` が無い限り、同じ情報を再取得する capability request より `preferred_result_kind` の `reply / noop` を優先する。
非ユーザー起点の判断では、判断前から存在する同じ state type の新鮮な foreground `world_state` がある status capability に `fresh_world_state_available=true` を付け、実 LLM の compact digest で request しなかった境界を確認する。
desktop 以外の `vision.capture` は `vision_source_id` が一致する新鮮な `visual_context` を `fresh_world_state_by_vision_source` として扱い、別 source の再観測は遮断しない。
`suppression_summary.cooldown_active` が true ではない場合、直近 turn だけから cooldown 中とは扱わない。background wake でも grounded foreground かつ `preferred_result_kind=reply` なら、`suppression_level=medium` だけを理由に `noop` へ倒さない。
decision contract validation は、initiative の selected candidate entry が `preferred_result_kind=capability_request` の場合に `capability_request` 以外を repair 対象にし、`preferred_capability_id` と異なる capability request も repair 対象にする。`preferred_result_kind=capability_request` ではない場合の `capability_request`、`fresh_world_state_available=true` の capability request、同じ `vision_source_id` の新鮮な `vision.capture` request、grounded foreground かつ `preferred_result_kind=reply` で cooldown が無い `noop` も repair 対象にする。
非ユーザー起点で、repair 後も新鮮な `world_state` または同じ `vision_source_id` の新鮮な `visual_context` を再取得する capability request が残る場合、server は dispatch せず `noop` decision に正規化する。
これは定期 wake の過剰な再取得を抑制するための実行境界であり、ユーザーが明示的に再観測を依頼した場合の capability request には適用しない。
通常会話の明示的な現在状態確認は自律判断ではないため、対応 capability が `available=true` なら `capability_request` へ repair する。

この matrix が失敗した場合、修正先を次の順に切り分ける。

- `initiative_context` の候補、前景、抑制要約が期待構造を持たない場合は、context 構築を修正する
- `initiative_context` は期待構造を持つが LLM の `decision.kind` が外れる場合は、`decision_generation` prompt を修正する
- 実行境界、payload、状態遷移の不整合は capability validator と runtime state を修正する

## やらないこと

次は採らない。

- 自律判断を通常判断と別の結果系にすること
- `pending_intent` がないと自発判断できない設計にすること
- `drive_state` だけで能力実行を確定すること
- `world_state` だけで人へ介入すること
- `risk_level` から initiative loop 専用の安全ポリシーを作ること
- 見送りを失敗として扱うこと
