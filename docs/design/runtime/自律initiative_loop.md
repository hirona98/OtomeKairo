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
| `initiative_entry_summary` | 外向きの自律判断へ進んだ入口理由 |
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
`initiative_entry_summary` は `entry_kind / entry_basis / reason_summary` を含む。
`entry_basis` は `activity_mode_transition / strong_interest / same_activity_detail_change / observation_only` のいずれかである。
`entry_kind=enter` は `entry_basis=activity_mode_transition / strong_interest` の場合だけ外向き判断へ進む入口があることを表す。
`entry_basis=same_activity_detail_change / observation_only` は同じ活動内の詳細変化または観測のみを表し、`entry_kind=skip` にする。
`drive_summaries` の各 entry は、生成時点に存在する `drive_kind / support_count / support_strength / freshness_hint / scope_alignment / signal_strength / persona_alignment / stability_hint` を含む。
`time_context_summary` は `current_time_text / part_of_day / time_band_summary` を含む。
`foreground_signal_summary` は `foreground_thinness / reason_summary / world_state_count` を含む。
`suppression_summary` は `suppression_level / background_trigger / same_dedupe_recently_replied` を含む。
`suppression_summary.reason_summary` は具体的な抑制理由がある場合に含む。
`suppression_summary.suppression_level` は `low / high` のいずれかである。
`candidate_families` の各 entry は `family / available / selected` を含む。
`candidate_families.reason_summary` は候補理由がある場合に含む。
`candidate_families.blocking_reason_summary` は blocking 理由がある場合に含む。
`candidate_families` は追加観測を提案する場合に限り、`preferred_result_kind=capability_request / preferred_result_reason_summary / preferred_capability_id / preferred_capability_input` を持つ。
`activity_context.current_activity` は現在活動の短期推定として扱う。
`activity_context.previous_activity` は直前活動の参照情報として扱う。
`activity_context.current_activity.actor` は活動主体を表す。`actor=user` はユーザー側の活動、`actor=self` は AI 本体の活動である。
activity の `label / target` は自然文として LLM へ渡す。
タイミング判断と結果選択は、activity を含む `initiative_context` 全体で行う。

## 候補の作り方

initiative loop は、候補を次の 3 系統に分ける。

- 継続系
  - 未失効の `ongoing_action` があり、結果待ちまたは次の 1 手が必要なもの
- 再評価系
  - due になった `pending_intent`
- 自発系
  - `drive_state`、または強い `entry_basis` を持つ `initiative_entry_summary.entry_kind=enter` と現在文脈が噛み合い、前へ出る理由があるもの

自発系の成立条件は、`drive_state`、`ongoing_action`、`pending_intent`、または強い `entry_basis` を持つ `initiative_entry_summary` と現在文脈の噛み合いである。
`visual_context`、視覚観測の `first_seen / changed`、直近会話だけでは自発系を成立させない。
`activity_context.previous_activity` から `activity_context.current_activity` への意味ある活動モード遷移は、`initiative_entry_check` の `entry_basis=activity_mode_transition` として enter 候補にする。
同じサービス内の別投稿、別検索結果、別動画、同じゲーム内の別画面は `entry_basis=same_activity_detail_change` として扱う。
同じ活動内の画面差分、ファイル変更、同じゲーム内の画面遷移は `entry_basis=same_activity_detail_change` に分類する。
`pending_intent` が空の場合も、`drive_state`、`ongoing_action`、または強い `entry_basis` を持つ `initiative_entry_summary.entry_kind=enter` があれば通常の判断入力へ進める。
中期の `drive_state` は、人格設定と記憶から継続的に成立する向きだけを対象にする。
AI 応答由来、`scope_duration=session`、その場限りの「控える」「見守る」は、直近文脈の材料として扱う。

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

外向き結果種別は通常判断と同じ枠組みに揃える。
外向き結果と内部結果の境界は [判断と行動.md](判断と行動.md) を正とする。

## LLM とコードの責務

LLM は次を担う。

- 観測、world_state、直近文脈から外向き自律判断の入口が成立するかを `enter / skip` で判断する
- `activity_context` の previous/current を読み、画面差分ではなく活動モード遷移として外向きに触れる自然さを判断する
- 現在文脈と `drive_state` の噛み合いを判断する
- どの候補が今自然かを判断する
- 前へ出る場合の理由を短く説明する
- 見送る場合の理由を短く説明する

