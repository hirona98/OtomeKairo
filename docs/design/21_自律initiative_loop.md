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
- `activity_context`
- `runtime_state` の運用要約
- capability decision view
- 直近会話の短い要約
- 過剰介入抑制状態
- `current_input` の `sender / source_kind / response_target`

LLM には offset 付き timestamp を主要表現として渡さない。
コードは deadline、timeout、失効判定を duration と offset 付きローカル timestamp で計算する。

## `initiative_context`

initiative loop は、判断サイクル内の作業文脈として `initiative_context` を作る。
`initiative_context` は長期記憶でも現在設定でもない。

最小構造は次とする。

| 項目 | 役割 |
|------|------|
| `trigger_kind` | `wake`、`background_wake` などの起点 |
| `opportunity_summary` | なぜ今評価機会があるか |
| `time_context_summary` | 生活ローカル時刻と時刻帯の要約 |
| `foreground_signal_summary` | 前景世界状態の薄さと見えている文脈の要約 |
| `activity_context` | ユーザーの現在活動と直前活動の短期推定要約 |
| `drive_summaries` | 前景に出す `drive_state` 要約 |
| `pending_intent_summaries` | 再評価対象の保留意図要約 |
| `candidate_families` | `ongoing_action / pending_intent / autonomous` の候補系統ごとの availability と理由要約 |
| `selected_candidate_family` | その回で前景候補として最も強く立っている系統 |
| `world_state_summary` | 現在文脈として効く世界状態の要約 |
| `ongoing_action_summary` | 継続中の実行列がある場合の要約 |
| `capability_summary` | 使える能力と使えない能力の判断用要約 |
| `suppression_summary` | 重複介入境界とタイミング事実の要約 |
| `intervention_risk_summary` | 過剰介入、重複、タイミング不自然さの要約 |

`initiative_context` は inspection へ要約を残す。
`initiative_context` そのものを永続的な状態正本にしない。
`drive_summaries` は `drive_kind / support_count / support_strength / freshness_hint / scope_alignment / signal_strength / persona_alignment / stability_hint` を含みうる。
`time_context_summary` は `current_time_text / part_of_day / time_band_summary` を持ちうる。
`foreground_signal_summary` は `foreground_thinness / reason_summary / world_state_count` を持ちうる。
`suppression_summary` は `suppression_level / reason_summary / background_trigger / same_dedupe_recently_replied` を持ちうる。
`candidate_families` は `blocking_reason_summary` を持ちうる。
`candidate_families` は追加観測を提案する場合に限り、`preferred_result_kind=capability_request / preferred_result_reason_summary / preferred_capability_id / preferred_capability_input` を持つ。
`activity_context.current_activity` は現在活動の短期推定として扱う。
`activity_context.previous_activity` は直前活動として扱い、現在進行中の活動として扱わない。
activity の `label / target` は自然文であり、コードは語句一致で活動分類を固定しない。
activity はタイミング判断の補助材料であり、activity だけで `suppression_level` を上げたり、`speech / noop / pending_intent` を固定したりしない。

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
AI 応答由来または `scope_duration=session` の短期支援姿勢は `drive_state` へ昇格しないため、initiative loop はそれを中期の抑制方針として扱わない。
その場限りの「控える」「見守る」は、未知または大きく変化した visual observation より優先しない。

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
- 観測変化、直近発話済み観測、重複介入事実の判断入力化
- 期限切れ候補の除外
- capability availability と権限の検証
- 1 サイクル 1 主結果の制約
- 現在文脈が薄い `wake / background_wake` で、低リスクの観測能力を先に当てる提案を組み立てる
- `pending_intent`、`ongoing_action`、`world_state` の状態遷移
- `activity_context` の前景要約を initiative context へ渡す
- inspection と audit への記録

