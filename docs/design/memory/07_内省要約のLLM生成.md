# 内省要約の LLM 生成

## 位置づけ

この文書は `reflective consolidation` における `summary_text` 生成の完成形を定める。
現在地は [../../plan/01_現行計画.md](../../plan/01_現行計画.md) を正とする。
LLM 補助処理の共通境界、source pack、出力、failure、inspection の共通ルールは [../19_LLM補助契約共通.md](../19_LLM補助契約共通.md) を正とする。
背景で述べるロジック生成は、導入前の比較対象として扱う。

## 背景

導入前の `reflective consolidation` では、`summary` 系 `memory_units` を作るときの `summary_text` をロジックで組み立てていた。

この方式は、初期段階では次の点で有利だった。

- 壊れにくい
- trigger や state 遷移と切り分けやすい
- smoke や inspection で追いやすい

一方で、このまま本命実装にすると次の問題が出る。

- 文面が定型化しやすい
- `predicate` の増加に対して表現が育ちにくい
- `relationship` や `self` の微妙な変化を自然文へ落としにくい
- 後段の想起や返答でも、硬い `summary_text` がそのまま効きやすい

そのため、`reflective consolidation` のうち自然文の要約生成だけは、LLM へ分離する。

## 採用方針

OtomeKairo では、`reflective consolidation` 全体を LLM 任せにはしない。

- trigger 判定はロジックで行う
- evidence の収集と絞り込みはロジックで行う
- `summary` を作る下限判定はロジックで行う
- `confirmed` / `dormant` の状態遷移はロジックで行う
- `create / refine / reinforce / supersede` の action 解決はロジックで行う
- `summary_text` の自然文生成だけを LLM へ任せる

要するに、`何を保持するか` は構造化で管理し、`どう言語化するか` だけを LLM に任せる。

## 目的

- `summary_text` を定型文ではなく、証拠に沿った自然な要約へする
- `self / relationship / user / topic` の違いを無理な手書き分岐ではなく、文面で柔らかく表現できるようにする
- `predicate` や `qualifiers` が増えても、文面生成コードを増やし続けなくて済むようにする
- 既存の `memory_units` 契約と `reflective consolidation` の安定性を壊さない

## 対象範囲

この設計の対象は次だけである。

- `reflective consolidation` で生成する `memory_type=summary` の `summary_text`
- そのための evidence pack 構築
- LLM 入出力契約
- 失敗時の扱いと観測

## 対象外

この設計では次は変えない。

- `reflective consolidation` の trigger 条件
- `summary` を出す最小 evidence 条件
- `confirmed` / `dormant` の判定規則
- `events` の広い再読
- `memory_action` の解決規則
- `RecallPack` の section 構成

## 基本構成

`reflective consolidation` の summary 生成は、次の 3 段に分ける。

1. ロジックで scope ごとの evidence pack を作る
2. LLM に `summary_text` だけ生成させる
3. 返ってきた文面を使って、既存の action resolver で `summary` 系 action を決める

ここで重要なのは、LLM の出力をそのまま state 遷移へ使わないことである。

## 追加する論理役割

この機能では、モデルプリセットに `memory_reflection_summary` という論理 role を追加する。

この role の責務は次だけである。

- `reflective consolidation` 用 evidence pack を読んで `summary_text` を返す

この role を `memory_interpretation` と分ける理由は次である。

- turn ごとの記憶解釈と、長期要約では入出力契約が異なる
- background worker で別のモデル品質や token 上限を選べる
- prompt 改修が turn consolidation 側へ波及しにくい

ただし、実際の設定値として同じ model を両 role に指定する。

## `memory_postprocess_job` への追加情報

`memory_postprocess_job` は embedding 同期だけでなく、会話時点の設定 snapshot も保持する。
`summary_text` を LLM 生成するようにした後は、postprocess job に次も含める。

- `selected_model_preset_id`
- `roles.memory_reflection_summary` の snapshot

background worker は、会話時点の設定 snapshot を使って reflective summary を生成する。
実行時の current 設定を引き直してはいけない。

## evidence pack の設計

LLM に渡す入力は raw `events` ではなく、圧縮済みの `episodes` と `memory_units` を中心にした evidence pack とする。

最低限、次を含める。