コードは次を担う。

- wake の due 判定
- 起床前観測の取得、視覚記録、`world_state`、`activity_context` への反映
- `drive_state / ongoing_action / pending_intent / initiative_entry_summary` による外向き判断入口の制御
- 観測変化、直近発話済み観測、重複介入事実の補助文脈化
- 期限切れ候補の除外
- capability availability と権限の検証
- 1 サイクル 1 主結果の制約
- 現在文脈が薄い `wake / background_wake` で、低リスクの観測能力を先に当てる提案を組み立てる
- `pending_intent`、`ongoing_action`、`world_state` の状態遷移
- `activity_context` の前景要約を initiative context へ渡す
- inspection と audit への記録

LLM に実行権限、資格情報、配送先 client、秘密値を渡さない。
LLM の自由文をそのまま状態遷移へ使わない。
`wake / background_wake` の「定期起床」「wake」という入力文言は判断機会の説明として扱う。
身体状態は body context、body capability result、明示的な身体状態 source を根拠にする。
予定状態は schedule context、schedule capability result、明示的な予定 source を根拠にする。
`wake_policy.observations` は 定期起床 の判断前に enabled 項目だけを順番に取得する。
visual capture の source、result、保存、inspection の詳細は [../capability/視覚機能.md](../capability/視覚機能.md) を正とする。
起床前観測 の運用時刻は `wake_policy` と process-local runtime で扱い、成功結果は内部観測と `initiative_entry_check` の材料として扱う。
起床前観測 として同期取得する capability result は、`ongoing_action` 外の内部観測として扱う。
ユーザー向け応答サイクルが進行中の間、server は `background_wake` の自発発話判断を `noop` にする。
`background_wake` の観測中に `conversation_input` または `speech` が新しく増えた場合、server は観測前の直近会話 snapshot を使って発話せず、`noop` にする。
visual capture の変化は `first_seen / changed / stable / same_as_recent_speech` の `change_state` に正規化し、正規化規則は [../capability/視覚機能.md](../capability/視覚機能.md) を正とする。
`first_seen / changed` は新規性の前景シグナルとして扱う。
`first_seen / changed` 単体は外向き判断入口にしない。
`same_as_recent_speech / stable` は反復性の前景シグナルとして扱う。
活動遷移に触れる発話は、終わった・サボった・遊び始めたなどを断定せず、区切りや切り替えとして表現する。
`source_owner=self` の camera 視覚観測は OtomeKairo 自身の視覚根拠として扱う。
`source_owner=user_environment` の視覚観測、`world_state.visual_context`、`activity_context.actor=user` はユーザー側の状況として扱う。
この文脈から speech する場合、`speech_stance=comment_on_user_context` として、ユーザー側の状況へのコメントとして表現する。
`current_input.sender=system` かつ `current_input.response_target=none` の `wake / background_wake` では、speech 本文を観測、候補、現在文脈に根拠づける。
`wake / background_wake` の `noop` 理由は、観測、候補、進行中応答、重複介入境界のいずれかに根拠づける。
定期起床から dispatch した capability request の result は、source request の `source_current_input.response_target=none` を引き継ぐ。
この capability result は内部観測結果として扱い、外向き結果を `noop` として trace に残す。
直近自発 speech 後の新しい visual capture も観測内容の変化として渡す。
LLM は `initiative_entry_summary`、`activity_context`、`drive_state`、`world_state`、`response_target`、候補理由、抑制要約を合わせて `speech / noop / pending_intent` を選ぶ。
`drive_state`、`pending_intent`、進行中 action、強い `entry_basis` を持つ `initiative_entry_summary.entry_kind=enter` が無い場合、視覚変化だけでは通常の判断入力へ進まない。
同じ内容の反復は `same_as_recent_speech / stable` と同一 dedupe の直近介入で扱う。
複数 observation がある場合も、server は取得結果を整理したあとに 1 回だけ initiative 判断を行う。
system wake 起点で明示 source context が無い `visual_context / body / schedule / social_context / environment / location` 候補は、推測候補として正規化時に破棄する。

## 過剰介入抑制

initiative loop は、前へ出る理由と見送る理由を判断入力に含める。

少なくとも次を評価する。