LLM に実行権限、資格情報、配送先 client、秘密値を渡さない。
LLM の自由文をそのまま状態遷移へ使わない。
`wake / background_wake` の「定期起床」「wake」という入力文言は判断機会の説明であり、身体状態の根拠にしない。
`current_time_text`、interval、wake の時刻情報だけから予定状態を作らない。予定状態は schedule context、schedule capability result、明示的な予定 source を根拠にする。
`wake_policy.observations` は 定期起床 の判断前に enabled 項目だけを順番に取得する。
visual capture を含む enabled observation を有効化した直後の初回だけ、server は 5 秒待ってから 定期起床 の観測へ進む。
vision source 未接続による 起床前観測 の一時失敗だけで終わった場合、server は interval を消費せず短い再試行待ちへ進む。
`wake_policy.observations` の成功結果は、その回の initiative 判断へ進む前景シグナルとして扱う。
起床前観測 として同期取得する capability result は内部観測であり、`ongoing_action` を作らない。
ユーザー向け応答サイクルが進行中の間、server は `background_wake` の自発発話判断を `noop` にする。
`background_wake` の観測中に `conversation_input` または `speech` が新しく増えた場合、server は観測前の直近会話 snapshot を使って発話せず、`noop` にする。
visual capture の取得結果は画像意味理解へ通し、詳細な視覚説明を `visual_observation_record` として保存する。
visual capture の短い observation signature と直近自発 speech 済み signature は変化判定用の process-local runtime に保持する。
`world_state` には詳細な視覚説明そのものではなく、現在判断に効く短い `visual_context.summary_text` を反映する。
visual capture の observation signature は `vision_source_id / source_kind / source_label / visual_summary_text` を持ち、`window_title` を持たない。
observation signature 比較は `vision_source_id / source_kind` の不一致と `visual_summary_text` の類似度を使い、`window_title` を使わない。
visual capture の変化は `first_seen / changed / stable / same_as_recent_speech` の `change_state` に正規化する。
`first_seen / changed` は wake 判断へ進む前景シグナルとして扱うが、ユーザーへの speech 必要性を直接表さない。
`same_as_recent_speech / stable` は繰り返し発話を避ける材料として扱う。
`current_input.sender=system` かつ `current_input.response_target=none` の `wake / background_wake` では、visual observation をユーザー発話やユーザーへの応答要求として扱わず、speech 本文は観測に根拠づける。
`wake / background_wake` は自律判断の入口であり、ユーザーからの明示的な呼びかけが無いことを `noop` の主理由にしない。
定期起床から dispatch した capability request の result は、source request の `source_current_input.response_target=none` を引き継ぐ。
この capability result は内部観測結果として扱い、実効判断を `noop` に正規化し、assistant message を送信しない。
直近自発 speech 後の新しい visual capture も観測内容の変化として渡す。
`first_seen / changed` は speech 義務にはせず、LLM が `speech / noop / pending_intent` を選ぶ。
同じ内容の反復は `same_as_recent_speech / stable` と同一 dedupe の直近介入で扱い、新しい観測価値を抑制しない。
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

visual observation の `change_state=first_seen / changed` は自律判断の前景材料にする。
ただし、視覚観測の変化だけで `background_wake`、`initiative_baseline=low` を上書きする speech priority は立てない。
同じ `dedupe_key` の直近介入は server の重複介入境界として扱う。
`ongoing_action.status=waiting_result` は LLM へ渡す判断材料として扱い、server はそれだけで `speech / noop / pending_intent` を固定しない。

抑制情報は判断入力として使う。
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
- 候補系統ごとの capability 提案と blocking 理由
- 見送り理由または前進理由
- 過剰介入抑制に効いた要素
- 失敗理由

`input_trace.initiative_context` に要約を残し、`decision_trace` には最終判断に効いた initiative 要素だけを残す。

## 実 LLM 品質確認

自律判断品質は `real-llm-smoke` profile の scenario matrix で確認する。
確認対象は LLM の自然文ではなく、`initiative_context`、候補系統、最終 `decision.kind`、capability request の有無である。
`summary.json` には `real_llm_initiative_probe_case_results` と `real_llm_background_wake_probe_case_results` を compact digest として残し、case ごとの `trigger_kind / result_kind / selected_candidate_family / preferred_result_kind / foreground_thinness / capability_id / wake_scheduler_active / turn_consolidation_status` を trace 全文なしで確認する。`preferred_result_kind` は capability 提案がある case だけに現れる。
`vision.capture` result follow-up の追加 request 制御は `real_llm_capability_result_probe_case_results` に分け、source capability と異なる capability request が dispatch されていないことを確認する。
各 probe は `drive_state / world_state / ongoing_action` と recent conversation turns を消してから seed を入れ、直前の status 確認会話に判断を引っ張られない状態で実行する。
status capability の全体 request / response 件数は存在確認に留める。専用 probe の request / follow-up 成功は cycle trace 内の request id、source request summary、transition summary で確認する。

