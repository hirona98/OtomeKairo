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
| `trigger_kind` | `wake`、`background_wake`、`desktop_watch` などの起点 |
| `opportunity_summary` | なぜ今評価機会があるか |
| `drive_summaries` | 前景に出す `drive_state` 要約 |
| `pending_intent_summaries` | 再評価対象の保留意図要約 |
| `candidate_families` | `ongoing_action / pending_intent / autonomous` の候補系統ごとの availability と理由要約 |
| `selected_candidate_family` | その回で前景候補として最も強く立っている系統 |
| `world_state_summary` | 現在文脈として効く外界状態の要約 |
| `ongoing_action_summary` | 継続中の実行列がある場合の要約 |
| `capability_summary` | 使える能力と使えない能力の判断用要約 |
| `intervention_risk_summary` | 過剰介入、重複、タイミング不自然さの要約 |

`initiative_context` は inspection へ要約を残す。
`initiative_context` そのものを永続的な状態正本にしない。
`drive_summaries` は `drive_kind / support_count / support_strength / freshness_hint / scope_alignment / signal_strength / persona_alignment / stability_hint` を含みうる。
`candidate_families` は `preferred_result_kind` に加えて、必要なら `preferred_capability_id / preferred_capability_input` を持ってよい。

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
- 見送り理由または前進理由
- 過剰介入抑制に効いた要素
- 失敗理由

`input_trace.initiative_context` に要約を残し、`decision_trace` には最終判断に効いた initiative 要素だけを残す。

## やらないこと

次は採らない。

- 自律判断を通常判断と別の結果系にすること
- `pending_intent` がないと自発判断できない設計にすること
- `drive_state` だけで能力実行を確定すること
- `world_state` だけで人へ介入すること
- `risk_level` から initiative loop 専用の安全ポリシーを作ること
- 見送りを失敗として扱うこと