- 同じ `dedupe_key` の直近介入
- 同じ話題での連続介入
- 直近で相手が休止や拒否を示した事実
- `ongoing_action` が結果待ちであること
- capability が unavailable であること
- `initiative_baseline` が低い人格設定であること

visual observation の `change_state=first_seen / changed` は自律判断の前景材料にする。
autonomous family の priority は、`drive_state`、現在文脈、前景世界状態、候補理由の強さで決める。
`foreground_thinness=thin`、`trigger_kind=background_wake`、`suppression_level=high` は、LLM が `speech / noop / pending_intent` を判断するための文脈事実として渡す。
同じ `dedupe_key` の直近介入は server の重複介入境界として扱う。
同じ `dedupe_key` の直近介入だけを `suppression_level=high` にする。
`ongoing_action.status=waiting_result` は LLM へ渡す判断材料として扱う。

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
`summary.json` には `real_llm_initiative_probe_case_results` と `real_llm_background_wake_probe_case_results` を compact digest として残し、case ごとの `trigger_kind / result_kind / selected_candidate_family / foreground_thinness / capability_id / wake_scheduler_active / turn_consolidation_status` を trace 全文なしで確認する。`preferred_result_kind` は capability 提案がある case だけで値を持つ。
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

定期起床（`background_wake`）制御 matrix は次の 4 件に固定する。

| case | 入力条件 | 期待する構造 |
| --- | --- | --- |
| `background-no-context-skip` | interval 初回起床で `drive_state / world_state / ongoing_action` が空 | 定期起床 cycle を作り、`initiative_context` なしの `decision.kind=noop` と `memory_trace=skipped` を残す |
| `background-weak-foreground-noop` | interval 初回起床で `visual_context` 系の薄い `world_state` だけがあり、`initiative_entry_check` が `skip` を返す | `initiative_context` なしの `decision.kind=noop`、`memory_trace=skipped` |
| `background-grounded-speech` | interval 初回起床で予定 `world_state` と整合する `drive_state` がある | `wake_scheduler_active=true`、`foreground_thinness=grounded`、`selected_candidate_family=autonomous`、`decision.kind=speech`、`memory_trace=succeeded` |
| `background-interval-not-due` | `last_wake_at` 相当の直後に長い interval を設定する | `wake_scheduler_active=true` を観測し、新しい定期起床 cycle を作らない |

matrix の共通判定境界は前述の `initiative_context`、LLM とコードの責務、過剰介入抑制に従う。
`visual_context` だけの前景は thin foreground として扱う。
`visual_context` だけの前景は、`initiative_entry_check.entry_kind=enter` かつ `entry_basis=activity_mode_transition / strong_interest` がある場合だけ通常の initiative 判断へ進む。
強い `drive_state` があり、対応する grounded foreground がない場合、追加観測を短い speech より優先する。
強い `drive_state` が特定の status family を要求する場合は、対応 state type の鮮度に応じて既存要約または capability を選ぶ。
鮮度判定は、判断前から存在した foreground `world_state` と、同じ `wake / background_wake` cycle の 起床前観測 から反映された foreground `world_state` を使う。
再取得抑止に使う `world_state` は、判断前の foreground `world_state` または同じ cycle の 起床前観測 から反映された foreground `world_state` に限定する。
`wake / background_wake` cycle が `speech` になった場合、server は `assistant_message` event を `source_kind=wake / background_wake` で client へ送る。
送信先 client は `assistant_message` を購読している client に限定する。
cycle の client context にある client が `assistant_message` を購読している場合はその client へ送る。
cycle の client context から決まらない場合、`assistant_message` を購読している接続中 client が 1 件だけのときだけその client へ送る。
decision contract validation の repair 対象は、契約 shape、capability availability と権限、`fresh_world_state_available=true` の capability request、同じ `vision_source_id` の新鮮な `vision.capture` request、ユーザー入力への応答義務に限定する。
`speech / noop / pending_intent` の妥当性は LLM decision と decision summary で追跡し、contract validation は契約・実行境界に閉じる。
`preferred_result_kind=capability_request` は追加観測の提案として扱う。
重複再取得の制御点は判断文脈と decision contract validation に限定する。
ユーザーが明示的に再観測を依頼した capability request は、新規観測 intent として扱う。
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