```json
{
  "scope_type": "relationship",
  "scope_key": "self|user",
  "summary_status_candidate": "inferred",
  "dominant_memory_types": ["relation", "interpretation"],
  "evidence_counts": {
    "episodes": 3,
    "memory_units": 4,
    "support_cycles": 3,
    "open_loops": 1
  },
  "existing_summary_text": "最近のあなたとのやり取りでは、距離感に関する理解が少しずつ安定している。",
  "episodes": [
    {
      "formed_time_label": "2026年4月12日 10時30分（日本時間）",
      "summary_text": "前回の相談の続きと体調確認をした。",
      "outcome_text": "結論は保留で、次回も様子を見ることになった。",
      "open_loops": ["体調の変化をまた確認する"],
      "salience": 0.82
    }
  ],
  "memory_units": [
    {
      "memory_type": "relation",
      "predicate": "talk_again",
      "object_ref_or_value": "topic:health",
      "summary_text": "また体調の話の続きをしたい流れがある。",
      "status": "inferred",
      "confidence": 0.72,
      "salience": 0.61
    }
  ]
}
```

入力の原則は次である。

- `episodes` は新しい順で最大 6 件
- `memory_units` は重要度順で最大 8 件
- 各 item は全文ではなく、`summary_text` と構造化項目の要点だけに絞る
- `event_id` や `cycle_id` は LLM へ渡さない
- `formed_at` のような正本 timestamp は、生活文脈向けに整形した `formed_time_label` として渡す
- `existing_summary_text` は安定化のために渡す

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "summary_text": "最近のあなたとのやり取りでは、体調を気にかけながら無理のない進め方を探る流れが続いている。"
}
```

`summary_text` の契約は次とする。

- 必須
- 文字列
- 前後空白を除いて空でない
- 1 文から 2 文まで
- 140 文字以内
- 改行を含まない
- `event_id` や `memory_unit_id` のような内部識別子を含まない
- 逐語引用を主目的にしない
- 単発出来事ではなく、反復して見えている傾向として書く

## プロンプト方針

system prompt では、少なくとも次を明示する。

- あなたは `reflective consolidation` の summary 文面だけを書く
- 新しい事実を足さない
- 渡された evidence pack の外を推測で埋めない
- `summary_status_candidate=inferred` なら断定しすぎない
- `summary_status_candidate=confirmed` でも過剰に強い人格断定にしない
- `open_loops` は長期傾向に効くときだけ触れる
- 単発イベントの説明ではなく、継続パターンとして要約する

user prompt では、scope 情報、counts、既存 summary、episode 要点、memory_unit 要点をそのまま構造化で渡す。

## 処理フロー

実装時の flow は次とする。

1. 既存ロジックで trigger 判定をする
2. 既存ロジックで `episodes` と active `memory_units` を収集する
3. 既存ロジックで scope ごとに `summary` 候補を作るか判定する
4. 候補を作る scope について evidence pack を構築する
5. `memory_reflection_summary` role で LLM を呼び、`summary_text` を得る
6. 返ってきた `summary_text` を入れた candidate を `MemoryActionResolver` に渡す
7. 既存どおり `create / refine / reinforce / supersede` を解決する
8. 他の reflective 処理である `confirmed` 見直しと `dormant` 化を続ける
9. 結果を `reflection_runs` と `memory_trace` へ記録する

## 失敗時の扱い

この設計では fallback 文面生成を入れない。
LLM が失敗したときに、古いロジック生成へ戻すことはしない。

代わりに、失敗は次のように閉じ込める。

- ある scope の `summary_text` 生成だけが失敗したら、その scope の `summary` action だけを作らない
- 他 scope の summary 生成、`confirmed` 見直し、`dormant` 化は継続する
- reflective run 全体は、他の action が成立していれば `updated`、何も変化がなければ `no_change` とする
- store 書き込みや worker 自体の異常のように、run 全体が完了できない場合だけ `failed` とする

## 観測と監査

`reflection_runs.payload_json` には、少なくとも次を追加する。

- `summary_generation.requested_scope_count`
- `summary_generation.succeeded_scope_count`
- `summary_generation.failed_scopes`

`failed_scopes` の各要素は次を持つ。

- `scope_type`
- `scope_key`
- `failure_stage`
- `failure_reason`

inspection で現在地を追いやすくするため、`memory_trace.reflective_consolidation` にも summary generation の件数要約を持たせる。

監査 event は `reflective_summary_generation_failure` を新設し、scope 単位の失敗を追えるようにする。

この機能で実装上やることは、要するに次である。

- 導入前の `_reflective_summary_text()` 系の定型文ロジックを本命として育てない
- evidence pack、`summary_status`、`salience`、`confidence`、`MemoryActionResolver` は残す
- 文面だけを専用の LLM 生成へ分離する

## この設計で守ること

- `summary_text` は自然文にするが、記憶の正本はあくまで構造化項目である
- LLM は状態遷移を決めない
- 根拠の少なさを、文面の巧さでごまかさない
- 失敗しても deterministic な他処理まで巻き込んで止めない
