# `event_evidence` の LLM 圧縮

## 背景

現在の `event_evidence` は、選ばれた `event_id` を最大 1-3 件だけ読み、
`anchor / topic / decision_or_result / tone_or_note` をロジックで埋めている。

この方式は、初期段階では次の点で有利だった。

- 選択順と件数上限を deterministic に保てる
- `events` の広い再読を防ぎやすい
- recall の失敗要因を単純に保ちやすい

一方で、このまま本命実装にすると次の問題が出る。

- `anchor` が「2026-04-12 10:30 の判断」のような固定文へ寄りやすい
- `topic` が単なる短縮引用になりやすく、何が重要だったかが薄い
- `decision` event の意味が `reason=...` のような記号的表現へ潰れやすい
- `primary_intent` や、どの section からその event が選ばれたかを文面へ反映しにくい
- event kind ごとの分岐を手で増やすほど、repo 全体の LLM 判断優先方針に逆行する

そのため、`event_evidence` のうち **短い証拠表現への圧縮** だけを LLM へ分離する。

## 採用方針

OtomeKairo では、`event_evidence` 全体を LLM 任せにはしない。

- `event_evidence` を使うかどうかの判定はロジックで行う
- section 優先順と `event_id` 選定はロジックで行う
- `events` の読み込み件数上限はロジックで守る
- `event_id`、`kind`、最終的な並び順はコード側で固定する
- `anchor / topic / decision_or_result / tone_or_note` の自然文生成だけを LLM へ任せる

要するに、`どの event を証拠候補にするか` はコードが決め、`どう短く意味づけるか` だけを LLM に任せる。

## 目的

- `RecallPack.event_evidence` を、固定ラベルではなく判断に効く短い証拠表現へする
- `primary_intent`、`time_reference`、選定元 section を踏まえた強調点を自然に切り替えられるようにする
- event kind ごとの定型分岐を増やし続けないで済むようにする
- `event_evidence` の shape と `RecallPack` の責務を崩さない
- 失敗しても recall 本体を巻き込まず、inspection で追えるようにする

## 対象範囲

この設計の対象は次だけである。

- `RecallPack.event_evidence` の生成
- selected event ごとの source pack 構築
- LLM role と structured output 契約
- failure の閉じ込め方
- inspection / audit への露出

## 対象外

この設計では次は変えない。

- `event_evidence` を使うかどうかの判定
- `_select_event_evidence_ids()` の基本方針
- `EVENT_EVIDENCE_LIMIT=3` の件数上限
- `events` の広い再読や時系列再構成
- 正確引用を標準責務にすること
- `RecallPack` の section 構成
- `conflicts`、association rerank、pending-intent 選択

## 基本構成

`event_evidence` 生成は、次の 5 段に分ける。

1. 既存ロジックで selected `event_id` 群を決める
2. selected event を読み、各 event ごとに source pack を作る
3. `event_evidence_generation` role を **event 単位** で呼ぶ
4. 返ってきた slot payload を検証し、`event_id` と `kind` をコード側で付け直す
5. 失敗があっても recall cycle 自体は継続し、failure を inspection へ残す

ここで event 単位の呼び出しにする理由は次である。

- selected event は最大 3 件で、call 数が膨らまない
- 1 件だけ失敗しても他の `event_evidence` を残せる
- `event_id` と生成失敗の対応を inspection で追いやすい

## 追加する論理 role

この機能では、モデルプリセットに `event_evidence_generation` という論理 role を追加する。

この role の責務は次だけである。

- selected event 1 件分の source pack を読み、slot payload を返す

この role を `observation_interpretation` や `decision_generation` から分ける理由は次である。

- 入出力契約が小さく、責務が限定されている
- prompt を変えても通常会話の判断 prompt へ波及しにくい
- recall 用の短文化だけを個別に改善できる

ただし、実際の設定値として同じ model を指定してよい。

## source pack の設計

LLM に渡すのは raw `events` 全文ではなく、selected event 1 件ぶんの圧縮済み source pack とする。

最低限、次を含める。

```json
{
  "primary_intent": "commitment_check",
  "secondary_intents": ["reminisce"],
  "time_reference": "past",
  "selection_basis": {
    "retrieval_sections": ["active_commitments", "episodic_evidence"],
    "source_summaries": [
      "また体調の話の続きをしたい流れがある。",
      "前回の相談の続きとして様子を確認した。"
    ]
  },
  "event": {
    "kind": "decision",
    "role": "system",
    "created_at": "2026-04-12T10:30:00",
    "text": null,
    "result_kind": "reply",
    "external_result_kind": "reply",
    "reason_code": "follow_up_gently",
    "reason_summary": "結論を急がず、次も様子を見ながら話を続ける方針にした。",
    "pending_intent_summary": null
  }
}
```

入力の原則は次である。