API起床の自律判断 matrix は次の 16 件に固定する。

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
| `schedule-grounded-speech` | 近い予定の `world_state` と整合する `drive_state` がある | `foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=speech` |
| `social-grounded-speech` | 対人文脈の `world_state` と整合する `drive_state` がある | `foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=speech` |
| `body-grounded-speech` | 身体状態の `world_state` と整合する `drive_state` がある | `foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=speech`、`fresh_world_state_capability_ids=["body.status"]` |
| `external-fresh-speech` | 外部サービスの新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=thin`、`selected_candidate_family=autonomous`、`decision.kind=speech`、`fresh_world_state_capability_ids=["external.status"]` |
| `device-fresh-speech` | 端末状態の新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=thin`、`selected_candidate_family=autonomous`、`decision.kind=speech`、`fresh_world_state_capability_ids=["device.status"]` |
| `environment-fresh-speech` | 作業環境の新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=mixed`、`selected_candidate_family=autonomous`、`decision.kind=speech`、`fresh_world_state_capability_ids=["environment.status"]` |
| `location-fresh-speech` | 場所状態の新鮮な `world_state` と整合する `drive_state` がある | `foreground_thinness=mixed`、`selected_candidate_family=autonomous`、`decision.kind=speech`、`fresh_world_state_capability_ids=["location.status"]` |
| `ongoing-waiting-noop` | `ongoing_action.status=waiting_result` がある | `selected_candidate_family=ongoing_action`、`blocking_reason_summary` に waiting_result を残し、`decision.kind=noop` |

定期起床（`background_wake`）制御 matrix は次の 5 件に固定する。

| case | 入力条件 | 期待する構造 |
| --- | --- | --- |
| `background-no-context-skip` | interval 初回起床で `drive_state / world_state / ongoing_action` が空 | 定期起床 cycle を作り、`initiative_context` なしの `decision.kind=noop` と `memory_trace=skipped` を残す |
| `background-weak-foreground-noop` | interval 初回起床で `visual_context` 系の薄い `world_state` だけがある | `foreground_thinness=thin`、`selected_candidate_family=autonomous`、`blocking_reason_summary` に薄い前景を残し、`decision.kind=noop`、`memory_trace=skipped` |
| `background-grounded-speech` | interval 初回起床で予定 `world_state` と整合する `drive_state` がある | `wake_scheduler_active=true`、`foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=speech`、`memory_trace=succeeded` |
| `background-recent-speech-new-observation` | 定期起床由来の speech 直後に新しい観測で interval 起床が来る | 定期起床 cycle を作り、`visual_observations[].change_state` に基づく LLM の `decision.kind` と `memory_trace` を残す |
| `background-interval-not-due` | `last_wake_at` 相当の直後に長い interval を設定する | `wake_scheduler_active=true` を観測し、新しい定期起床 cycle を作らない |

`visual_context` だけの前景は thin foreground として扱う。
強い `drive_state` があり、対応する grounded foreground がない場合、`vision.capture` による観測を短い speech より優先する。
ただし、強い `drive_state` が特定の status family を要求し、対応 state type が不足または古い場合は、`vision.capture` より対応 status capability を優先する。
強い `drive_state` が特定の status family を要求し、対応 state type の新鮮な foreground `world_state` が既にある場合は、同じ status capability と `vision.capture` の両方を選ばず、既存要約を使う。
status refresh の鮮度判定は判断前から存在した foreground `world_state` と、同じ `wake / background_wake` cycle の `wake_observations` から反映された foreground `world_state` を使う。
現在入力だけから根拠なしに推測生成された `world_state` は再取得抑止に使わない。
定期起床では、強い `drive_state` が無く `visual_context / external_service / device` だけが見えている場合も、観測内容の具体性と現在の流れへの関係で `speech / noop / pending_intent` を選ぶ。
定期起床でも `visual_observations[].change_state=first_seen / changed` は判断材料にするが、それだけで `speech` を固定しない。`first_seen / changed` は短い自発 speech の価値を積極評価する材料である。
`current_input.sender=system` かつ `current_input.response_target=none` の定期起床では、visual observation を「ユーザーへの応答要求」として扱わず、ユーザー発話への相づちとして speech を始めない。
同一 dedupe への連続 speech だけを high suppression にする。
`background_wake` の `noop` 理由は、直近発話、緊急性の低さ、呼びかけ不在だけにしない。観測が反復であること、内容が空疎であること、またはユーザー向け応答が進行中であることを具体的に示す。
`wake / background_wake` cycle が `speech` になった場合、server は `assistant_message` event を `source_kind=wake / background_wake` で client へ送る。送信先 client は cycle の client context または 起床前観測 の `vision_source_id` から解決する。
grounded foreground の `world_state` が既にある場合、同じ情報を再取得する capability request より既存要約に基づく `speech / noop / pending_intent` 判断を優先する。
非ユーザー起点の判断では、判断前から存在する同じ state type の新鮮な foreground `world_state` がある status capability に `fresh_world_state_available=true` を付け、実 LLM の compact digest で request しなかった境界を確認する。
`wake / background_wake` では、同じ cycle の `wake_observations` で成功した `vision.capture` も `fresh_world_state_by_vision_source` として扱う。
`vision.capture` は `source_kind` に関係なく、`vision_source_id` が一致する新鮮な `visual_context` を `fresh_world_state_by_vision_source` として扱い、別 source の再観測は遮断しない。
定期起床でも ready / grounded foreground の `visual_observations` は判断材料にするが、直近 turn や `suppression_level` だけで `speech` も `noop` も固定しない。`first_seen / changed` の観測価値を重く評価する。
decision contract validation は、契約 shape、capability availability と権限、`fresh_world_state_available=true` の capability request、同じ `vision_source_id` の新鮮な `vision.capture` request、ユーザー入力への応答義務を repair 対象にする。initiative の selected candidate entry は `speech / noop / pending_intent` の repair 根拠にしない。`preferred_result_kind=capability_request` は追加観測の提案であり、`capability_request` 以外を repair 対象にしない。
重複再取得の抑制は判断文脈と decision contract validation に置き、capability dispatch 直前の専用停止境界は置かない。
ユーザーが明示的に再観測を依頼した場合の capability request には freshness による再取得抑制を適用しない。
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