- source pack は selected event 1 件だけを扱う
- `selection_basis.source_summaries` は、その event を指していた `episode / memory_unit` の `summary_text` を最大 2 件だけ入れる
- `retrieval_sections` は section 名だけを入れ、内部 ID は渡さない
- `event.text` は改行を畳み、必要なら長さを切り詰める
- `decision` event では `reason_summary` と `result_kind` を優先して渡す
- `reply` / `observation` event では `text` を主材料にし、不要なメタデータは増やさない
- `event_id`、`cycle_id`、`memory_set_id` は LLM へ渡さない

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "anchor": "前回の体調相談の続きの場面",
  "topic": "休み方と体調の様子見",
  "decision_or_result": "結論を急がず、次も確認しながら話を続ける流れになった",
  "tone_or_note": "慎重に様子を見る空気だった"
}
```

契約は次とする。

- 必須キーは `anchor / topic / decision_or_result / tone_or_note` の 4 つ
- 各値は `string | null`
- 4 slot のうち少なくとも 1 つは `null` ではない
- 文字列なら前後空白を除いて空でない
- 改行を含まない
- 1 文までに留める
- 内部識別子を含まない
- 生ログの長い逐語引用にしない
- source pack に無い事実を補わない

最終的な `RecallPack.event_evidence` では、`null` slot は落とし、コード側で次の shape に戻す。

```json
{
  "event_id": "event:...",
  "kind": "decision",
  "anchor": "前回の体調相談の続きの場面",
  "decision_or_result": "結論を急がず、次も確認しながら話を続ける流れになった"
}
```

## プロンプト方針

system prompt では、少なくとも次を明示する。

- あなたは `event_evidence` の短い証拠表現だけを作る
- slot に無い新しい意味カテゴリを増やさない
- `decision_or_result` は決定や結果があるときだけ書く
- `tone_or_note` は補助であり、主根拠の代わりにしない
- `primary_intent=commitment_check` では決定や継続性を優先しやすくする
- `primary_intent=reminisce` や `time_reference=past` では `anchor / topic` を残しやすくする
- event kind に無い情報は `null` にする
- 長い引用、言い直し、相槌をそのまま繰り返さない

user prompt では、recall 文脈と selected event の source pack をそのまま構造化で渡す。

## 処理フロー

実装時の flow は次とする。

1. 既存ロジックで `event_evidence` が必要か判定する
2. 既存ロジックで selected `event_id` 群を決める
3. store から selected event を順序付きで読む
4. 読み込めた各 event について source pack を構築する
5. `event_evidence_generation` role で slot payload を生成する
6. contract を検証し、`event_id` と `kind` をコード側で付ける
7. `null` slot を落として `RecallPack.event_evidence` へ積む
8. `selected_event_ids` は、圧縮成功件数とは独立に保持する
9. recall trace と retrieval run へ生成結果を記録する

## 失敗時の扱い

この設計では fallback 生成を入れない。
LLM が失敗したときに、古い `_event_evidence_anchor()` 系ロジックへ戻すことはしない。

代わりに、失敗は **event 単位** で閉じ込める。

- ある event の source pack 構築や LLM 生成が失敗しても、他 event の圧縮は続ける
- 失敗した event だけ `RecallPack.event_evidence` へ入れない
- selected `event_id` は trace に残す
- selected event が全件失敗した場合でも、無言で `event_evidence=[]` にするのではなく、failure を trace と audit event に残す
- `event_evidence` 生成失敗だけで recall cycle 全体を `internal_failure` にしない

`failure_stage` は少なくとも次を持つ。

- `load_event`
- `build_source_pack`
- `llm_generation`
- `contract_validation`

## 観測と監査

inspection で追いやすくするため、`cycle_trace.recall_trace` に `event_evidence_generation` を追加する。

最低限、次を持つ。

- `requested_event_count`
- `loaded_event_count`
- `succeeded_event_count`
- `failed_items`

`failed_items` の各要素は次を持つ。

- `event_id`
- `kind`
- `failure_stage`
- `failure_reason`

補助的に、`retrieval_runs.payload_json` にも件数要約を追加する。

- `event_evidence_generation.requested_event_count`
- `event_evidence_generation.succeeded_event_count`
- `event_evidence_generation.failed_count`

また、監査 event として `event_evidence_generation_failure` を追加する。
これは memory 系 audit event と同様に `events` テーブルへ残し、cycle 単位で追えるようにする。

ここで重要なのは、`selected_event_ids` と `event_evidence` 件数が一致しないケースを異常ではなく **可観測な部分失敗** として扱うことである。

## `03_想起と判断.md` との関係

`docs/design/memory/03_想起と判断.md` は、`event_evidence` の意味と圧縮ルールを定める上位設計である。

この文書は、その圧縮を LLM で行うための実装設計として次を具体化する。

- role
- source pack
- structured output 契約
- failure の閉じ込め方
- inspection と audit への出し方

この機能で実装上やることは、要するに次である。

- `_event_evidence_anchor()` などの定型文ロジックを本命として育てない
- selected `event_id` の決定と件数上限はコード側へ残す
- slot 生成だけを専用 role へ分離する
- 失敗を silent に捨てず、trace と audit へ出す

## この設計で守ること

- `event_evidence` は狭い証拠確認であり、event 全文再読の入口にしない
- LLM は `event_id` の選定や並び順を決めない
- 逐語引用や詳細時系列を標準責務にしない
- 失敗を fallback 文面でごまかさない
- `RecallPack` の shape と他 section の責務を崩さない
